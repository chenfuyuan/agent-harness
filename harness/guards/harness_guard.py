#!/usr/bin/env python3
"""统一代码园丁门禁引擎（Harness Guard / Code Gardener）。

用法：
  `python3 harness_guard.py [项目根目录] [--diff] [--base <ref>] [--audit-json]`

核心确定性检查（全部基于 Python 标准库，零外部依赖，兼容 Python 3.9+）：
1. 声明式架构与反模式规则（项目根可选 `.harness-rules.json`，语法或 schema 非法时 fail-closed 拦截）：
   - `public_entries`：模块唯一公开入口边界（与模块 `AGENTS.md` 声明兼容，规则文件优先）；
   - `layer_bans`：目录单向分层依赖禁令（Python AST 解析 + JS/TS 词法级导入解析）；
   - `pattern_bans`：高危 API / 反模式一刀切封禁（支持 `paths` / `allow_in` 白名单目录，默认跳过纯注释行）。
   所有声明式规则支持行内显式豁免 `rule-ok(<rule-id>: <具体原因>)`。
2. 裸类型/Lint 消音拦截（全仓零容忍）：
   - Python：拦裸 `# type: ignore` / `# pyright: ignore`（缺 `[code]`）、裸 `# noqa` / `# ruff: noqa`（缺 `: CODE`）；
   - JS/TS：拦 `@ts-ignore`、`@ts-nocheck`、无说明 `@ts-expect-error`、裸 `eslint-disable*`（缺具体规则名）；
   放行带具体错误码/规则名的定向抑制，或带原因的 `ignore-ok(<原因>)` / `ignore-ok: <原因>`。
3. 反偷懒占位与静默吞错拦截：
   - 未备案 `TODO/FIXME/HACK/XXX`、`MOCK_DATA/FAKE_DATA/DUMMY_DATA`、`raise NotImplementedError`（非 `@abstractmethod`）、
     `todo!()` / `unimplemented!()`、独立 `placeholder` 注释，除非标 `stub-ok(<具体原因或切片号>)`；
   - 单行与跨行 Python 空 `except ...: pass/...` 及 JS/TS 空/纯注释 `catch {}`，除非标 `ignore-ok`。
4. 过期占位债硬拦：若 `stub-ok(slice-NN)` 指向的 `design/slices/NN-*.md` 状态已为 `verified`/`used`/`done`，直接报错拦截。
5. 模块就近契约瘦身预算：守护子目录 `AGENTS.md` 非空行硬上限 25 行（建议 ≤20 行，简单模块可不建）。
6. `--audit-json`：输出 Tier 3 规则数、豁免债账本与过期占位清单，供 `ws learn` 透明审计。
"""
from __future__ import annotations
import ast
import difflib
import io
import json
import os
import re
import subprocess
import sys
import tokenize
from pathlib import Path

NON_CODE_TOP_DIRS = {
    "design", "docs", "sandbox", "scripts", "examples", "vendor", "third_party", "target", "tmp",
}
SKIP_DIRS = {
    "node_modules", ".git", ".next", "dist", "build", "out",
    "coverage", "__pycache__", ".venv", "venv", "tests", "__tests__",
    "test", "e2e", "fixtures", "__mocks__",
}
TEST_FILE_RE = re.compile(r"(\.(test|spec)\.[A-Za-z0-9]+$|^test_.*|.*_test\.(py|go)$)")
CODE_EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".go", ".rs"}
JS_EXTS = {".js", ".jsx", ".ts", ".tsx", ".mjs"}

RULES_FILENAME = ".harness-rules.json"
ALLOWED_RULES_TOP_KEYS = {"ignore_paths", "public_entries", "layer_bans", "pattern_bans"}

MAX_MODULE_AGENTS_NONEMPTY_LINES = 25

STUB_RE = re.compile(
    r"(?://|#|\*)\s*(TODO|FIXME|HACK|XXX)\b"
    r"|\b(MOCK_DATA|FAKE_DATA|DUMMY_DATA)\b"
    r"|\b(raise\s+NotImplementedError|(?:todo|unimplemented)!\s*\()"
    r"|(?i:\b(?:throw|panic|raise)\b[^\n]*?\b(not\s+(?:yet\s+)?implemented)\b)"
    r"|(?i:(?://|#)\s*(placeholder)\s*$)"
)
STUB_OK_RE = re.compile(r"stub-ok\(\s*([^)\s][^)]*)\)")
RULE_OK_RE = re.compile(r"rule-ok\(\s*([A-Za-z0-9_.-]+)\s*(?:[:：]\s*|\s+)([^)\s][^)]*)\)")
IGNORE_OK_REASON_RE = re.compile(
    r"ignore-ok(?:\(\s*([^)\s][^)]*)\)|(?:\s*[:：]\s*|\s+)([^\r\n*/]+))"
)
IGNORE_OK_ANY_RE = re.compile(r"\bignore-ok\b")
BOUNDARY_OK_RE = re.compile(r"\bboundary-ok\b")
ABSTRACT_LOOKBACK_LINES = 5

PY_EMPTY_EXCEPT_BLOCK_RE = re.compile(
    r"^[ \t]*except\b[^\n]*:(?:[ \t]*(?:pass|\.\.\.)[ \t]*(?:#[^\n]*)?|"
    r"[ \t]*(?:#[^\n]*)?\n(?:[ \t]*(?:#[^\n]*)?\n)*[ \t]*(?:pass|\.\.\.)[ \t]*(?:#[^\n]*)?)$",
    re.MULTILINE,
)
JS_EMPTY_CATCH_BLOCK_RE = re.compile(
    r"\bcatch\s*(?:\([^)]*\))?\s*\{((?:\s|//[^\n]*|/\*[\s\S]*?\*/)*)\}"
)

PUBLIC_ENTRY_LINE_RE = re.compile(r"唯一(?:公开)?入口\*{0,2}\s*[：:]([^\n]*)")
ENTRY_QUOTED_RE = re.compile(r"`(?:\./)?([A-Za-z0-9_][A-Za-z0-9_.-]*)`")
ENTRY_BARE_RE = re.compile(r"(?<![A-Za-z0-9_./-])(?:\./)?([A-Za-z0-9_][A-Za-z0-9_-]*(?:\.[A-Za-z0-9]+)+)")

JS_IMPORT_RE = re.compile(
    r"""\b(?:import|export)\s+(?:[^'";]*?\s+from\s+)?['"]([^'"]+)['"]"""
    r"""|\b(?:require|import)\s*\(\s*['"]([^'"]+)['"]\s*\)"""
)
PY_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+(\.*[\w.]*)\s+import\s+([\w.*,\s]+)|import\s+([\w.]+))",
    re.MULTILINE,
)

# Python 裸消音模式（仅在真实 tokenize.COMMENT 中匹配）
PY_TYPE_IGNORE_RE = re.compile(r"\b(?:type|pyright)\s*:\s*ignore\b(?!\s*\[\s*[A-Za-z0-9_,-]+(?:\s*,\s*[A-Za-z0-9_,-]+)*\s*\])")
PY_NOQA_RE = re.compile(r"\b(?:ruff\s*:\s*)?noqa\b(?!\s*:\s*[A-Za-z][A-Za-z0-9_-]*)")

# JS/TS 裸消音模式（仅在真实 JS/TS 注释中匹配）
JS_TS_NOCHECK_RE = re.compile(r"@ts-nocheck\b")
JS_TS_IGNORE_RE = re.compile(r"@ts-ignore\b")
JS_TS_EXPECT_ERROR_RE = re.compile(r"@ts-expect-error\b([^\r\n*]*)")
JS_ESLINT_DISABLE_RE = re.compile(r"\beslint-disable(?:-next-line|-line)?\b([^\r\n*]*)")
ESLINT_RULE_HEAD_RE = re.compile(r"^\s*(?:@[A-Za-z0-9_.-]+/)?[A-Za-z][A-Za-z0-9_-]*(?:/[A-Za-z0-9_-]+)?")

SLICE_STATUS_LINE_RE = re.compile(
    r"^\s*-\s*\*\*(?:状态|Status)\*\*\s*[：:]\s*`?([^\s`|]+)`?",
    re.IGNORECASE | re.MULTILINE,
)
CLOSED_SLICE_STATUSES = {"verified", "used", "done"}
SLICE_REF_RE = re.compile(r"\bslice[-_]?(\d+[a-z]?)\b", re.IGNORECASE)
SLICE_STEM_NUM_RE = re.compile(r"^(\d+[a-z]?)(?:-|$)", re.IGNORECASE)


def expand_braces(pattern: str) -> list[str]:
    """展开单层或多层 `{a,b}` 花括号通配（如 `src/**/*.{ts,tsx}`）。"""
    m = re.search(r"\{([^{}]+)\}", pattern)
    if not m:
        return [pattern]
    prefix = pattern[: m.start()]
    suffix = pattern[m.end() :]
    results: list[str] = []
    for part in m.group(1).split(","):
        results.extend(expand_braces(prefix + part.strip() + suffix))
    return results


def strip_leading_dot_slash(raw: str) -> str:
    p = raw.strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def _single_glob_to_regex(pattern: str) -> re.Pattern[str]:
    p = strip_leading_dot_slash(pattern)
    i = 0
    n = len(p)
    out = ["^"]
    while i < n:
        if p.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif p.startswith("/**", i) and i + 3 == n:
            out.append("/.*")
            i += 3
        elif p.startswith("**", i):
            out.append(".*")
            i += 2
        elif p[i] == "*":
            out.append("[^/]*")
            i += 1
        elif p[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(p[i]))
            i += 1
    out.append("$")
    return re.compile("".join(out))


def compile_globs(patterns: list[str]) -> list[re.Pattern[str]]:
    compiled: list[re.Pattern[str]] = []
    for raw in patterns:
        for expanded in expand_braces(raw):
            compiled.append(_single_glob_to_regex(expanded))
    return compiled


def matches_any_glob(rel_posix: str, globs: list[re.Pattern[str]]) -> bool:
    norm = strip_leading_dot_slash(rel_posix)
    return any(g.match(norm) for g in globs)


def load_harness_rules(root: Path) -> tuple[dict, list[str]]:
    """加载并严格校验 `.harness-rules.json`；语法或 schema 非法时返回错误（fail-closed）。"""
    rules_path = root / RULES_FILENAME
    empty = {"present": False, "ignore_globs": [], "ignore_paths_count": 0, "public_entries": {}, "layer_bans": [], "pattern_bans": []}
    if not rules_path.is_file():
        return empty, []

    errors: list[str] = []
    try:
        raw = json.loads(rules_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return empty, [f"{RULES_FILENAME}: JSON 解析失败（fail-closed 拦截）-> {exc}"]

    if not isinstance(raw, dict):
        return empty, [f"{RULES_FILENAME}: 顶层必须是 JSON 对象"]

    unknown_top = sorted(set(raw.keys()) - ALLOWED_RULES_TOP_KEYS)
    if unknown_top:
        errors.append(f"{RULES_FILENAME}: 包含未知顶层字段 {unknown_top}（仅允许 {sorted(ALLOWED_RULES_TOP_KEYS)}）")

    ignore_globs: list[re.Pattern[str]] = []
    ignore_paths_count = 0
    raw_ign = raw.get("ignore_paths")
    if raw_ign is not None:
        if not isinstance(raw_ign, list) or not all(isinstance(x, str) and x.strip() for x in raw_ign):
            errors.append(f"{RULES_FILENAME}: `ignore_paths` 必须为非空字符串 glob 数组")
        else:
            valid_ign: list[str] = []
            for pat in raw_ign:
                norm_pat = strip_leading_dot_slash(pat)
                expanded_parts = [strip_leading_dot_slash(p) for p in expand_braces(norm_pat)]
                if not norm_pat or any(
                    not part or part.startswith("/") or re.match(r"^[A-Za-z]:/", part)
                    for part in expanded_parts
                ):
                    errors.append(f"{RULES_FILENAME}: `ignore_paths` 不允许绝对路径或空路径：`{pat}`")
                    continue
                if any(".." in Path(part).parts for part in expanded_parts):
                    errors.append(f"{RULES_FILENAME}: `ignore_paths` 不允许越界路径段 `..`：`{pat}`")
                    continue
                cand_globs = compile_globs([norm_pat])
                if (
                    (matches_any_glob("__probe_root__.py", cand_globs) and matches_any_glob("__probe_a__/__probe_b__.ts", cand_globs))
                    or (matches_any_glob("__probe_a__/__probe_b__.ts", cand_globs) and matches_any_glob("__probe_c__/__probe_d__.py", cand_globs))
                    or any(part in ("*", "**", "**/*", "**/**", "*/**", "*/*") for part in expanded_parts)
                ):
                    errors.append(f"{RULES_FILENAME}: `ignore_paths` 禁止全仓静默逃逸通配：`{pat}`")
                    continue
                valid_ign.append(norm_pat)
                ignore_globs.extend(cand_globs)
            ignore_paths_count = len(valid_ign)

    public_entries: dict[Path, str] = {}
    raw_pe = raw.get("public_entries")
    if raw_pe is not None:
        if not isinstance(raw_pe, dict):
            errors.append(f"{RULES_FILENAME}: `public_entries` 必须是 `{{目录: 入口文件名}}` 对象")
        else:
            for k, v in raw_pe.items():
                if not isinstance(k, str) or not k.strip() or not isinstance(v, str) or not v.strip():
                    errors.append(f"{RULES_FILENAME}: `public_entries` 条目 `{k}: {v}` 必须为非空字符串")
                    continue
                mod_rel = k.strip().replace("\\", "/").strip("/")
                if ".." in Path(mod_rel).parts:
                    errors.append(f"{RULES_FILENAME}: `public_entries` 目录不能越出项目根：`{k}`")
                    continue
                entry_name = Path(strip_leading_dot_slash(v)).name
                public_entries[(root / mod_rel).resolve()] = Path(entry_name).stem

    layer_bans: list[dict] = []
    raw_lb = raw.get("layer_bans")
    if raw_lb is not None:
        if not isinstance(raw_lb, list):
            errors.append(f"{RULES_FILENAME}: `layer_bans` 必须是数组")
        else:
            seen_ids: set[str] = set()
            for idx, item in enumerate(raw_lb):
                prefix = f"{RULES_FILENAME}: layer_bans[{idx}]"
                if not isinstance(item, dict):
                    errors.append(f"{prefix} 必须是对象")
                    continue
                rid = item.get("id")
                frm = item.get("from")
                forbid = item.get("forbid_imports")
                allow_in = item.get("allow_in", [])
                msg = item.get("message", "")
                if not isinstance(rid, str) or not re.match(r"^[A-Za-z0-9_.-]+$", rid):
                    errors.append(f"{prefix}: `id` 必须为非空标识符（字母/数字/`-`/`_`/`.`）")
                    continue
                if rid in seen_ids:
                    errors.append(f"{prefix}: 规则 `id` 重复：`{rid}`")
                seen_ids.add(rid)
                if not isinstance(frm, list) or not frm or not all(isinstance(x, str) and x.strip() for x in frm):
                    errors.append(f"{prefix} (`{rid}`): `from` 必须为非空路径 glob 数组")
                    continue
                if not isinstance(forbid, list) or not forbid or not all(isinstance(x, str) and x.strip() for x in forbid):
                    errors.append(f"{prefix} (`{rid}`): `forbid_imports` 必须为非空字符串数组")
                    continue
                if not isinstance(allow_in, list) or not all(isinstance(x, str) and x.strip() for x in allow_in):
                    errors.append(f"{prefix} (`{rid}`): `allow_in` 必须为字符串数组")
                    continue
                if not isinstance(msg, str):
                    errors.append(f"{prefix} (`{rid}`): `message` 必须为字符串")
                    continue
                layer_bans.append({
                    "id": rid,
                    "from_globs": compile_globs(frm),
                    "allow_globs": compile_globs(allow_in),
                    "forbid_imports": [x.strip().replace("\\", "/") for x in forbid],
                    "message": msg.strip() or f"违反分层依赖规则 `{rid}`",
                })

    pattern_bans: list[dict] = []
    raw_pb = raw.get("pattern_bans")
    if raw_pb is not None:
        if not isinstance(raw_pb, list):
            errors.append(f"{RULES_FILENAME}: `pattern_bans` 必须是数组")
        else:
            seen_ids = set()
            for idx, item in enumerate(raw_pb):
                prefix = f"{RULES_FILENAME}: pattern_bans[{idx}]"
                if not isinstance(item, dict):
                    errors.append(f"{prefix} 必须是对象")
                    continue
                rid = item.get("id")
                regex_raw = item.get("regex")
                paths = item.get("paths", ["**"])
                allow_in = item.get("allow_in", [])
                msg = item.get("message", "")
                match_comments = item.get("match_comments", False)
                if not isinstance(rid, str) or not re.match(r"^[A-Za-z0-9_.-]+$", rid):
                    errors.append(f"{prefix}: `id` 必须为非空标识符（字母/数字/`-`/`_`/`.`）")
                    continue
                if rid in seen_ids:
                    errors.append(f"{prefix}: 规则 `id` 重复：`{rid}`")
                seen_ids.add(rid)
                if not isinstance(regex_raw, str) or not regex_raw:
                    errors.append(f"{prefix} (`{rid}`): `regex` 必须为非空正则表达式字符串")
                    continue
                try:
                    compiled_re = re.compile(regex_raw)
                except re.error as exc:
                    errors.append(f"{prefix} (`{rid}`): 无效正则表达式 `{regex_raw}` -> {exc}")
                    continue
                if not isinstance(paths, list) or not paths or not all(isinstance(x, str) and x.strip() for x in paths):
                    errors.append(f"{prefix} (`{rid}`): `paths` 必须为非空路径 glob 数组")
                    continue
                if not isinstance(allow_in, list) or not all(isinstance(x, str) and x.strip() for x in allow_in):
                    errors.append(f"{prefix} (`{rid}`): `allow_in` 必须为字符串数组")
                    continue
                if not isinstance(msg, str) or not isinstance(match_comments, bool):
                    errors.append(f"{prefix} (`{rid}`): `message` 须为字符串且 `match_comments` 须为布尔值")
                    continue
                pattern_bans.append({
                    "id": rid,
                    "path_globs": compile_globs(paths),
                    "allow_globs": compile_globs(allow_in),
                    "regex": compiled_re,
                    "message": msg.strip() or f"命中禁用模式 `{rid}`",
                    "match_comments": match_comments,
                })

    return {
        "present": True,
        "ignore_globs": ignore_globs,
        "ignore_paths_count": ignore_paths_count,
        "public_entries": public_entries,
        "layer_bans": layer_bans,
        "pattern_bans": pattern_bans,
    }, errors


def collect_diff_line_map(root: Path, base_ref: str = "") -> dict[str, set[int]] | None:
    """获取工作区相对 HEAD（或与 base_ref 的分叉基点）新增/修改的文件及其行号集合；非 Git 仓返回 None 回退全量。"""
    if not (root / ".git").exists():
        return None
    has_head = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=str(root), capture_output=True).returncode == 0
    if not has_head:
        return None

    compare_ref = "HEAD"
    if base_ref:
        mb = subprocess.run(
            ["git", "merge-base", base_ref, "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
        )
        if mb.returncode != 0 or not mb.stdout.strip():
            return None
        compare_ref = mb.stdout.strip()

    line_map: dict[str, set[int]] = {}
    changed_out = subprocess.run(
        ["git", "diff", compare_ref, "--name-only", "-z", "--diff-filter=ACMRT"],
        cwd=str(root), capture_output=True, check=False,
    ).stdout
    for raw in changed_out.split(b"\0"):
        if not raw:
            continue
        rel = os.fsdecode(raw)
        fpath = root / rel
        if not fpath.is_file():
            continue
        new_lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()
        old_res = subprocess.run(["git", "show", f"{compare_ref}:{rel}"], cwd=str(root), capture_output=True)
        old_lines = old_res.stdout.decode("utf-8", errors="replace").splitlines() if old_res.returncode == 0 else []
        added: set[int] = set()
        for tag, _, _, i2, j2 in difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False).get_opcodes():
            if tag in ("insert", "replace"):
                added.update(range(i2 + 1, j2 + 1))
        line_map[rel] = added

    untracked_out = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=str(root), capture_output=True, check=False,
    ).stdout
    for raw in untracked_out.split(b"\0"):
        if not raw:
            continue
        rel = os.fsdecode(raw)
        fpath = root / rel
        if fpath.is_file():
            cnt = len(fpath.read_text(encoding="utf-8", errors="replace").splitlines())
            line_map[rel] = set(range(1, cnt + 1))

    return line_map


def parse_closed_slices(root: Path) -> dict[str, tuple[str, str]]:
    """扫描 `design/slices/*.md`，返回已结项切片索引：{key: (rel_path, status)}。"""
    slices_dir = root / "design" / "slices"
    if not slices_dir.is_dir():
        return {}
    closed: dict[str, tuple[str, str]] = {}
    for sf in sorted(slices_dir.glob("*.md")):
        try:
            txt = sf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = SLICE_STATUS_LINE_RE.search(txt)
        if not m:
            continue
        status = m.group(1).strip()
        if status.lower() not in CLOSED_SLICE_STATUSES:
            continue
        rel = sf.relative_to(root).as_posix()
        stem = sf.stem.lower()
        closed[stem] = (rel, status)
        num_m = SLICE_STEM_NUM_RE.match(stem)
        if num_m:
            closed[num_m.group(1).lower()] = (rel, status)
    return closed


def match_closed_slice(detail: str, closed_slices: dict[str, tuple[str, str]]) -> tuple[str, str] | None:
    if not closed_slices:
        return None
    low = detail.lower()
    for ref_num in SLICE_REF_RE.findall(low):
        if ref_num in closed_slices:
            return closed_slices[ref_num]
        if ref_num.lstrip("0") in closed_slices:
            return closed_slices[ref_num.lstrip("0")]
    for key, val in closed_slices.items():
        if "-" in key and re.search(rf"(?<![a-z0-9-]){re.escape(key)}(?![a-z0-9-])", low):
            return val
    return None


def stem_without_ext(filename: str) -> str:
    return Path(filename).stem


def parse_public_entry(agents_text: str) -> str | None:
    line = PUBLIC_ENTRY_LINE_RE.search(agents_text)
    if not line:
        return None
    m = ENTRY_QUOTED_RE.search(line.group(1)) or ENTRY_BARE_RE.search(line.group(1))
    return m.group(1) if m else None


def is_pure_comment_line(line: str) -> bool:
    s = line.strip()
    return s.startswith(("#", "//", "/*", "*"))


def find_stub_token(lines: list[str], idx: int) -> str | None:
    line = lines[idx - 1]
    m = STUB_RE.search(line)
    if not m or STUB_OK_RE.search(line):
        return None
    if idx >= 2 and is_pure_comment_line(lines[idx - 2]) and STUB_OK_RE.search(lines[idx - 2]):
        return None
    token = next(g for g in m.groups() if g)
    if "NotImplementedError" in token and any(
        "abstractmethod" in prev for prev in lines[max(0, idx - 1 - ABSTRACT_LOOKBACK_LINES): idx - 1]
    ):
        return None
    return token


def has_rule_waiver(line: str, rule_id: str) -> bool:
    for m in RULE_OK_RE.finditer(line):
        if m.group(1) == rule_id:
            return True
    return False


def has_rule_waiver_at(lines: list[str], idx: int, rule_id: str) -> bool:
    """支持同行行尾或紧邻上一行纯注释的 `rule-ok(<id>: <原因>)`（兼容 ruff/Prettier 自动折行）。"""
    if 1 <= idx <= len(lines) and has_rule_waiver(lines[idx - 1], rule_id):
        return True
    if idx >= 2 and idx - 1 <= len(lines) and is_pure_comment_line(lines[idx - 2]):
        return has_rule_waiver(lines[idx - 2], rule_id)
    return False


def strip_js_comments(content: str) -> tuple[str, list[tuple[int, int, str]]]:
    """词法级拆离 JS/TS 注释（跳过字符串与正则字面量），返回 (同换行位置的纯代码, [(start_line, end_line, comment)])。"""
    n = len(content)
    i = 0
    line = 1
    out: list[str] = []
    comments: list[tuple[int, int, str]] = []
    prev_sig = ""
    in_regex_allowed = True

    while i < n:
        ch = content[i]
        nxt = content[i + 1] if i + 1 < n else ""

        if ch in ("'", '"', "`"):
            quote = ch
            out.append(ch)
            i += 1
            while i < n:
                c = content[i]
                out.append(c)
                if c == "\n":
                    line += 1
                if c == "\\" and i + 1 < n:
                    i += 1
                    out.append(content[i])
                    if content[i] == "\n":
                        line += 1
                    i += 1
                    continue
                if c == quote:
                    i += 1
                    break
                i += 1
            prev_sig = quote
            in_regex_allowed = False
            continue

        if ch == "/" and nxt == "/":
            start_line = line
            start = i
            while i < n and content[i] != "\n":
                out.append(" ")
                i += 1
            comments.append((start_line, start_line, content[start:i]))
            continue

        if ch == "/" and nxt == "*":
            start_line = line
            start = i
            out.append("  ")
            i += 2
            while i < n:
                if content[i] == "*" and i + 1 < n and content[i + 1] == "/":
                    out.append("  ")
                    i += 2
                    break
                if content[i] == "\n":
                    out.append("\n")
                    line += 1
                else:
                    out.append(" ")
                i += 1
            comments.append((start_line, line, content[start:i]))
            continue

        if ch == "/" and in_regex_allowed and nxt not in ("/", "*", "="):
            # 简单识别同行正则字面量 `/.../`，避免误把正则内 `//` 当注释
            j = i + 1
            in_class = False
            closed = False
            while j < n and content[j] != "\n":
                rc = content[j]
                if rc == "\\" and j + 1 < n:
                    j += 2
                    continue
                if rc == "[":
                    in_class = True
                elif rc == "]":
                    in_class = False
                elif rc == "/" and not in_class:
                    closed = True
                    j += 1
                    break
                j += 1
            if closed:
                out.append(content[i:j])
                i = j
                prev_sig = "/"
                in_regex_allowed = False
                continue

        out.append(ch)
        if ch == "\n":
            line += 1
        elif not ch.isspace():
            prev_sig = ch
            in_regex_allowed = ch in "([{:;,=!?&|+-*%^~<>"
        i += 1

    return "".join(out), comments


def strip_python_comments(content: str) -> tuple[str, list[tuple[int, str]]]:
    """用 Python `tokenize` 提取真实注释并生成同换行位置去注释代码，零字符串误判。"""
    lines = content.splitlines(keepends=True)
    masked = [list(ln) for ln in lines]
    comments: list[tuple[int, str]] = []
    try:
        toks = tokenize.generate_tokens(io.StringIO(content).readline)
        for tok in toks:
            if tok.type == tokenize.COMMENT:
                ln_no, col = tok.start
                end_ln, end_col = tok.end
                comments.append((ln_no, tok.string))
                if ln_no == end_ln and 1 <= ln_no <= len(masked):
                    row = masked[ln_no - 1]
                    for c_idx in range(col, min(end_col, len(row))):
                        if row[c_idx] not in ("\r", "\n"):
                            row[c_idx] = " "
    except (tokenize.TokenError, IndentationError):
        # 语法不完整时退回逐行检查注释
        for idx, ln in enumerate(lines, start=1):
            stripped = ln.lstrip()
            if stripped.startswith("#"):
                comments.append((idx, stripped.rstrip("\r\n")))
                masked[idx - 1] = ["\n"] if ln.endswith("\n") else []
    return "".join("".join(row) for row in masked), comments


def extract_python_imports(root: Path, fpath: Path, content: str) -> list[tuple[int, str, Path | None]]:
    """提取 Python 导入，返回 [(line_no, raw_module_expr, resolved_path_or_None)]。"""
    results: list[tuple[int, str, Path | None]] = []

    def resolve_abs_mod(mod_name: str) -> Path | None:
        parts = [p for p in mod_name.split(".") if p]
        if not parts:
            return None
        direct = (root / Path(*parts)).resolve()
        if direct.exists() or direct.with_suffix(".py").exists():
            return direct
        if (root / "src").is_dir():
            under_src = (root / "src" / Path(*parts)).resolve()
            if under_src.exists() or under_src.with_suffix(".py").exists():
                return under_src
        return None

    try:
        tree = ast.parse(content, filename=str(fpath))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    results.append((node.lineno, alias.name, resolve_abs_mod(alias.name)))
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if node.level and node.level > 0:
                    base_dir = fpath.parent
                    for _ in range(node.level - 1):
                        base_dir = base_dir.parent
                    if mod:
                        target = (base_dir / Path(*mod.split("."))).resolve()
                        expr = "." * node.level + mod
                        results.append((node.lineno, expr, target))
                        if target.is_dir():
                            for alias in node.names:
                                if alias.name == "*":
                                    continue
                                child = (target / alias.name).resolve()
                                if child.is_dir() or child.with_suffix(".py").is_file():
                                    results.append((node.lineno, f"{expr}.{alias.name}", child))
                    else:
                        for alias in node.names:
                            if alias.name == "*":
                                results.append((node.lineno, "." * node.level, base_dir.resolve()))
                            else:
                                target = (base_dir / alias.name).resolve()
                                results.append((node.lineno, "." * node.level + alias.name, target))
                elif mod:
                    mod_resolved = resolve_abs_mod(mod)
                    results.append((node.lineno, mod, mod_resolved))
                    if mod_resolved is not None and mod_resolved.is_dir():
                        for alias in node.names:
                            if alias.name == "*":
                                continue
                            child = (mod_resolved / alias.name).resolve()
                            if child.is_dir() or child.with_suffix(".py").is_file():
                                results.append((node.lineno, f"{mod}.{alias.name}", child))
        return results
    except SyntaxError:
        for m in PY_IMPORT_RE.finditer(content):
            line_no = content[: m.start()].count("\n") + 1
            mod_expr = (m.group(1) or m.group(3) or "").strip()
            if mod_expr and not mod_expr.startswith("."):
                results.append((line_no, mod_expr, resolve_abs_mod(mod_expr)))
        return results


def extract_js_imports(root: Path, fpath: Path, code_no_comments: str) -> list[tuple[int, str, Path | None]]:
    results: list[tuple[int, str, Path | None]] = []
    for m in JS_IMPORT_RE.finditer(code_no_comments):
        spec = m.group(1) or m.group(2) or ""
        line_no = code_no_comments[: m.start(1 if m.group(1) else 2)].count("\n") + 1
        resolved: Path | None = None
        if spec.startswith("."):
            resolved = Path(os.path.normpath(str(fpath.parent / spec))).resolve()
        elif spec.startswith(("@/", "~/")):
            resolved = Path(os.path.normpath(str(root / "src" / spec[2:]))).resolve()
        results.append((line_no, spec, resolved))
    return results


def import_matches_forbid(
    root: Path,
    spec: str,
    resolved: Path | None,
    forbid_pattern: str,
) -> bool:
    """判定一条导入（包名或解析后仓库相对路径）是否命中 `forbid_imports` 规则。"""
    fp = strip_leading_dot_slash(forbid_pattern)
    is_path_rule = ("/" in fp or "*" in fp or "?" in fp) and not fp.startswith(("@", "node:"))

    def build_path_globs(pat: str) -> list[re.Pattern[str]]:
        globs = compile_globs([pat])
        if pat.endswith("/**"):
            globs.extend(compile_globs([pat[:-3]]))
        elif not any(ch in pat for ch in "*?{}"):
            globs.extend(compile_globs([f"{pat.rstrip('/')}/**"]))
        return globs

    if resolved is not None:
        try:
            rel_res = resolved.relative_to(root).as_posix()
        except ValueError:
            rel_res = ""
        if rel_res:
            candidates = {rel_res}
            if "." in Path(rel_res).name:
                candidates.add(str(Path(rel_res).with_suffix("")).replace("\\", "/"))
            globs = build_path_globs(fp)
            if any(matches_any_glob(c, globs) for c in candidates):
                return True

    if is_path_rule:
        # 兜底支持以点分模块路径形式书写的 Python 绝对导入（即便目标文件尚未创建）
        norm_spec = spec.lstrip(".").replace(".", "/")
        globs = build_path_globs(fp)
        if norm_spec and matches_any_glob(norm_spec, globs):
            return True
        if norm_spec and matches_any_glob(f"src/{norm_spec}", globs):
            return True
        return False

    # 第三方/标准库包名匹配（兼容 Python `pkg.sub`、JS `pkg/sub`、`node:fs`、`@scope/pkg`）
    spec_clean = spec.strip()
    if spec_clean == fp:
        return True
    if spec_clean.startswith("node:") and spec_clean[5:] == fp:
        return True
    if spec_clean.startswith(fp + "/") or spec_clean.startswith(fp + "."):
        return True
    if spec_clean.startswith("node:" + fp + "/"):
        return True
    if any(ch in fp for ch in "*?{}") and matches_any_glob(spec_clean, compile_globs([fp])):
        return True
    return False


def check_silencers(
    rel: str,
    fpath: Path,
    lines: list[str],
    py_comments: list[tuple[int, str]],
    js_comments: list[tuple[int, int, str]],
    active_lines: set[int] | None,
    problems: list[str],
):
    """全仓拦截裸类型/Lint 消音注解，放行精确错误码/规则名或带原因的 `ignore-ok(...)`。"""
    if fpath.suffix == ".py":
        for line_no, ctext in py_comments:
            if active_lines is not None and line_no not in active_lines:
                continue
            if IGNORE_OK_REASON_RE.search(ctext):
                continue
            if PY_TYPE_IGNORE_RE.search(ctext):
                problems.append(
                    f"{rel}:{line_no}: 检测到裸类型消音 `{ctext.strip()}` -> 请修复根因、指定具体错误码（如 `# type: ignore[code]`）或标注 `ignore-ok(<原因>)`"
                )
            elif PY_NOQA_RE.search(ctext):
                problems.append(
                    f"{rel}:{line_no}: 检测到裸 Lint 消音 `{ctext.strip()}` -> 请指定具体规则码（如 `# noqa: E501`）或标注 `ignore-ok(<原因>)`"
                )
    elif fpath.suffix in JS_EXTS:
        for start_line, end_line, ctext in js_comments:
            if active_lines is not None and not any(ln in active_lines for ln in range(start_line, end_line + 1)):
                continue
            if IGNORE_OK_REASON_RE.search(ctext):
                continue
            if JS_TS_NOCHECK_RE.search(ctext):
                problems.append(
                    f"{rel}:{start_line}: 检测到全文件类型消音 `@ts-nocheck` -> 严禁关闭类型检查，特殊情况请标 `ignore-ok(<原因>)`"
                )
                continue
            if JS_TS_IGNORE_RE.search(ctext):
                problems.append(
                    f"{rel}:{start_line}: 检测到裸 `@ts-ignore` -> 请改用带说明的 `@ts-expect-error: <原因>` 或修复类型根因"
                )
                continue
            em = JS_TS_EXPECT_ERROR_RE.search(ctext)
            if em:
                rest = em.group(1).strip().lstrip(":-—").strip()
                if not rest:
                    problems.append(
                        f"{rel}:{start_line}: 检测到无说明的裸 `@ts-expect-error` -> 请附具体原因（如 `// @ts-expect-error: <原因>`）"
                    )
                    continue
            dm = JS_ESLINT_DISABLE_RE.search(ctext)
            if dm:
                rest = dm.group(1).strip()
                # 去掉 `-- 注释说明` 后必须仍有至少一个具体规则名
                before_desc = re.split(r"(?:^|\s)--(?:\s|$)", rest, maxsplit=1)[0].strip()
                if not before_desc or not ESLINT_RULE_HEAD_RE.match(before_desc):
                    problems.append(
                        f"{rel}:{start_line}: 检测到无规则名的裸 `eslint-disable` -> 请指定具体规则名（如 `eslint-disable-next-line no-console`）或标 `ignore-ok(<原因>)`"
                    )


def collect_file_exemptions(
    rel: str,
    lines: list[str],
    closed_slices: dict[str, tuple[str, str]],
    exemptions: list[dict],
    stale_stubs: list[dict],
):
    for idx, line in enumerate(lines, start=1):
        for m in STUB_OK_RE.finditer(line):
            detail = m.group(1).strip()
            closed = match_closed_slice(detail, closed_slices)
            item = {
                "kind": "stub-ok",
                "file": rel,
                "line": idx,
                "detail": detail,
                "stale_slice": {"slice": closed[0], "status": closed[1]} if closed else None,
            }
            exemptions.append(item)
            if closed:
                stale_stubs.append(item)
        for m in RULE_OK_RE.finditer(line):
            exemptions.append({
                "kind": "rule-ok",
                "file": rel,
                "line": idx,
                "detail": f"{m.group(1)}: {m.group(2).strip()}",
            })
        if IGNORE_OK_ANY_RE.search(line):
            rm = IGNORE_OK_REASON_RE.search(line)
            detail = (rm.group(1) or rm.group(2) or "").strip() if rm else ""
            exemptions.append({"kind": "ignore-ok", "file": rel, "line": idx, "detail": detail})
        if BOUNDARY_OK_RE.search(line):
            exemptions.append({"kind": "boundary-ok", "file": rel, "line": idx, "detail": ""})


def check_module_boundaries_for_file(
    root: Path,
    rel: str,
    fpath: Path,
    lines: list[str],
    imports: list[tuple[int, str, Path | None]],
    module_contracts: dict[Path, str],
    active_lines: set[int] | None,
    problems: list[str],
):
    if not module_contracts:
        return
    for line_no, spec, resolved in imports:
        if active_lines is not None and line_no not in active_lines:
            continue
        if line_no <= len(lines) and BOUNDARY_OK_RE.search(lines[line_no - 1]):
            continue

        if fpath.suffix in JS_EXTS:
            if resolved is None:
                continue
            for mod_dir, entry_stem in module_contracts.items():
                if fpath == mod_dir or mod_dir in fpath.parents:
                    continue
                if resolved == mod_dir:
                    if entry_stem != "index":
                        problems.append(
                            f"{rel}:{line_no}: 模块边界越权导入 `{spec}` -> `{mod_dir.relative_to(root)}` 唯一公开入口为 `{entry_stem}`"
                        )
                elif mod_dir in resolved.parents:
                    rel_inside = resolved.relative_to(mod_dir).parts
                    if len(rel_inside) > 1 or stem_without_ext(rel_inside[0]) != entry_stem:
                        problems.append(
                            f"{rel}:{line_no}: 模块边界越权深层导入 `{spec}` -> 禁止绕过 `{mod_dir.relative_to(root)}/{entry_stem}` 直接引用内部文件"
                        )
        elif fpath.suffix == ".py":
            # 优先使用已解析路径（兼容相对导入与 src/ 目录布局），再退回点分模块名匹配
            matched_via_resolved = False
            if resolved is not None:
                for mod_dir, entry_stem in module_contracts.items():
                    if fpath == mod_dir or mod_dir in fpath.parents:
                        continue
                    if mod_dir in resolved.parents:
                        matched_via_resolved = True
                        rel_inside = resolved.relative_to(mod_dir).parts
                        if len(rel_inside) > 1 or stem_without_ext(rel_inside[0]) not in (entry_stem, "__init__"):
                            problems.append(
                                f"{rel}:{line_no}: 模块边界越权导入 `{spec}` -> 禁止绕过 `{mod_dir.relative_to(root)}/{entry_stem}.py` 直接引用内部模块"
                            )
            if not matched_via_resolved and spec and not spec.startswith("."):
                parts = spec.split(".")
                for mod_dir, entry_stem in module_contracts.items():
                    if fpath == mod_dir or mod_dir in fpath.parents:
                        continue
                    mod_rel_parts = mod_dir.relative_to(root).parts
                    for candidate_parts in (mod_rel_parts, mod_rel_parts[1:] if len(mod_rel_parts) > 1 else ()):
                        if not candidate_parts or tuple(parts[: len(candidate_parts)]) != candidate_parts:
                            continue
                        rest = parts[len(candidate_parts) :]
                        if rest and (len(rest) > 1 or rest[0] not in (entry_stem, "__init__")):
                            problems.append(
                                f"{rel}:{line_no}: 模块边界越权导入 `{spec}` -> 禁止绕过 `{mod_dir.relative_to(root)}/{entry_stem}.py` 直接引用内部模块"
                            )


def check_layer_bans_for_file(
    root: Path,
    rel: str,
    lines: list[str],
    imports: list[tuple[int, str, Path | None]],
    layer_bans: list[dict],
    active_lines: set[int] | None,
    problems: list[str],
):
    if not layer_bans or not imports:
        return
    for rule in layer_bans:
        if not matches_any_glob(rel, rule["from_globs"]):
            continue
        if rule["allow_globs"] and matches_any_glob(rel, rule["allow_globs"]):
            continue
        reported_lines: set[int] = set()
        for line_no, spec, resolved in imports:
            if line_no in reported_lines:
                continue
            if active_lines is not None and line_no not in active_lines:
                continue
            if has_rule_waiver_at(lines, line_no, rule["id"]):
                continue
            for forbid in rule["forbid_imports"]:
                if import_matches_forbid(root, spec, resolved, forbid):
                    problems.append(
                        f"{rel}:{line_no}: [{rule['id']}] 禁止导入 `{spec}`（命中 `{forbid}`）-> {rule['message']}"
                    )
                    reported_lines.add(line_no)
                    break


def check_pattern_bans_for_file(
    rel: str,
    raw_lines: list[str],
    code_only_lines: list[str],
    pattern_bans: list[dict],
    active_lines: set[int] | None,
    problems: list[str],
):
    if not pattern_bans:
        return
    for rule in pattern_bans:
        if not matches_any_glob(rel, rule["path_globs"]):
            continue
        if rule["allow_globs"] and matches_any_glob(rel, rule["allow_globs"]):
            continue
        target_lines = raw_lines if rule["match_comments"] else code_only_lines
        for idx, text in enumerate(target_lines, start=1):
            if active_lines is not None and idx not in active_lines:
                continue
            if has_rule_waiver_at(raw_lines, idx, rule["id"]):
                continue
            if rule["regex"].search(text):
                problems.append(f"{rel}:{idx}: [{rule['id']}] {rule['message']}")


def scan_project(
    root: Path, diff_only: bool = False, base_ref: str = ""
) -> tuple[list[str], int, dict]:
    """返回 (问题列表, 纳入扫描范围的源码文件数, 审计汇总字典)。"""
    problems: list[str] = []
    rules, rule_errors = load_harness_rules(root)
    problems.extend(rule_errors)

    diff_map = collect_diff_line_map(root, base_ref=base_ref) if diff_only else None
    closed_slices = parse_closed_slices(root)

    # 先收集模块 AGENTS.md 声明，再由 `.harness-rules.json` 的 `public_entries` 覆盖（规则文件为唯一事实源）
    module_contracts: dict[Path, str] = {}
    module_agents_stats: list[dict] = []
    code_files: list[Path] = []
    exemptions: list[dict] = []
    stale_stubs: list[dict] = []
    ignore_globs = rules.get("ignore_globs") or []

    for dirpath, dirnames, filenames in os.walk(root):
        cur_dir = Path(dirpath)
        at_root = cur_dir == root
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIRS and not d.startswith(".") and not (at_root and d in NON_CODE_TOP_DIRS)
        )
        for fname in sorted(filenames):
            fpath = cur_dir / fname
            rel = fpath.relative_to(root).as_posix()

            if ignore_globs and matches_any_glob(rel, ignore_globs):
                continue

            if fname == "AGENTS.md":
                if at_root:
                    continue
                txt = fpath.read_text(encoding="utf-8", errors="replace")
                non_empty = [ln for ln in txt.splitlines() if ln.strip()]
                module_agents_stats.append({"file": rel, "lines": len(non_empty)})
                if len(non_empty) > MAX_MODULE_AGENTS_NONEMPTY_LINES:
                    problems.append(
                        f"{rel}: 模块就近契约非空行达 {len(non_empty)} 行（建议 ≤20 行，硬上限 {MAX_MODULE_AGENTS_NONEMPTY_LINES} 行），"
                        f"请将机械规则升级至 `.harness-rules.json`，仅保留纯业务不变量与单测命令"
                    )
                entry = parse_public_entry(txt)
                if entry:
                    module_contracts[cur_dir.resolve()] = stem_without_ext(entry)
                continue

            if TEST_FILE_RE.search(fname) or fpath.suffix not in CODE_EXTS:
                continue
            code_files.append(fpath)

    module_contracts.update(rules["public_entries"])

    for fpath in code_files:
        rel = fpath.relative_to(root).as_posix()
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = content.splitlines()

        # 豁免债账本始终统计全仓（供 ws learn 审计）
        file_stale_before = len(stale_stubs)
        collect_file_exemptions(rel, lines, closed_slices, exemptions, stale_stubs)

        if diff_map is not None and rel not in diff_map:
            continue
        active_lines = diff_map.get(rel) if diff_map is not None else None

        # 过期占位债硬拦
        for stale in stale_stubs[file_stale_before:]:
            if active_lines is not None and stale["line"] not in active_lines:
                continue
            s_info = stale["stale_slice"]
            problems.append(
                f"{rel}:{stale['line']}: 过期占位债 `stub-ok({stale['detail']})` -> 关联切片 `{s_info['slice']}` 状态已为 `{s_info['status']}`，请清理占位并补齐真实实现"
            )

        # 未备案占位
        for idx in range(1, len(lines) + 1):
            if active_lines is not None and idx not in active_lines:
                continue
            token = find_stub_token(lines, idx)
            if token:
                problems.append(
                    f"{rel}:{idx}: 生产代码残留未备案占位 `{token}` -> 请完成真实实现，若确属跨切片预留请标 `stub-ok(slice-XX)`"
                )

        # 空异常吞错
        if fpath.suffix == ".py":
            for pm in PY_EMPTY_EXCEPT_BLOCK_RE.finditer(content):
                start_line = content[: pm.start()].count("\n") + 1
                end_line = content[: pm.end()].count("\n") + 1
                if active_lines is not None and not any(ln in active_lines for ln in range(start_line, end_line + 1)):
                    continue
                if "ignore-ok" not in pm.group(0):
                    problems.append(
                        f"{rel}:{start_line}: 检测到静默吞错 `except ...: pass/...` -> 请显式处理/记录异常或标注 `ignore-ok`"
                    )
        elif fpath.suffix in JS_EXTS:
            for jm in JS_EMPTY_CATCH_BLOCK_RE.finditer(content):
                start_line = content[: jm.start()].count("\n") + 1
                end_line = content[: jm.end()].count("\n") + 1
                if active_lines is not None and not any(ln in active_lines for ln in range(start_line, end_line + 1)):
                    continue
                end_line_text = lines[end_line - 1] if end_line <= len(lines) else ""
                start_line_text = lines[start_line - 1] if start_line <= len(lines) else ""
                if "ignore-ok" not in jm.group(0) and "ignore-ok" not in end_line_text and "ignore-ok" not in start_line_text:
                    problems.append(
                        f"{rel}:{start_line}: 检测到空 `catch {{}}` 静默吞错 -> 请显式处理错误或标注 `ignore-ok`"
                    )

        # 注释拆离 + 裸消音拦截 + 导入提取
        py_comments: list[tuple[int, str]] = []
        js_comments: list[tuple[int, int, str]] = []
        code_only = content
        imports: list[tuple[int, str, Path | None]] = []

        if fpath.suffix == ".py":
            code_only, py_comments = strip_python_comments(content)
            imports = extract_python_imports(root, fpath, content)
        elif fpath.suffix in JS_EXTS:
            code_only, js_comments = strip_js_comments(content)
            imports = extract_js_imports(root, fpath, code_only)
        else:
            # Go / Rust 简单去单行 `//` 注释供 pattern_bans 使用
            code_only = re.sub(r"//[^\n]*", "", content)

        check_silencers(rel, fpath, lines, py_comments, js_comments, active_lines, problems)
        check_module_boundaries_for_file(root, rel, fpath, lines, imports, module_contracts, active_lines, problems)
        check_layer_bans_for_file(root, rel, lines, imports, rules["layer_bans"], active_lines, problems)
        check_pattern_bans_for_file(rel, lines, code_only.splitlines(), rules["pattern_bans"], active_lines, problems)

    audit = {
        "rules_file": RULES_FILENAME if rules["present"] else None,
        "rules_counts": {
            "ignore_paths": rules.get("ignore_paths_count", 0),
            "public_entries": len(rules["public_entries"]),
            "layer_bans": len(rules["layer_bans"]),
            "pattern_bans": len(rules["pattern_bans"]),
        },
        "rules_errors": rule_errors,
        "scanned_files": len(code_files),
        "module_agents": module_agents_stats,
        "exemptions": exemptions,
        "stale_stubs": stale_stubs,
    }
    return problems, len(code_files), audit


def main() -> int:
    raw_argv = sys.argv[1:]
    diff_only = False
    audit_json = False
    base_ref = os.environ.get("WS_CHECK_BASE", "").strip()
    pos_args: list[str] = []
    i = 0
    while i < len(raw_argv):
        arg = raw_argv[i]
        if arg == "--diff":
            diff_only = True
        elif arg == "--audit-json":
            audit_json = True
        elif arg == "--base" and i + 1 < len(raw_argv):
            base_ref = raw_argv[i + 1].strip()
            i += 1
        elif arg.startswith("--base="):
            base_ref = arg.split("=", 1)[1].strip()
        else:
            pos_args.append(arg)
        i += 1

    root = Path(pos_args[0] if pos_args else os.getcwd()).resolve()
    problems, scanned, audit = scan_project(root, diff_only=diff_only, base_ref=base_ref)
    if audit_json:
        print(json.dumps(audit, ensure_ascii=False))
        return 0
    if problems:
        print("代码园丁门禁检查未通过：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    if scanned == 0:
        print("代码园丁门禁检查：未发现可扫描的源码文件（design/、docs/、tests/ 等非生产目录不在扫描范围内）。")
    else:
        r_note = ""
        if audit["rules_file"]:
            rc = audit["rules_counts"]
            r_note = (
                f"，已启用 {RULES_FILENAME}"
                f"（忽略 {rc.get('ignore_paths', 0)} / 入口 {rc['public_entries']} / 分层 {rc['layer_bans']} / 禁式 {rc['pattern_bans']}）"
            )
        print(f"代码园丁门禁检查通过（已扫描 {scanned} 个源码文件{r_note}）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
