#!/usr/bin/env python3
"""提交前与工作区密钥检查器。

模式：
- 默认（无参）：扫描 Git 暂存区新增/修改行（用于 pre-commit 钩子）
- `--worktree`：扫描暂存区 + 未暂存改动 + 未跟踪新文件（用于开发期 `ws check` / `scripts/check`，无需提前 git add）
- `--all`：扫描全仓已跟踪文件与未跟踪非忽略文件

误报放行：在确认安全的示例行末尾添加 `secret-ok` 注释。
"""
from __future__ import annotations
import difflib
import os
import re
import subprocess
import sys

PATTERNS = [
    ("Tavily key", r"tvly-[A-Za-z0-9_-]{16,}"),
    ("Context7 key", r"ctx7sk-[A-Za-z0-9-]{16,}"),
    ("OpenAI/Anthropic 风格 key", r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}"),
    ("GitHub token", r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
    ("AWS access key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("Google API key", r"\bAIza[0-9A-Za-z_-]{30,}"),
    ("Slack token", r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    ("私钥内容", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("URL 参数含密钥", r"[?&](?:api[_-]?key|apikey|access[_-]?token|token|key)=[A-Za-z0-9_-]{16,}"),
    ("Authorization Bearer 头", r"(?i)authorization['\"]?\s*[:=]\s*['\"]?bearer\s+[A-Za-z0-9._-]{16,}"),
]
COMPILED = [(name, re.compile(p)) for name, p in PATTERNS]

GENERIC_ASSIGN_RE = re.compile(
    r"(?i)\b([\w-]*(?:api[_-]?key|secret|token|passwd|password|passphrase)[\w-]*)['\"]?\s*[:=]\s*['\"]([A-Za-z0-9_\-+/=.]{16,})['\"]"
)
NON_SECRET_VAR_SUFFIX_RE = re.compile(
    r"(?i)(?:url|uri|path|route|endpoint|header|name|field|key_name|env|type|schema|label|msg|message|err|error|prefix|suffix|ref|file|dir|version)s?$"
)
PLACEHOLDER_VAL_RE = re.compile(
    r"(?i)(?:example|placeholder|your[_-]|change[_-]?me|dummy|mock|test[_-]?key|todo|xxxx|00000000)"
)

# 配置类文件里不带引号的赋值（YAML / .env 模板 / Dockerfile / shell），如 `password: xxxx`、`export API_KEY=xxxx`。
# 只在这些文件里启用：源码中不带引号的右值多为变量名或函数调用，直接套用会大量误报。
CONFIG_FILE_RE = re.compile(
    r"(^|/)(?:[^/]+\.(?:ya?ml|toml|ini|cfg|conf|properties|tfvars|sh|bash|zsh|env)|\.env[^/]*|Dockerfile[^/]*|docker-compose[^/]*)$",
    re.IGNORECASE,
)
UNQUOTED_ASSIGN_RE = re.compile(
    r"(?i)^\s*(?:-\s+|export\s+|ENV\s+|ARG\s+)?([\w.-]*(?:api[_-]?key|secret|token|passwd|password|passphrase)[\w.-]*)"
    r"\s*[:=]\s*([A-Za-z0-9_\-+/=.]{16,})\s*(?:#.*)?$"
)
# 连接串 / 带凭证的 URL：scheme://user:password@host
CONN_URL_RE = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://([^\s:/@'\"]+):([^\s@/'\"]{6,})@[\w.\[-]")
WEAK_DEFAULT_PASSWORDS = {"password", "passwd", "secret", "changeme"}

SAFE_TEMPLATE_SUFFIXES = (".env.example", ".env.sample", ".env.template", ".env.test.example")
BAD_FILES = re.compile(
    r"(^|/)(\.env(\..*)?|.*(?:priv|private|key).*\.pem|.*\.p12|.*\.pfx|.*\.key|id_(rsa|ed25519|ecdsa)|\.mcp\.json|\.claude/settings\.local\.json|\.codex/config\.toml)$",
    re.IGNORECASE,
)
SKIP_FILES = re.compile(r"(^|/)(package-lock\.json|pnpm-lock\.yaml|yarn\.lock|check_secrets\.py|test_harness\.py)$")


def is_forbidden_filename(path: str) -> bool:
    if path.endswith(SAFE_TEMPLATE_SUFFIXES):
        return False
    return bool(BAD_FILES.search(path))


def is_false_positive_assignment(var_name: str, val: str) -> bool:
    if NON_SECRET_VAR_SUFFIX_RE.search(var_name):
        return True
    if val.startswith(("/", "./", "../", "http://", "https://", "ws://", "wss://")):
        return True
    # 全大写且带下划线分段的才当作“引用的环境变量名 / 常量名”豁免；
    # 不含下划线的纯大写字母数字串（不少平台的 SecretKey 就是 32 位大写十六进制）必须照常报出
    if re.fullmatch(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+", val):
        return True
    if re.fullmatch(r"X-[A-Za-z0-9-]+", val):
        return True
    if PLACEHOLDER_VAL_RE.search(val) or len(set(val)) <= 2:
        return True
    return False


def is_placeholder_credential(user: str, password: str) -> bool:
    """连接串里的口令是否只是变量插值、占位符或与用户名相同的本地默认值。"""
    if any(c in password for c in "${<*"):
        return True
    if PLACEHOLDER_VAL_RE.search(password):
        return True
    return password.lower() in WEAK_DEFAULT_PASSWORDS | {user.lower()}


def git(*args, cwd=None) -> bytes:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=True).stdout


def has_head(root: str) -> bool:
    res = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=root, capture_output=True)
    return res.returncode == 0


def text_lines(data: bytes) -> list[str] | None:
    if b"\0" in data[:8000]:
        return None
    return data.decode("utf-8", errors="replace").splitlines()


def parse_raw_diff_entries(raw: bytes):
    fields = raw.split(b"\0")
    i = 0
    while i < len(fields) and fields[i]:
        old_mode, new_mode, old_oid, new_oid, status = fields[i].split()
        i += 1
        path = fields[i]
        i += 1
        if status[:1] in (b"R", b"C"):
            path = fields[i]
            i += 1
        yield os.fsdecode(path), old_mode.lstrip(b":"), new_mode, old_oid, new_oid


def staged_changes(root: str):
    raw = git(
        "diff", "--cached", "--raw", "-z", "--no-abbrev", "--find-renames",
        "--no-ext-diff", "--no-textconv", "--diff-filter=ACMRT", cwd=root,
    )
    yield from parse_raw_diff_entries(raw)


def tracked_files(root: str):
    files = {}
    for entry in git("ls-files", "--stage", "-z", cwd=root).split(b"\0"):
        if not entry:
            continue
        metadata, _, path = entry.partition(b"\t")
        mode, _, _ = metadata.split()
        files[os.fsdecode(path)] = mode
    deleted = {
        os.fsdecode(path)
        for path in git("ls-files", "--deleted", "-z", cwd=root).split(b"\0") if path
    }
    return files, deleted


def untracked_files(root: str) -> list[str]:
    out = git("ls-files", "--others", "--exclude-standard", "-z", cwd=root)
    return [os.fsdecode(p) for p in out.split(b"\0") if p]


def mask(s: str) -> str:
    return s[:6] + "***" if len(s) > 6 else "***"


def scan_line(path: str, no: int, line: str, problems: list[str]):
    if "secret-ok" in line:
        return
    for name, rx in COMPILED:
        m = rx.search(line)
        if m:
            problems.append(f"{path}:{no}: {name}（{mask(m.group(0))}）")
            return
    cm = CONN_URL_RE.search(line)
    if cm and not is_placeholder_credential(cm.group(1), cm.group(2)):
        problems.append(f"{path}:{no}: 连接串内嵌明文口令（用户 `{cm.group(1)}`）")
        return
    gm = GENERIC_ASSIGN_RE.search(line)
    if not gm and CONFIG_FILE_RE.search(path):
        um = UNQUOTED_ASSIGN_RE.search(line)
        # 不带引号的值要求同时含字母与数字，排除 `token: my-service-name` 这类普通标识
        if um and re.search(r"\d", um.group(2)) and re.search(r"[A-Za-z]", um.group(2)):
            gm = um
    if gm:
        var_name, val = gm.group(1), gm.group(2)
        if not is_false_positive_assignment(var_name, val):
            problems.append(f"{path}:{no}: 疑似硬编码密钥赋值 `{var_name}`（{mask(val)}）")


def scan_diff_lines(path: str, old_lines: list[str], new_lines: list[str], problems: list[str]):
    for tag, _, _, first, end in difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False).get_opcodes():
        if tag in ("insert", "replace"):
            for no in range(first, end):
                scan_line(path, no + 1, new_lines[no], problems)


def scan_staged(root: str, problems: list[str]):
    blobs = {}

    def blob(oid, mode):
        if not oid.strip(b"0") or mode == b"160000":
            return b""
        if oid not in blobs:
            blobs[oid] = git("cat-file", "blob", oid.decode("ascii"), cwd=root)
        return blobs[oid]

    for path, old_mode, new_mode, old_oid, new_oid in staged_changes(root):
        if is_forbidden_filename(path):
            problems.append(f"{path}: 禁止提交敏感本机配置文件")
            continue
        if SKIP_FILES.search(path) or new_mode == b"160000":
            continue
        new = text_lines(blob(new_oid, new_mode))
        if new is None:
            continue
        old = text_lines(blob(old_oid, old_mode)) or []
        scan_diff_lines(path, old, new, problems)


def read_worktree_lines(root: str, rel_path: str) -> list[str] | None:
    full_path = os.path.join(root, rel_path)
    try:
        if os.path.islink(full_path):
            data = os.fsencode(os.readlink(full_path))
        elif os.path.isfile(full_path):
            with open(full_path, "rb") as f:
                data = f.read()
        else:
            return None
    except OSError:
        return None
    return text_lines(data)


def scan_worktree(root: str, problems: list[str]):
    """扫描暂存区 + 工作区未暂存修改 + 未跟踪新文件（无需 git add 即可拦截新建文件中的密钥）。"""
    seen_problems: list[str] = []
    scan_staged(root, seen_problems)

    # 扫描工作区已跟踪但有未暂存修改的文件（相对于 HEAD 或空基线）
    head_exists = has_head(root)
    if head_exists:
        changed_raw = git("diff", "HEAD", "--name-only", "-z", "--diff-filter=ACMRT", cwd=root)
        changed_paths = [os.fsdecode(p) for p in changed_raw.split(b"\0") if p]
    else:
        files_map, deleted = tracked_files(root)
        changed_paths = [p for p in files_map if p not in deleted]

    for path in changed_paths:
        if is_forbidden_filename(path):
            seen_problems.append(f"{path}: 禁止在仓库保留未忽略的敏感配置文件")
            continue
        if SKIP_FILES.search(path):
            continue
        new_lines = read_worktree_lines(root, path)
        if new_lines is None:
            continue
        old_lines: list[str] = []
        if head_exists:
            res = subprocess.run(["git", "show", f"HEAD:{path}"], cwd=root, capture_output=True)
            if res.returncode == 0:
                old_lines = text_lines(res.stdout) or []
        scan_diff_lines(path, old_lines, new_lines, seen_problems)

    # 扫描所有未跟踪（且未被 .gitignore 忽略）的新文件
    for path in untracked_files(root):
        if is_forbidden_filename(path):
            seen_problems.append(f"{path}: 未被 .gitignore 忽略的敏感配置文件")
            continue
        if SKIP_FILES.search(path):
            continue
        lines = read_worktree_lines(root, path)
        if lines is None:
            continue
        for no, line in enumerate(lines, 1):
            scan_line(path, no, line, seen_problems)

    # 保序去重
    dedup = set()
    for p in seen_problems:
        if p not in dedup:
            dedup.add(p)
            problems.append(p)


def scan_all_files(root: str, problems: list[str]):
    files, deleted = tracked_files(root)
    all_paths = [p for p, mode in files.items() if p not in deleted and mode != b"160000"]
    for u in untracked_files(root):
        if u not in files:
            all_paths.append(u)

    for path in all_paths:
        if is_forbidden_filename(path):
            problems.append(f"{path}: 禁止纳入版本控制的敏感本机配置文件")
            continue
        if SKIP_FILES.search(path):
            continue
        lines = read_worktree_lines(root, path)
        if lines is None:
            continue
        for no, line in enumerate(lines, 1):
            scan_line(path, no, line, problems)


def scan_commits_since_base(root: str, base_ref: str, problems: list[str]):
    """逐提交扫描 base_ref..HEAD 区间内引入的所有变更（防止先提交密钥再删除导致历史残留合入主干）。"""
    if not has_head(root):
        return
    revs_out = git("rev-list", "--reverse", f"{base_ref}..HEAD", cwd=root).decode("ascii", errors="replace").splitlines()
    if not revs_out:
        return
    blobs: dict[bytes, bytes] = {}

    def blob(oid: bytes, mode: bytes) -> bytes:
        if not oid.strip(b"0") or mode == b"160000":
            return b""
        if oid not in blobs:
            blobs[oid] = git("cat-file", "blob", oid.decode("ascii"), cwd=root)
        return blobs[oid]

    seen: set[str] = set()
    for sha in revs_out:
        short = sha[:8]
        raw = git(
            "diff-tree", "-r", "--root", "-m", "--first-parent", "--no-commit-id", "--raw", "-z",
            "--no-abbrev", "--find-renames", "--no-ext-diff", "--no-textconv", "--diff-filter=ACMRT",
            sha, cwd=root,
        )
        for path, old_mode, new_mode, old_oid, new_oid in parse_raw_diff_entries(raw):
            if is_forbidden_filename(path):
                msg = f"{path} (commit {short}): 历史提交包含敏感本机配置文件"
                if msg not in seen:
                    seen.add(msg)
                    problems.append(msg)
                continue
            if SKIP_FILES.search(path) or new_mode == b"160000":
                continue
            new = text_lines(blob(new_oid, new_mode))
            if new is None:
                continue
            old = text_lines(blob(old_oid, old_mode)) or []
            commit_probs: list[str] = []
            scan_diff_lines(f"{path}@{short}", old, new, commit_probs)
            for cp in commit_probs:
                if cp not in seen:
                    seen.add(cp)
                    problems.append(cp)


def main() -> int:
    scan_all = "--all" in sys.argv
    scan_wt = "--worktree" in sys.argv
    cwd = None
    base_ref = os.environ.get("WS_CHECK_BASE", "").strip()
    if "--cwd" in sys.argv:
        idx = sys.argv.index("--cwd")
        if idx + 1 < len(sys.argv):
            cwd = sys.argv[idx + 1]
    if "--base" in sys.argv:
        idx = sys.argv.index("--base")
        if idx + 1 < len(sys.argv):
            base_ref = sys.argv[idx + 1].strip()
    problems: list[str] = []
    try:
        root_out = git("rev-parse", "--show-toplevel", cwd=cwd)
        root = os.fsdecode(root_out.rstrip(b"\n"))
        if scan_all:
            scan_all_files(root, problems)
        elif scan_wt:
            scan_worktree(root, problems)
            if base_ref:
                scan_commits_since_base(root, base_ref, problems)
        else:
            scan_staged(root, problems)
            if base_ref:
                scan_commits_since_base(root, base_ref, problems)
    except (OSError, subprocess.CalledProcessError, ValueError, IndexError):
        problems.append("无法读取 Git 仓库或基线状态，密钥检查中止")

    if problems:
        print("密钥扫描未通过：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print("修复：将密钥移入环境变量或已忽略的本机配置；若确属安全示例误报，在行尾加 `secret-ok`。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
