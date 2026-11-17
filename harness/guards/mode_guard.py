#!/usr/bin/env python3
"""双模态阶段门禁（Mode Gate）：在 Mode: design 下拦截对业务代码目录的写入与自解锁。

三道防线（由事前到事后）：
- 事前拦截（PreToolUse）：文件工具按白名单硬拦；终端命令做词法分析，只拦能确定写入目标的写操作。
- 事后核对（PostToolUse / `--worktree`）：命令跑完后比对工作区实际改动，兜住事前看不穿的脚本、脚手架等写入。
- 提交兜底（`--git-staged`）：pre-commit 拒绝在 design 模式提交白名单外文件。

同时兼容三种运行协议：
1. Antigravity (agy) PreToolUse Hook（stdin 为 camelCase JSON: toolCall / cwd）
2. Claude Code PreToolUse / PostToolUse Hook（stdin 为 snake_case JSON: tool_name / tool_input / cwd）
3. Git pre-commit / CLI 模式：`python3 mode_guard.py --git-staged|--worktree [--cwd <repo>]`（兜底 Codex 与手工提交）
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import NamedTuple

MODE_RE = re.compile(
    r"^\s*(?:-\s*)?\*{0,2}Mode\*{0,2}\s*:\s*`?(design|build)`?",
    re.IGNORECASE | re.MULTILINE,
)

# Mode: design 下允许在项目仓库内修改的根文件与相对目录前缀
ALLOWED_DESIGN_ROOT_FILES = {
    "AGENTS.md",
    "CLAUDE.md",
    "PROJECT.md",
    "README.md",
    ".gitignore",
    ".env.example",
    "LICENSE",
}
ALLOWED_DESIGN_PREFIXES = (
    "design/",
    "sandbox/",
    "docs/",
    ".agents/",
    ".claude/",
)

# 严禁 AI 在 Mode: design 下自行篡改的门禁钩子配置
PROTECTED_HOOK_FILES = {
    ".agents/hooks.json",
    ".claude/settings.local.json",
    ".claude/settings.json",
}
PROTECTED_HOOK_PREFIXES = (
    ".githooks/",
)
# 事后核对时一并比对指纹的钩子文件（多数被 .gitignore 忽略，git status 看不到）
WATCHED_HOOK_FILES = sorted(PROTECTED_HOOK_FILES) + [".githooks/pre-commit", ".husky/pre-commit"]

# 允许零副作用临时探针与会话产物写入的系统目录前缀（仅当目标不在受管项目仓内部时豁免）
TEMP_PREFIXES = (
    "/tmp/",
    "/private/tmp/",
    "/var/folders/",
    "/private/var/folders/",
)

SHELL_SCAFFOLD_RE = re.compile(
    r"\b(?:npx\s+create-[\w@/-]+|(?:npm|pnpm|yarn|bun)\s+create\s+\S+|cargo\s+init)\b"
)
LOCAL_MODE_STATE_FILE = "ws-mode-state.json"

SHELL_HOOK_TAMPER_RE = re.compile(
    r"(?:\.agents/hooks\.json|\.claude/settings(?:\.local)?\.json|\.githooks\b|core\.hooksPath|ws-mode-state\.json|ws-lines\.json)"
)
FALLBACK_UNLOCK_HINT_RE = re.compile(
    r"""(?:\bws['"]?\s+['"]?(?:mode|line)\b|scripts/line['"]?\s+['"]?new\b|Mode\*{0,2}\s*:\s*`?build)""",
    re.IGNORECASE,
)
INLINE_MODE_BUILD_RE = re.compile(r"Mode\*{0,2}\s*:\s*`?build", re.IGNORECASE)

# 终端命令词法分析：按词元识别写入目标，不在引号内的文字里猜
SHELL_PUNCTUATION = "();<>|&\n"
HEREDOC_BODY_RE = re.compile(
    r"(<<-?\s*(['\"]?)([A-Za-z_]\w*)\2[^\n]*)\n.*?\n[ \t]*\3[ \t]*(?=\n|$)",
    re.DOTALL,
)
SHELL_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_]\w*=")
SHELL_PREFIX_WORDS = {"sudo", "command", "env", "time", "nohup", "exec", "then", "do", "else", "!", "{"}
WRITE_ALL_ARGS_CMDS = {"mkdir", "touch", "rm", "rmdir", "mv", "unlink", "tee"}
WRITE_LAST_ARG_CMDS = {"cp", "ln", "install", "rsync"}
INPLACE_EDIT_FLAG_RE = re.compile(r"^(?:-[A-Za-z]*i|--in-place)")
GIT_SKIP_HOOKS_FLAG_RE = re.compile(r"--no-verify|-[A-Za-z]*n[A-Za-z]*")

SNAPSHOT_DIR = Path(tempfile.gettempdir()) / "ws-mode-guard"
SNAPSHOT_MAX_AGE_SECONDS = 24 * 3600


class ToolCall(NamedTuple):
    kind: str = "other"  # 'file' | 'shell' | 'other'
    value: str = ""  # 目标文件路径或命令行
    new_texts: tuple = ()  # 本次写入的新文本片段
    full_content: str | None = None  # 整文件覆写时的全文
    replacements: tuple = ()  # (旧文本, 新文本, 是否全部替换)


def realpath_preserve_symlinks_inside(raw_path: Path) -> Path:
    """规范化路径（消除 .. 与系统顶层 /var->/private/var 别名），但不把项目内软链接解析跳出仓库。"""
    abs_str = os.path.abspath(str(raw_path.expanduser()))
    # 仅解析系统根级软链接别名（如 macOS 的 /tmp、/var）；同名但非软链接的目录（如 Linux 的 /var）保持原样
    for alias in ("/tmp", "/var"):
        if (abs_str == alias or abs_str.startswith(alias + "/")) and os.path.islink(alias):
            abs_str = os.path.realpath(alias) + abs_str[len(alias):]
            break
    return Path(abs_str)


def find_project_root(start_path: Path) -> Path | None:
    """向上查找项目根目录（不被子目录模块级 `src/<mod>/AGENTS.md` 劫持）。

    规则：在到达 `.git` 仓库边界前收集沿途目录；优先认领最外层（仓库根级）含 `.git` + `AGENTS.md` 的目录，
    其次认领显式声明了 `Mode:` 或 `HARNESS:START` 的最外层 `AGENTS.md` 所在目录。
    """
    cur = realpath_preserve_symlinks_inside(start_path)
    if cur.exists() and cur.is_file():
        cur = cur.parent

    candidates: list[Path] = []
    git_root: Path | None = None
    for d in [cur, *cur.parents]:
        if (d / "AGENTS.md").is_file():
            candidates.append(d)
        if (d / ".git").exists():
            git_root = d
            break

    if git_root and (git_root / "AGENTS.md").is_file():
        return git_root

    # 从外层向内层寻找包含项目状态（Mode: 或 HARNESS:START）的根 AGENTS.md
    for d in reversed(candidates):
        try:
            txt = (d / "AGENTS.md").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if MODE_RE.search(txt) or "HARNESS:START" in txt:
            return d

    return candidates[-1] if candidates else None


def mode_of_text(text: str) -> str:
    """从 AGENTS.md 全文判定模式；未接入宪章的普通文档按 build 处理。"""
    m = MODE_RE.search(text)
    if m:
        return m.group(1).lower()
    # 若已挂载双模态宪章但意外删丢了 Mode 行，按安全侧默认 design 拦截
    if "HARNESS:START" in text:
        return "design"
    return "build"


def resolve_git_dir(project_root: Path | None) -> Path | None:
    """解析项目或并行 git worktree 专属的 Git 目录（主仓为 `.git/`，工作树为 `.git/worktrees/<name>/`）。"""
    if not project_root:
        return None
    dot_git = project_root / ".git"
    if dot_git.is_dir():
        return realpath_preserve_symlinks_inside(dot_git)
    if dot_git.is_file():
        try:
            first = dot_git.read_text(encoding="utf-8", errors="replace").splitlines()[0].strip()
        except (OSError, IndexError):
            return None
        if first.lower().startswith("gitdir:"):
            raw = first.split(":", 1)[1].strip()
            target = Path(raw)
            if not target.is_absolute():
                target = project_root / target
            resolved = realpath_preserve_symlinks_inside(target)
            if resolved.is_dir():
                return resolved
    return None


def resolve_main_project_root(project_root: Path | None) -> Path | None:
    """若 project_root 是关联 git worktree，解析其所属的主仓库根目录；否则原样返回。"""
    if not project_root:
        return None
    dot_git = project_root / ".git"
    if dot_git.is_dir():
        return project_root
    git_dir = resolve_git_dir(project_root)
    if not git_dir:
        return project_root
    commondir_file = git_dir / "commondir"
    if commondir_file.is_file():
        try:
            rel = commondir_file.read_text(encoding="utf-8", errors="replace").splitlines()[0].strip()
            common_git = realpath_preserve_symlinks_inside((git_dir / rel) if not Path(rel).is_absolute() else Path(rel))
            if common_git.name == ".git" and (common_git.parent / "AGENTS.md").is_file():
                return common_git.parent
        except (OSError, IndexError):
            pass
    if git_dir.parent.name == "worktrees" and git_dir.parent.parent.name == ".git":
        cand = git_dir.parent.parent.parent
        if (cand / "AGENTS.md").is_file():
            return cand
    return project_root


def local_mode_state_path(project_root: Path | None) -> Path | None:
    git_dir = resolve_git_dir(project_root)
    return (git_dir / LOCAL_MODE_STATE_FILE) if git_dir else None


def read_local_mode_state(project_root: Path | None) -> dict | None:
    state_file = local_mode_state_path(project_root)
    if not state_file or not state_file.is_file():
        return None
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict) and str(data.get("mode", "")).lower() in ("design", "build"):
        return data
    return None


def read_agents_mode(project_root: Path | None) -> str:
    """仅读取项目根 AGENTS.md 中的 Mode 字段（忽略工作树本地覆盖）。"""
    if not project_root:
        return "build"
    agents_md = project_root / "AGENTS.md"
    if not agents_md.is_file():
        return "build"
    try:
        text = agents_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "build"
    return mode_of_text(text)


def read_project_mode(project_root: Path | None) -> str:
    """读取项目当前生效的 Mode：优先取当前工作树本地状态（ws-mode-state.json），否则读根 AGENTS.md。"""
    if not project_root or not (project_root / "AGENTS.md").is_file():
        return "build"
    local_state = read_local_mode_state(project_root)
    if local_state is not None:
        return str(local_state["mode"]).lower()
    return read_agents_mode(project_root)


def classify_design_path(rel: str) -> str:
    """按 Mode: design 的规则给仓内相对路径归类：'protected'（门禁钩子）| 'allowed'（设计产物）| 'code'（其余一律视为业务代码）。"""
    if rel in PROTECTED_HOOK_FILES or any(
        rel == pfx.rstrip("/") or rel.startswith(pfx) for pfx in PROTECTED_HOOK_PREFIXES
    ):
        return "protected"
    if rel in ALLOWED_DESIGN_ROOT_FILES or any(
        rel == pfx.rstrip("/") or rel.startswith(pfx) for pfx in ALLOWED_DESIGN_PREFIXES
    ):
        return "allowed"
    return "code"


def is_temp_or_artifact_path(lexical_path: Path) -> bool:
    s = str(lexical_path)
    if s.startswith(TEMP_PREFIXES):
        return True
    if "/brain/" in s and ("/scratch/" in s or "/.gemini" in s or "antigravity" in s):
        return True
    return False


def text_attempts_build_unlock(new_texts) -> bool:
    for t in new_texts:
        if not t:
            continue
        m = MODE_RE.search(t)
        if m and m.group(1).lower() == "build":
            return True
    return False


def simulate_edit(current: str, full_content: str | None, replacements) -> str | None:
    """推演文件工具执行后的全文；信息不足（如旧文本在文件中对不上）时返回 None。"""
    if full_content is not None:
        return full_content
    if not replacements:
        return None
    text = current
    for old, new, replace_all in replacements:
        if not old or old not in text:
            return None
        text = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    return text


def edit_unlocks_build(agents_md: Path, new_texts, full_content: str | None, replacements) -> bool:
    """按“改完之后的全文”判断是否会离开 design（只看补丁片段会漏掉最小改动与整文件覆写）。"""
    try:
        current = agents_md.read_text(encoding="utf-8", errors="replace")
    except OSError:
        current = ""
    after = simulate_edit(current, full_content, replacements)
    if after is not None and mode_of_text(after) != "design":
        return True
    # 无法推演全文时，退回检查新写入片段里是否出现 Mode: build
    return text_attempts_build_unlock(new_texts)


def locate_target(target_path: str, cwd: str) -> tuple[Path | None, str, Path]:
    """返回 (所属项目根, 相对项目根的词法路径, 规范化绝对路径)；目标不归任何受管项目管辖时项目根为 None。"""
    base_cwd = realpath_preserve_symlinks_inside(Path(cwd or os.getcwd()))
    raw_target = Path(target_path).expanduser()
    lexical_p = realpath_preserve_symlinks_inside(raw_target if raw_target.is_absolute() else base_cwd / raw_target)

    target_proj = find_project_root(lexical_p)

    # 仅当写入路径不属于任何受管项目仓内部时，才豁免系统临时目录 / brain 产物目录
    if target_proj is None and is_temp_or_artifact_path(lexical_p):
        return None, "", lexical_p

    proj_root = target_proj or find_project_root(base_cwd)
    if not proj_root:
        return None, "", lexical_p
    try:
        rel_lexical = lexical_p.relative_to(realpath_preserve_symlinks_inside(proj_root)).as_posix()
    except ValueError:
        # 写入在当前 cwd 项目之外，且目标自身不属于任何受管项目
        return None, "", lexical_p
    return proj_root, rel_lexical, lexical_p


def evaluate_file_write(
    target_path: str,
    cwd: str,
    new_texts=(),
    full_content: str | None = None,
    replacements=(),
) -> tuple[str, str]:
    """返回 (decision, reason)，decision 取值 'allow' | 'deny' | 'ask'。"""
    if not target_path:
        return "allow", ""

    if Path(target_path).name == LOCAL_MODE_STATE_FILE:
        return (
            "deny",
            f"[Mode Gate 已拦截] 禁止直接编辑门禁状态文件 `{LOCAL_MODE_STATE_FILE}`；"
            "请通过 `ws mode <design|build> --local` 切换。",
        )

    proj_root, rel_lexical, lexical_p = locate_target(target_path, cwd)
    if not proj_root:
        return "allow", ""

    # 防护：禁止在业务仓内顺着软链接直接覆写母仓共享技能资产
    resolved_real = Path(os.path.realpath(str(lexical_p)))
    proj_real = Path(os.path.realpath(str(proj_root)))
    try:
        resolved_real.relative_to(proj_real)
    except ValueError:
        return (
            "deny",
            f"[Mode Gate 已拦截] 路径 `{rel_lexical}` 通过软链接指向仓库外部共享资产（{resolved_real}），"
            "禁止在业务仓内直接覆写母仓文件；如需升级通用技能请切回母仓修改。",
        )

    if read_project_mode(proj_root) != "design":
        return "allow", ""

    kind = classify_design_path(rel_lexical)

    # Mode: design 下硬性保护钩子配置不被 AI 编辑篡改
    if kind == "protected":
        return (
            "deny",
            f"[Mode Gate 已拦截] 当前处于 `Mode: design`，禁止修改门禁钩子文件 `{rel_lexical}`。",
        )

    # Mode: design 下拦截 AI 擅自把根 AGENTS.md 改成 Mode: build（升级为用户确认）
    if rel_lexical == "AGENTS.md" and edit_unlocks_build(proj_root / "AGENTS.md", new_texts, full_content, replacements):
        return (
            "ask",
            "检测到本次修改会让 `AGENTS.md` 离开 `Mode: design`（解锁编码阶段）。仅在您已确认原型与本切片范围后批准。",
        )

    if kind == "allowed":
        return "allow", ""

    reason = (
        f"[Mode Gate 已拦截] 当前项目 ({proj_root.name}) 处于 `Mode: design` 阶段，"
        f"禁止创建、修改或删除业务代码文件 `{rel_lexical}`。\n"
        f"请先在 `design/` 下完成骨架（SYSTEM.md）、设计（DESIGN.md / prototype/）与切片规划；"
        f"获用户明确拍板后，通过 `ws mode build` 解锁编码阶段。"
    )
    return "deny", reason


def is_shell_operator(token: str) -> bool:
    return bool(token) and all(c in SHELL_PUNCTUATION for c in token)


def split_shell_commands(command_line: str) -> list[list[str]] | None:
    """把命令行切成若干条简单命令的词元表（丢弃 heredoc 正文与注释）；引号不配对等无法解析时返回 None。"""
    text = HEREDOC_BODY_RE.sub(lambda m: m.group(1), command_line).replace("\\\n", " ")
    lex = shlex.shlex(text, posix=True, punctuation_chars=SHELL_PUNCTUATION)
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        tokens = list(lex)
    except ValueError:
        return None

    commands: list[list[str]] = []
    cur: list[str] = []
    in_comment = False
    for tok in tokens:
        is_op = is_shell_operator(tok)
        ends_command = is_op and not ("<" in tok or ">" in tok)
        if in_comment:
            if not (is_op and "\n" in tok):
                continue
            in_comment = False
        elif not is_op and tok.startswith("#"):
            in_comment = True
            continue
        if ends_command:
            if cur:
                commands.append(cur)
                cur = []
        else:
            cur.append(tok)
    if cur:
        commands.append(cur)
    return commands


def git_subcommand(words: list[str]) -> tuple[str, list[str]]:
    """返回 (git 子命令, 其后的参数)，跳过 `-c k=v` / `-C dir` 等全局选项。"""
    i = 1
    while i < len(words):
        if words[i] in ("-c", "-C"):
            i += 2
        elif words[i].startswith("-"):
            i += 1
        else:
            return words[i], words[i + 1:]
    return "", []


def shell_command_targets(tokens: list[str], vcwd: str) -> tuple[list[str], list[str]]:
    """从一条简单命令里取出 (会被写入的路径表, 去掉重定向与前缀后的命令词表)。"""
    targets: list[str] = []
    words: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if is_shell_operator(tok):
            nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
            if words and words[-1].isdigit():
                words.pop()  # `2>` 这类文件描述符前缀
            if ">" in tok and nxt and not nxt.isdigit() and nxt != "-":
                targets.append(nxt)
            i += 2
            continue
        words.append(tok)
        i += 1

    while words and (SHELL_ENV_ASSIGN_RE.match(words[0]) or words[0] in SHELL_PREFIX_WORDS):
        words.pop(0)
    if not words:
        return targets, words

    name, args = os.path.basename(words[0]), words[1:]
    plain = [a for a in args if a and not a.startswith("-") and not a.isdigit()]
    if name in WRITE_ALL_ARGS_CMDS:
        targets += plain
    elif name in WRITE_LAST_ARG_CMDS and len(plain) >= 2:
        targets.append(plain[-1])
    elif name in ("sed", "perl") and any(INPLACE_EDIT_FLAG_RE.match(a) for a in args):
        # 原地编辑：脚本表达式不是路径，只认真实存在的文件参数
        targets += [a for a in plain if os.path.isfile(os.path.join(vcwd, os.path.expanduser(a)))]
    elif name == "git":
        sub, rest = git_subcommand(words)
        if sub in ("mv", "rm"):
            targets += [a for a in rest if a and not a.startswith("-")]
    return targets, words


def expand_shell_path(token: str, vcwd: str) -> str | None:
    """展开 $PWD 与 ~；含其他变量或命令替换的路径无法静态确定，返回 None 交给事后核对。"""
    for var in ("${PWD}", "$PWD"):
        if token.startswith(var):
            token = vcwd + token[len(var):]
    if "$" in token or "`" in token:
        return None
    return os.path.expanduser(token)


def unwrap_program_words(words: list[str]) -> list[str]:
    """剥离前导 python/python3 解释器包装，还原真实被调脚本及其参数。"""
    if not words:
        return []
    base = os.path.basename(words[0])
    if re.match(r"^python(?:\d+(?:\.\d+)*)?$", base):
        idx = 1
        while idx < len(words):
            opt = words[idx]
            if opt in ("-c", "-m"):
                return words
            if opt.startswith("-"):
                idx += 1
                continue
            return words[idx:]
    return words


def resolve_arg_project(raw_arg: str, vcwd: str, fallback_proj: Path | None) -> tuple[Path | None, bool]:
    """解析命令参数中的目标仓库根；返回 (proj_root, 是否含动态未展开变量)。"""
    if not raw_arg:
        return fallback_proj, False
    expanded = expand_shell_path(raw_arg, vcwd)
    if expanded is None:
        return fallback_proj, True
    target = Path(expanded if os.path.isabs(expanded) else os.path.normpath(os.path.join(vcwd, expanded)))
    return find_project_root(target), False


def inspect_ws_or_line_command(words: list[str], vcwd: str) -> tuple[str, str, bool]:
    """按词元检查单条命令是否通过 ws mode / ws line / scripts/line 解锁编码或跳过门禁。
    返回 (decision, reason, is_explicit_unlock)。
    """
    unwrapped = unwrap_program_words(words)
    if not unwrapped:
        return "allow", "", False

    prog = unwrapped[0]
    prog_base = os.path.basename(prog)
    rest = unwrapped[1:]
    vcwd_proj = find_project_root(Path(vcwd))
    main_proj = resolve_main_project_root(vcwd_proj)

    # 1) 项目自带 scripts/line new（下游仓包装脚本）
    if (prog in ("scripts/line", "./scripts/line") or prog.endswith("/scripts/line")) and rest[:1] == ["new"]:
        sub_args = rest[1:]
        if "-h" in sub_args or "--help" in sub_args:
            return "allow", "", False
        if (vcwd_proj and read_project_mode(vcwd_proj) == "design") or (
            main_proj and read_project_mode(main_proj) == "design"
        ):
            return (
                "ask",
                "检测到通过 `scripts/line new` 开启并行开发线解锁编码阶段。仅在您已拍板原型与当前切片范围后批准。",
                True,
            )
        return "allow", "", True

    if prog_base != "ws" or not rest:
        return "allow", "", False

    sub = rest[0]
    sub_args = rest[1:]
    if "-h" in sub_args or "--help" in sub_args:
        return "allow", "", False

    # 2) ws mode ...
    if sub == "mode":
        clear_local = False
        unknown_flag = False
        positionals: list[str] = []
        i = 0
        while i < len(sub_args):
            tok = sub_args[i]
            if tok == "--local":
                i += 1
            elif tok == "--clear-local":
                clear_local = True
                i += 1
            elif tok == "--slice":
                i += 2
            elif tok.startswith("--slice="):
                i += 1
            elif tok.startswith("-"):
                unknown_flag = True
                i += 1
            else:
                positionals.append(tok)
                i += 1

        proj_arg = (positionals[0] if clear_local and positionals else (positionals[1] if len(positionals) > 1 else ""))
        target_proj, dynamic_arg = resolve_arg_project(proj_arg, vcwd, vcwd_proj)
        target_in_design = bool(
            (target_proj and read_project_mode(target_proj) == "design")
            or (dynamic_arg and ((vcwd_proj and read_project_mode(vcwd_proj) == "design") or (main_proj and read_project_mode(main_proj) == "design")))
        )
        if unknown_flag and target_in_design:
            return (
                "ask",
                "检测到含未识别选项的 `ws mode` 命令，可能改变阶段门禁状态；请确认是否批准。",
                True,
            )
        target_mode = "" if clear_local else (positionals[0].lower() if positionals else "")
        unlocks = (not clear_local and target_mode == "build") or (
            clear_local and bool(target_proj and read_agents_mode(target_proj) == "build")
        )
        if unlocks:
            if target_in_design:
                return (
                    "ask",
                    "检测到通过终端命令切换 `Mode: build` 解锁编码阶段。仅在您已拍板原型与当前切片范围后批准。",
                    True,
                )
            return "allow", "", True
        return "allow", "", False

    # 3) ws line new / sync
    if sub == "line" and sub_args:
        line_sub = sub_args[0]
        l_args = sub_args[1:]
        if "-h" in l_args or "--help" in l_args:
            return "allow", "", False

        if line_sub == "new":
            eff_mode = "build"
            unknown_flag = False
            positionals = []
            i = 0
            while i < len(l_args):
                tok = l_args[i]
                if tok == "--mode" and i + 1 < len(l_args):
                    eff_mode = l_args[i + 1].lower()
                    i += 2
                elif tok.startswith("--mode="):
                    eff_mode = tok.split("=", 1)[1].lower()
                    i += 1
                elif tok == "--slice":
                    i += 2
                elif tok.startswith("--slice="):
                    i += 1
                elif tok == "--scope":
                    i += 1
                    while i < len(l_args) and not l_args[i].startswith("-"):
                        i += 1
                elif tok == "--allow-overlap":
                    i += 1
                elif tok.startswith("-"):
                    unknown_flag = True
                    i += 1
                else:
                    positionals.append(tok)
                    i += 1

            if unknown_flag or eff_mode not in ("design", "build"):
                eff_mode = "build"  # 未知或异常参数一律按保守侧 build 要求确认

            proj_arg = positionals[1] if len(positionals) > 1 else ""
            target_proj, dynamic_arg = resolve_arg_project(proj_arg, vcwd, vcwd_proj)
            target_main = resolve_main_project_root(target_proj)
            gated = bool(
                (target_proj and read_project_mode(target_proj) == "design")
                or (target_main and read_project_mode(target_main) == "design")
                or (dynamic_arg and ((vcwd_proj and read_project_mode(vcwd_proj) == "design") or (main_proj and read_project_mode(main_proj) == "design")))
            )
            if eff_mode == "build" and gated:
                return (
                    "ask",
                    "检测到通过 `ws line new` 开启 `Mode: build` 并行开发线解锁编码阶段。仅在您已拍板原型与当前切片范围后批准。",
                    True,
                )
            return "allow", "", eff_mode == "build"

        if line_sub == "sync" and any(a == "--skip-check" or a.startswith("--skip") for a in l_args):
            return (
                "ask",
                "检测到 `ws line sync --skip-check` 将跳过合流前门禁检查，请确认是否批准。",
                False,
            )

    return "allow", "", False


def command_has_explicit_unlock(command_line: str, cwd: str) -> bool:
    commands = split_shell_commands(command_line)
    if commands is None:
        return bool(FALLBACK_UNLOCK_HINT_RE.search(command_line))
    vcwd = cwd
    for tokens in commands:
        _, words = shell_command_targets(tokens, vcwd)
        _, _, explicit = inspect_ws_or_line_command(words, vcwd)
        if explicit:
            return True
        if words[:1] == ["cd"]:
            dest = expand_shell_path(words[1], vcwd) if len(words) > 1 else os.path.expanduser("~")
            if dest and dest != "-":
                vcwd = os.path.normpath(os.path.join(vcwd, dest))
    return False


def analyze_shell_writes(command_line: str, cwd: str) -> tuple[str, str]:
    """逐条简单命令核对钩子保护、阶段解锁与文件写入目标，支持跨工作树边界拦截并跟踪 `cd`。"""
    commands = split_shell_commands(command_line)
    base_proj = find_project_root(Path(cwd))
    base_main = resolve_main_project_root(base_proj)
    any_design_context = bool(
        (base_proj and read_project_mode(base_proj) == "design")
        or (base_main and read_project_mode(base_main) == "design")
    )

    if commands is None:
        # 引号不闭合等语法异常时，在涉及 design 仓库上下文时按保守侧兜底
        if any_design_context:
            if SHELL_HOOK_TAMPER_RE.search(command_line):
                return (
                    "deny",
                    "[Mode Gate 已拦截] 当前处于 `Mode: design`，禁止通过终端命令修改门禁钩子配置。",
                )
            if SHELL_SCAFFOLD_RE.search(command_line):
                return (
                    "deny",
                    "[Mode Gate 已拦截] 当前处于 `Mode: design`，禁止通过终端命令初始化脚手架。",
                )
            if FALLBACK_UNLOCK_HINT_RE.search(command_line):
                return (
                    "ask",
                    "检测到可能变更阶段门禁的终端命令。仅在您已拍板原型与当前切片范围后批准。",
                )
        return "allow", ""

    vcwd = cwd
    rewrites_agents_md = False
    pending_ask_reason = ""
    for tokens in commands:
        targets, words = shell_command_targets(tokens, vcwd)
        vcwd_proj = find_project_root(Path(vcwd))
        vcwd_in_design = bool(vcwd_proj and read_project_mode(vcwd_proj) == "design")
        joined_tokens = " ".join(tokens)

        # 1) 钩子与状态文件保护（含跨工作树指向 design 仓的钩子/ws-mode-state.json）
        if SHELL_HOOK_TAMPER_RE.search(joined_tokens):
            if vcwd_in_design:
                return (
                    "deny",
                    "[Mode Gate 已拦截] 当前处于 `Mode: design`，禁止通过终端命令修改门禁钩子配置。",
                )
            for tok in tokens:
                if not SHELL_HOOK_TAMPER_RE.search(tok):
                    continue
                exp = expand_shell_path(tok, vcwd)
                t_proj = find_project_root(Path(os.path.normpath(os.path.join(vcwd, exp)))) if exp else base_main
                if t_proj and (read_project_mode(t_proj) == "design" or read_agents_mode(t_proj) == "design"):
                    return (
                        "deny",
                        "[Mode Gate 已拦截] 禁止跨工作树篡改受 `Mode: design` 保护的门禁或状态文件。",
                    )

        # 2) ws mode / ws line / scripts/line 词元级解锁与跳检判定
        ws_dec, ws_reason, _ = inspect_ws_or_line_command(words, vcwd)
        if ws_dec == "deny":
            return ws_dec, ws_reason
        if ws_dec == "ask" and not pending_ask_reason:
            pending_ask_reason = ws_reason

        # 3) 内联脚本改写 Mode: build 兜底与脚手架拦截（当前目录处于 design 时）
        if vcwd_in_design:
            if not pending_ask_reason and INLINE_MODE_BUILD_RE.search(joined_tokens) and "AGENTS.md" in joined_tokens:
                pending_ask_reason = "检测到终端命令可能将 `AGENTS.md` 修改为 `Mode: build` 解锁编码阶段。仅在您已拍板后批准。"
            if SHELL_SCAFFOLD_RE.search(" ".join(words)):
                return (
                    "deny",
                    f"[Mode Gate 已拦截] 当前项目 ({vcwd_proj.name}) 处于 `Mode: design` 阶段，"
                    "禁止通过终端命令初始化脚手架。请先完成设计并经用户拍板解锁 `Mode: build`。",
                )

        git_sub, git_args = git_subcommand(words) if words[:1] == ["git"] else ("", [])
        if vcwd_in_design and git_sub == "commit" and any(GIT_SKIP_HOOKS_FLAG_RE.fullmatch(w) for w in git_args):
            return (
                "deny",
                "[Mode Gate 已拦截] 当前处于 `Mode: design`，禁止用 `--no-verify` 跳过提交前门禁钩子。",
            )

        for raw in targets:
            path = expand_shell_path(raw, vcwd)
            if path is None:
                continue
            decision, reason = evaluate_file_write(path, vcwd)
            if decision == "deny":
                return decision, reason
            proj_root, rel, _ = locate_target(path, vcwd)
            if proj_root and rel == "AGENTS.md" and read_project_mode(proj_root) == "design":
                rewrites_agents_md = True

        if words[:1] == ["cd"]:
            dest = expand_shell_path(words[1], vcwd) if len(words) > 1 else os.path.expanduser("~")
            if dest and dest != "-":
                vcwd = os.path.normpath(os.path.join(vcwd, dest))

    if pending_ask_reason:
        return "ask", pending_ask_reason
    if rewrites_agents_md:
        return (
            "ask",
            "检测到终端命令将直接改写 `AGENTS.md`，可能改动 `Mode` 解锁编码阶段。仅在您已拍板原型与当前切片范围后批准。",
        )
    return "allow", ""


def evaluate_shell_command(command_line: str, cwd: str) -> tuple[str, str]:
    """防止在 Mode: design（或从 build 子工作树跨目录触碰 design 主仓）下篡改钩子、私自开线或写业务代码。"""
    if not command_line:
        return "allow", ""
    base_cwd = realpath_preserve_symlinks_inside(Path(cwd or os.getcwd()))
    return analyze_shell_writes(command_line, str(base_cwd))


def file_fingerprint(path: Path) -> str:
    try:
        st = os.lstat(path)
    except OSError:
        return "absent"
    return f"{st.st_size}:{st.st_mtime_ns}"


def dirty_code_paths(proj_root: Path) -> list[str] | None:
    """工作区里相对 HEAD 有改动（含未跟踪、已删除）且不在 design 白名单内的路径；非 Git 仓或 git 不可用时返回 None。"""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            cwd=str(proj_root),
            capture_output=True,
            check=True,
            timeout=8,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None

    paths: list[str] = []
    fields = out.split(b"\0")
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if len(entry) < 4:
            continue
        paths.append(os.fsdecode(entry[3:]))
        if entry[:1] in (b"R", b"C") and i < len(fields):
            if entry[:1] == b"R":
                paths.append(os.fsdecode(fields[i]))  # 重命名的原路径等同于被删除
            i += 1
    return sorted({p for p in paths if classify_design_path(p) == "code"})


def guarded_worktree_state(proj_root: Path) -> dict[str, str] | None:
    """事后核对用的指纹表：白名单外的改动文件 + 门禁钩子文件 + 本地模式覆盖文件。"""
    dirty = dirty_code_paths(proj_root)
    if dirty is None:
        return None
    state = {rel: file_fingerprint(proj_root / rel) for rel in [*dirty, *WATCHED_HOOK_FILES]}
    local_state = local_mode_state_path(proj_root)
    if local_state is not None:
        state[f"<git-dir>/{LOCAL_MODE_STATE_FILE}"] = file_fingerprint(local_state)
    return state


def snapshot_file(proj_root: Path, tool_use_id: str) -> Path:
    key = hashlib.sha1((tool_use_id or f"cwd:{proj_root}").encode("utf-8")).hexdigest()[:20]
    return SNAPSHOT_DIR / f"{key}.json"


def save_pre_snapshot(cwd: str, tool_use_id: str):
    """终端命令执行前记下工作区指纹，供事后核对只认“这条命令新造成的改动”（不把人手留下的改动算到 AI 头上）。"""
    proj_root = find_project_root(realpath_preserve_symlinks_inside(Path(cwd or os.getcwd())))
    if not proj_root or read_project_mode(proj_root) != "design":
        return
    state = guarded_worktree_state(proj_root)
    if state is None:
        return
    try:
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - SNAPSHOT_MAX_AGE_SECONDS
        for stale in SNAPSHOT_DIR.glob("*.json"):
            if stale.stat().st_mtime < cutoff:
                stale.unlink()
        snapshot_file(proj_root, tool_use_id).write_text(
            json.dumps({"project": str(proj_root), "state": state}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        return


def evaluate_post_shell(command_line: str, cwd: str, tool_use_id: str) -> str:
    """终端命令执行后的事后核对；返回需要反馈给 AI 的说明，无异常返回空串。"""
    cwd_proj = find_project_root(realpath_preserve_symlinks_inside(Path(cwd or os.getcwd())))
    snap = snapshot_file(cwd_proj or Path("/"), tool_use_id)
    try:
        saved = json.loads(snap.read_text(encoding="utf-8"))
        snap.unlink()
        proj_root, before = Path(saved["project"]), saved["state"]
    except (OSError, ValueError, KeyError, TypeError):
        return ""  # 命令执行前不在 design 模式（未留快照），无需核对

    if read_project_mode(proj_root) != "design":
        if command_has_explicit_unlock(command_line, str(proj_root)):
            return ""  # 已在事前经用户确认的解锁
        return (
            f"[Mode Gate 事后核对] 刚才的终端命令让项目 ({proj_root.name}) 离开了 `Mode: design`，"
            "而解锁编码阶段必须由用户拍板。\n"
            "请立即把 `Mode` 恢复为 `design`，停止后续编码动作，并向用户说明情况；"
            "用户确认原型与切片范围后再通过 `ws mode build` 解锁。"
        )

    after = guarded_worktree_state(proj_root)
    if after is None:
        return ""
    changed = sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))
    if not changed:
        return ""
    listing = "\n".join(f"  - {p}" for p in changed[:20])
    more = f"\n  （另有 {len(changed) - 20} 个未列出）" if len(changed) > 20 else ""
    return (
        f"[Mode Gate 事后核对] 当前项目 ({proj_root.name}) 处于 `Mode: design`，"
        f"但刚才的终端命令改动了 `design/` 白名单之外的文件：\n{listing}{more}\n"
        "请立即停止在这些文件上继续操作，并向用户说明情况，由用户决定："
        "撤销这些改动，或在确认原型与切片范围后运行 `ws mode build` 解锁。不要自行删除或还原，以免误伤用户手上的改动。"
    )


def run_git_staged_check(cwd: str) -> int:
    proj_root = find_project_root(Path(cwd))
    if not proj_root or read_project_mode(proj_root) != "design":
        return 0
    try:
        out = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "-z", "--diff-filter=ACMRDT"],
            cwd=str(proj_root),
            capture_output=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return 0

    blocked = []
    # 用 -z 取原始路径：默认输出会把中文等非 ASCII 文件名转义加引号，导致 design/ 下的文件被误判
    for rel in (os.fsdecode(raw) for raw in out.split(b"\0") if raw):
        decision, _ = evaluate_file_write(str(proj_root / rel), str(proj_root))
        if decision == "deny":
            blocked.append(rel)

    if blocked:
        print("阶段门禁拦截提交：当前处于 `Mode: design`，暂存区包含受保护的非 design/ 文件：", file=sys.stderr)
        for f in blocked:
            print(f"  - {f}", file=sys.stderr)
        print("处理：撤销业务代码改动，或在用户拍板原型与切片后运行 `ws mode build` 解锁。", file=sys.stderr)
        return 1
    return 0


def run_worktree_check(cwd: str) -> int:
    """`--worktree`：不依赖任何工具钩子的事后核对，列出 design 模式下工作区里白名单外的全部改动。"""
    proj_root = find_project_root(Path(cwd))
    if not proj_root or read_project_mode(proj_root) != "design":
        return 0
    dirty = dirty_code_paths(proj_root)
    if not dirty:
        return 0
    print("阶段门禁核对未通过：当前处于 `Mode: design`，工作区存在 design/ 白名单之外的改动：", file=sys.stderr)
    for f in dirty:
        print(f"  - {f}", file=sys.stderr)
    print("处理：撤销这些改动，或在用户拍板原型与切片后运行 `ws mode build` 解锁。", file=sys.stderr)
    return 1


def parse_agy_call(payload: dict) -> ToolCall:
    tc = payload.get("toolCall") or {}
    name = (tc.get("name") or "").lower()
    args = tc.get("args") or {}
    if name in ("write_file", "edit_file", "create_file", "replace_file_content", "multi_replace_file_content"):
        target = (
            args.get("Path")
            or args.get("path")
            or args.get("TargetFile")
            or args.get("target_file")
            or args.get("file_path")
            or ""
        )
        texts: list[str] = []
        for k in ("Content", "content", "Replacement", "replacement", "ReplacementContent"):
            if isinstance(args.get(k), str):
                texts.append(args[k])
        replacements: list[tuple[str, str, bool]] = []
        old = next((args[k] for k in ("TargetContent", "Target", "target") if isinstance(args.get(k), str)), None)
        new = next(
            (args[k] for k in ("ReplacementContent", "Replacement", "replacement") if isinstance(args.get(k), str)),
            None,
        )
        if old is not None and new is not None:
            # agy 的 edit_file 默认替换全部匹配项；replace_file_content 则由 AllowMultiple 控制
            replace_all = True if name == "edit_file" else bool(args.get("AllowMultiple"))
            replacements.append((old, new, replace_all))
        if isinstance(args.get("ReplacementChunks"), list):
            for ch in args["ReplacementChunks"]:
                if isinstance(ch, dict) and isinstance(ch.get("ReplacementContent"), str):
                    texts.append(ch["ReplacementContent"])
                    if isinstance(ch.get("TargetContent"), str):
                        replacements.append((ch["TargetContent"], ch["ReplacementContent"], bool(ch.get("AllowMultiple"))))
        full = next((args[k] for k in ("Content", "content") if isinstance(args.get(k), str)), None)
        if name not in ("write_file", "create_file") or bool(args.get("Append") or args.get("append")):
            full = None
        return ToolCall("file", target, tuple(texts), full, tuple(replacements))
    if name in ("run_command", "bash"):
        cmd = args.get("CommandLine") or args.get("command") or ""
        return ToolCall("shell", cmd)
    return ToolCall()


def parse_claude_call(payload: dict) -> ToolCall:
    name = payload.get("tool_name") or ""
    ti = payload.get("tool_input") or {}
    if name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        target = ti.get("file_path") or ti.get("notebook_path") or ti.get("path") or ""
        texts: list[str] = []
        for k in ("content", "new_string", "new_source"):
            if isinstance(ti.get(k), str):
                texts.append(ti[k])
        replacements: list[tuple[str, str, bool]] = []
        if isinstance(ti.get("old_string"), str) and isinstance(ti.get("new_string"), str):
            replacements.append((ti["old_string"], ti["new_string"], bool(ti.get("replace_all"))))
        if isinstance(ti.get("edits"), list):
            for ed in ti["edits"]:
                if isinstance(ed, dict) and isinstance(ed.get("new_string"), str):
                    texts.append(ed["new_string"])
                    if isinstance(ed.get("old_string"), str):
                        replacements.append((ed["old_string"], ed["new_string"], bool(ed.get("replace_all"))))
        full = ti["content"] if name == "Write" and isinstance(ti.get("content"), str) else None
        return ToolCall("file", target, tuple(texts), full, tuple(replacements))
    if name == "Bash":
        return ToolCall("shell", ti.get("command") or "")
    return ToolCall()


def evaluate_call(call: ToolCall, cwd: str) -> tuple[str, str]:
    if call.kind == "file":
        return evaluate_file_write(call.value, cwd, call.new_texts, call.full_content, call.replacements)
    if call.kind == "shell":
        return evaluate_shell_command(call.value, cwd)
    return "allow", ""


def main() -> int:
    if "--git-staged" in sys.argv or "--worktree" in sys.argv:
        cwd = os.getcwd()
        if "--cwd" in sys.argv:
            idx = sys.argv.index("--cwd")
            if idx + 1 < len(sys.argv):
                cwd = sys.argv[idx + 1]
        return run_git_staged_check(cwd) if "--git-staged" in sys.argv else run_worktree_check(cwd)

    raw = sys.stdin.read().strip()
    if not raw:
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0

    cwd = payload.get("cwd") or os.getcwd()

    # 1. Antigravity (agy) PreToolUse 协议
    if "toolCall" in payload:
        decision, reason = evaluate_call(parse_agy_call(payload), cwd)

        if decision == "allow":
            print(json.dumps({"decision": "allow"}))
        elif decision == "ask":
            print(json.dumps({"decision": "force_ask", "reason": reason}, ensure_ascii=False))
        else:
            print(json.dumps({"decision": "deny", "reason": reason}, ensure_ascii=False))
        return 0

    # 2. Claude Code PreToolUse / PostToolUse 协议
    event = payload.get("hook_event_name")
    if "tool_name" in payload or event in ("PreToolUse", "PostToolUse"):
        call = parse_claude_call(payload)
        tool_use_id = str(payload.get("tool_use_id") or "")

        if event == "PostToolUse":
            feedback = evaluate_post_shell(call.value, cwd, tool_use_id) if call.kind == "shell" else ""
            print(json.dumps({"decision": "block", "reason": feedback} if feedback else {}, ensure_ascii=False))
            return 0

        decision, reason = evaluate_call(call, cwd)
        if call.kind == "shell" and decision != "deny":
            save_pre_snapshot(cwd, tool_use_id)

        if decision == "allow":
            print(json.dumps({}))
        else:
            print(
                json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "ask" if decision == "ask" else "deny",
                            "permissionDecisionReason": reason,
                        }
                    },
                    ensure_ascii=False,
                )
            )
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
