#!/usr/bin/env python3
"""M4 反偷懒、模块契约预算与公开入口边界检查器（Anti-Stubbing & Module Boundary Guard）。

用法：
  `python3 check_anti_stub.py [项目根目录] [--diff]`

检查项（扫描项目内全部源码文件，自动跳过 design/、docs/、tests/ 等非生产目录、隐藏目录与构建产物）：
1. 拦截未备案的半成品占位（TODO / FIXME / HACK / XXX / placeholder 注释、MOCK_DATA 等假数据常量、
   `raise NotImplementedError` / “not implemented” 抛错 / `todo!()`），除非标注 `stub-ok(<具体原因或切片号>)`。
   本项只认显式标记，查不出“不写标记的假实现”（如硬编码返回值）——那一类靠 `adversarial-review` 零上下文审 Diff。
2. 拦截单行与跨行静默吞错（Python `except ...: pass` / `...` 及 JS/TS 空或纯注释 `catch {}`），除非标注 `ignore-ok`。
3. 守护模块就近契约 `AGENTS.md` 行数预算（建议 ≤40 行，硬上限 45 非空行）。
4. 守护模块公开入口边界：若 `src/<mod>/AGENTS.md` 声明了 `唯一公开入口: ./index.ts`，拦截其他模块绕过公开入口深层引用内部文件（除非标注 `boundary-ok`）。
5. `--diff` 增量模式：仅对相对 `HEAD` 新增/修改的代码行检查占位与吞错（方便存量老仓库渐进接入）。
"""
from __future__ import annotations
import difflib
import os
import re
import subprocess
import sys
from pathlib import Path

# 仅在项目根一级跳过的非生产目录（设计产物、文档、脚本与第三方代码）；其余目录一律视为源码，不依赖固定目录名白名单
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

STUB_RE = re.compile(
    r"(?://|#|\*)\s*(TODO|FIXME|HACK|XXX)\b"
    r"|\b(MOCK_DATA|FAKE_DATA|DUMMY_DATA)\b"
    r"|\b(raise\s+NotImplementedError|(?:todo|unimplemented)!\s*\()"
    r"|(?i:\b(?:throw|panic|raise)\b[^\n]*?\b(not\s+(?:yet\s+)?implemented)\b)"
    r"|(?i:(?://|#)\s*(placeholder)\s*$)"  # 只认整条注释就是 placeholder 的标记，不误伤描述界面占位文案的注释
)
STUB_OK_RE = re.compile(r"stub-ok\(\s*[^)\s][^)]*\)")
# 抽象方法里的 `raise NotImplementedError` 是接口声明而非半成品：向上回看这几行内有 @abstractmethod 即放行
ABSTRACT_LOOKBACK_LINES = 5

# 支持单行与标准多行 Python 空 except 块（含 pass 或 ...，允许夹杂空行或注释）
PY_EMPTY_EXCEPT_BLOCK_RE = re.compile(
    r"^[ \t]*except\b[^\n]*:(?:[ \t]*(?:pass|\.\.\.)[ \t]*(?:#[^\n]*)?|"
    r"[ \t]*(?:#[^\n]*)?\n(?:[ \t]*(?:#[^\n]*)?\n)*[ \t]*(?:pass|\.\.\.)[ \t]*(?:#[^\n]*)?)$",
    re.MULTILINE,
)

# 支持单行、跨行以及内部仅有注释的 JS/TS 空 catch 块
JS_EMPTY_CATCH_BLOCK_RE = re.compile(
    r"\bcatch\s*(?:\([^)]*\))?\s*\{((?:\s|//[^\n]*|/\*[\s\S]*?\*/)*)\}"
)

PUBLIC_ENTRY_LINE_RE = re.compile(r"唯一(?:公开)?入口\*{0,2}\s*[：:]([^\n]*)")
# 入口文件名只认 ASCII：`\w` 会把中文说明文字也吃进来
ENTRY_QUOTED_RE = re.compile(r"`(?:\./)?([A-Za-z0-9_][A-Za-z0-9_.-]*)`")
ENTRY_BARE_RE = re.compile(r"(?<![A-Za-z0-9_./-])(?:\./)?([A-Za-z0-9_][A-Za-z0-9_-]*(?:\.[A-Za-z0-9]+)+)")
# `from` 子句不得跨越分号或其他字符串，避免把前一条 export 语句和后面的 import 粘成一条；同时覆盖 require() 与动态 import()
JS_IMPORT_RE = re.compile(
    r"""\b(?:import|export)\s+(?:[^'";]*?\s+from\s+)?['"]([^'"]+)['"]"""
    r"""|\b(?:require|import)\s*\(\s*['"]([^'"]+)['"]\s*\)"""
)
PY_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([\w.]+)\s+import\s+([\w.*,\s]+)|import\s+([\w.]+))",
    re.MULTILINE,
)

MAX_MODULE_AGENTS_NONEMPTY_LINES = 45


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
            return None  # 无法解析基线时回退全量扫描（fail-safe）
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


def stem_without_ext(filename: str) -> str:
    return Path(filename).stem


def parse_public_entry(agents_text: str) -> str | None:
    """从模块契约的“唯一公开入口”一行取出入口文件名：优先取反引号内的第一个，其次取行内第一个带扩展名的文件名。"""
    line = PUBLIC_ENTRY_LINE_RE.search(agents_text)
    if not line:
        return None
    m = ENTRY_QUOTED_RE.search(line.group(1)) or ENTRY_BARE_RE.search(line.group(1))
    return m.group(1) if m else None


def find_stub_token(lines: list[str], idx: int) -> str | None:
    """返回第 idx 行（从 1 起）命中的未备案占位标记；已标 `stub-ok(...)` 或属于抽象方法声明时返回 None。"""
    line = lines[idx - 1]
    m = STUB_RE.search(line)
    if not m or STUB_OK_RE.search(line):
        return None
    token = next(g for g in m.groups() if g)
    if "NotImplementedError" in token and any(
        "abstractmethod" in prev for prev in lines[max(0, idx - 1 - ABSTRACT_LOOKBACK_LINES): idx - 1]
    ):
        return None
    return token


def check_module_boundaries(
    root: Path,
    module_contracts: dict[Path, str],
    code_files: list[Path],
    diff_map: dict[str, set[int]] | None,
    problems: list[str],
):
    """检查外部文件是否绕过模块 AGENTS.md 声明的唯一公开入口直接导入模块内部文件。"""
    if not module_contracts:
        return

    for fpath in code_files:
        rel = fpath.relative_to(root).as_posix()
        if diff_map is not None and rel not in diff_map:
            continue
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = content.splitlines()

        if fpath.suffix in JS_EXTS:
            for m in JS_IMPORT_RE.finditer(content):
                spec = m.group(1) or m.group(2) or ""
                # 行号取模块路径字符串所在行（多行 import 的 `from '...'` 行），豁免标注也写在这一行
                line_no = content[: m.start(1 if m.group(1) else 2)].count("\n") + 1
                if line_no <= len(lines) and "boundary-ok" in lines[line_no - 1]:
                    continue

                resolved_target: Path | None = None
                if spec.startswith("."):
                    resolved_target = Path(os.path.normpath(str(fpath.parent / spec)))
                elif spec.startswith(("@/", "~/")):
                    resolved_target = Path(os.path.normpath(str(root / "src" / spec[2:])))

                if not resolved_target:
                    continue

                for mod_dir, entry_stem in module_contracts.items():
                    # 同一模块内部互引允许
                    if fpath == mod_dir or mod_dir in fpath.parents:
                        continue
                    if resolved_target == mod_dir:
                        if entry_stem != "index":
                            problems.append(
                                f"{rel}:{line_no}: 模块边界越权导入 `{spec}` -> `{mod_dir.relative_to(root)}` 唯一公开入口为 `{entry_stem}`"
                            )
                    elif mod_dir in resolved_target.parents:
                        rel_inside = resolved_target.relative_to(mod_dir).parts
                        if len(rel_inside) > 1 or stem_without_ext(rel_inside[0]) != entry_stem:
                            problems.append(
                                f"{rel}:{line_no}: 模块边界越权深层导入 `{spec}` -> 禁止绕过 `{mod_dir.relative_to(root)}/{entry_stem}` 直接引用内部文件"
                            )

        elif fpath.suffix == ".py":
            for m in PY_IMPORT_RE.finditer(content):
                line_no = content[: m.start()].count("\n") + 1
                if line_no <= len(lines) and "boundary-ok" in lines[line_no - 1]:
                    continue
                mod_expr = m.group(1) or m.group(3) or ""
                if not mod_expr or mod_expr.startswith("."):
                    continue
                parts = mod_expr.split(".")
                for mod_dir, entry_stem in module_contracts.items():
                    if fpath == mod_dir or mod_dir in fpath.parents:
                        continue
                    mod_rel_parts = mod_dir.relative_to(root).parts
                    # 支持 `from src.orders.repo import ...` 或省略顶层 `src.` 的 `from orders.repo import ...`
                    for candidate_parts in (mod_rel_parts, mod_rel_parts[1:] if len(mod_rel_parts) > 1 else ()):
                        if not candidate_parts or tuple(parts[: len(candidate_parts)]) != candidate_parts:
                            continue
                        rest = parts[len(candidate_parts) :]
                        if rest and (len(rest) > 1 or rest[0] not in (entry_stem, "__init__")):
                            problems.append(
                                f"{rel}:{line_no}: 模块边界越权导入 `{mod_expr}` -> 禁止绕过 `{mod_dir.relative_to(root)}/{entry_stem}.py` 直接引用内部模块"
                            )


def scan_project(root: Path, diff_only: bool = False, base_ref: str = "") -> tuple[list[str], int]:
    """返回 (问题列表, 纳入扫描范围的源码文件数)。"""
    problems: list[str] = []
    diff_map = collect_diff_line_map(root, base_ref=base_ref) if diff_only else None
    module_contracts: dict[Path, str] = {}
    code_files: list[Path] = []

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

            if fname == "AGENTS.md":
                if at_root:
                    continue  # 项目根 AGENTS.md 是协作宪章，不受模块契约行数预算约束
                txt = fpath.read_text(encoding="utf-8", errors="replace")
                lines = txt.splitlines()
                non_empty = [ln for ln in lines if ln.strip()]
                if len(non_empty) > MAX_MODULE_AGENTS_NONEMPTY_LINES:
                    problems.append(
                        f"{rel}: 模块就近契约非空行达 {len(non_empty)} 行（建议 ≤40 行，硬上限 {MAX_MODULE_AGENTS_NONEMPTY_LINES} 行），"
                        f"请只保留职责禁区、唯一入口、不变量与单测命令"
                    )
                entry = parse_public_entry(txt)
                if entry:
                    module_contracts[cur_dir] = stem_without_ext(entry)
                continue

            if TEST_FILE_RE.search(fname) or fpath.suffix not in CODE_EXTS:
                continue

            code_files.append(fpath)

            if diff_map is not None and rel not in diff_map:
                continue
            active_lines = diff_map.get(rel) if diff_map is not None else None

            try:
                content = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue

            lines = content.splitlines()
            for idx in range(1, len(lines) + 1):
                if active_lines is not None and idx not in active_lines:
                    continue
                token = find_stub_token(lines, idx)
                if token:
                    problems.append(
                        f"{rel}:{idx}: 生产代码残留未备案占位 `{token}` -> 请完成真实实现，若确属跨切片预留请标 `stub-ok(slice-XX)`"
                    )

            if fpath.suffix == ".py":
                for pm in PY_EMPTY_EXCEPT_BLOCK_RE.finditer(content):
                    start_line = content[: pm.start()].count("\n") + 1
                    end_line = content[: pm.end()].count("\n") + 1
                    if active_lines is not None and not any(ln in active_lines for ln in range(start_line, end_line + 1)):
                        continue
                    block_text = pm.group(0)
                    if "ignore-ok" not in block_text:
                        problems.append(
                            f"{rel}:{start_line}: 检测到静默吞错 `except ...: pass/...` -> 请显式处理/记录异常或标注 `ignore-ok`"
                        )

            if fpath.suffix in JS_EXTS:
                for jm in JS_EMPTY_CATCH_BLOCK_RE.finditer(content):
                    start_line = content[: jm.start()].count("\n") + 1
                    end_line = content[: jm.end()].count("\n") + 1
                    if active_lines is not None and not any(ln in active_lines for ln in range(start_line, end_line + 1)):
                        continue
                    # 检查 catch 块本身及其所在起止行尾是否包含 ignore-ok
                    end_line_text = lines[end_line - 1] if end_line <= len(lines) else ""
                    start_line_text = lines[start_line - 1] if start_line <= len(lines) else ""
                    if "ignore-ok" not in jm.group(0) and "ignore-ok" not in end_line_text and "ignore-ok" not in start_line_text:
                        problems.append(
                            f"{rel}:{start_line}: 检测到空 `catch {{}}` 静默吞错 -> 请显式处理错误或标注 `ignore-ok`"
                        )

    check_module_boundaries(root, module_contracts, code_files, diff_map, problems)
    return problems, len(code_files)


def main() -> int:
    raw_argv = sys.argv[1:]
    diff_only = False
    base_ref = os.environ.get("WS_CHECK_BASE", "").strip()
    pos_args: list[str] = []
    i = 0
    while i < len(raw_argv):
        arg = raw_argv[i]
        if arg == "--diff":
            diff_only = True
        elif arg == "--base" and i + 1 < len(raw_argv):
            base_ref = raw_argv[i + 1].strip()
            i += 1
        elif arg.startswith("--base="):
            base_ref = arg.split("=", 1)[1].strip()
        else:
            pos_args.append(arg)
        i += 1
    root = Path(pos_args[0] if pos_args else os.getcwd()).resolve()
    problems, scanned = scan_project(root, diff_only=diff_only, base_ref=base_ref)
    if problems:
        print("反假实现与模块契约检查未通过：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    if scanned == 0:
        # 明说“没扫到东西”，不把空扫描报成通过
        print("反假实现与模块契约检查：未发现可扫描的源码文件（design/、docs/、tests/ 等非生产目录不在扫描范围内）。")
    else:
        print(f"反假实现与模块契约检查通过（已扫描 {scanned} 个源码文件）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
