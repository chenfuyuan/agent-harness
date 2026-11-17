#!/usr/bin/env python3
"""AST 与结构化代码骨架提取器（供 `ws map` 与 `read_guard.py` 共用）。

设计原则：
- Python 源码走内置标准库 `ast`，精确提取模块首句说明、类、方法、函数签名与 1-based 起止行号；
- TS/TSX/JS/Go/Rust/Swift/Kotlin/Dart/Java/C/C++ 优先批量调用本机 `ast-grep`（毫秒级 Tree-sitter AST），
  本机未安装 `ast-grep` 时自动降级为括号/缩进平衡的声明扫描器，保证零外部强依赖；
- Markdown（跳过代码块围栏提取章节区间）、JSON、SQL、HTML 提供专门的结构化目录。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple


class SymbolItem(NamedTuple):
    start_line: int
    end_line: int
    indent: int
    signature: str


IGNORE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__",
    "dist", "build", "out", ".next", ".nuxt", ".svelte-kit", "coverage",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", "target", "vendor",
    ".build", ".ops-e2e", ".live", "backups",
}
IGNORE_SUFFIXES = {
    ".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe", ".bin", ".o", ".a",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".tar",
    ".gz", ".bz2", ".xz", ".7z", ".woff", ".woff2", ".ttf", ".eot", ".mp4",
    ".mp3", ".wav", ".db", ".sqlite", ".sqlite3", ".parquet", ".lock", ".tsbuildinfo",
}
IGNORE_FILENAMES = {
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "uv.lock", "poetry.lock", "Cargo.lock",
}

AST_GREP_LANG_BY_SUFFIX = {
    ".ts": "TypeScript",
    ".mts": "TypeScript",
    ".cts": "TypeScript",
    ".tsx": "Tsx",
    ".js": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".jsx": "Tsx",
    ".go": "Go",
    ".rs": "Rust",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".dart": "Dart",
    ".java": "Java",
    ".c": "C",
    ".h": "C",
    ".cpp": "Cpp",
    ".cc": "Cpp",
    ".hpp": "Cpp",
}

AST_GREP_RULES_YAML = """
id: ts-sym
language: TypeScript
rule:
  any:
    - kind: function_declaration
    - kind: class_declaration
    - kind: interface_declaration
    - kind: type_alias_declaration
    - kind: enum_declaration
    - kind: method_definition
    - kind: lexical_declaration
      has:
        kind: variable_declarator
        has:
          kind: arrow_function
---
id: tsx-sym
language: Tsx
rule:
  any:
    - kind: function_declaration
    - kind: class_declaration
    - kind: interface_declaration
    - kind: type_alias_declaration
    - kind: enum_declaration
    - kind: method_definition
    - kind: lexical_declaration
      has:
        kind: variable_declarator
        has:
          kind: arrow_function
---
id: js-sym
language: JavaScript
rule:
  any:
    - kind: function_declaration
    - kind: class_declaration
    - kind: method_definition
    - kind: lexical_declaration
      has:
        kind: variable_declarator
        has:
          kind: arrow_function
---
id: go-sym
language: Go
rule:
  any:
    - kind: function_declaration
    - kind: method_declaration
    - kind: type_declaration
---
id: rust-sym
language: Rust
rule:
  any:
    - kind: function_item
    - kind: struct_item
    - kind: enum_item
    - kind: trait_item
    - kind: impl_item
---
id: swift-sym
language: Swift
rule:
  any:
    - kind: class_declaration
    - kind: function_declaration
    - kind: protocol_declaration
---
id: kotlin-sym
language: Kotlin
rule:
  any:
    - kind: class_declaration
    - kind: object_declaration
    - kind: function_declaration
---
id: java-sym
language: Java
rule:
  any:
    - kind: class_declaration
    - kind: interface_declaration
    - kind: enum_declaration
    - kind: record_declaration
    - kind: method_declaration
---
id: c-sym
language: C
rule:
  any:
    - kind: function_definition
    - kind: struct_specifier
---
id: cpp-sym
language: Cpp
rule:
  any:
    - kind: function_definition
    - kind: class_specifier
    - kind: struct_specifier
""".strip()


def find_ast_grep_bin() -> str | None:
    if os.environ.get("WS_DISABLE_AST_GREP") == "1":
        return None
    found = shutil.which("ast-grep")
    if found:
        return found
    for candidate in ("/opt/homebrew/bin/ast-grep", "/usr/local/bin/ast-grep"):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _clean_one_line(text: str, max_len: int = 96) -> str:
    collapsed = re.sub(r"\s+", " ", text.strip())
    return collapsed if len(collapsed) <= max_len else collapsed[: max_len - 1] + "…"


def extract_top_summary(lines: list[str], suffix: str) -> str:
    """提取文件顶部 1–2 行职责简述（模块 docstring 或文件头注释），无则返回空串。"""
    if not lines:
        return ""
    if suffix in (".py", ".pyi"):
        try:
            mod = ast.parse("\n".join(lines))
            doc = ast.get_docstring(mod)
            if doc:
                first = next((ln.strip() for ln in doc.splitlines() if ln.strip()), "")
                return _clean_one_line(first, 90)
        except SyntaxError:  # ignore-ok: 语法未闭合时降级读取顶部注释
            pass
    comment_lines: list[str] = []
    for raw in lines[:12]:
        s = raw.strip()
        if not s or s.startswith("#!"):
            continue
        if s.startswith(("import ", "from ", "export ", "package ", "use ")):
            if comment_lines:
                break
            continue
        if s.startswith(("//", "#")) and not s.startswith(("# ", "## ", "### ")):
            cleaned = s.lstrip("/# ").strip()
            if cleaned and not cleaned.startswith(("fmt:", "ruff:", "type:", "eslint", "noqa")):
                comment_lines.append(cleaned)
                if len(comment_lines) >= 1:
                    break
        elif s.startswith("/*") or s.startswith("/**"):
            cleaned = re.sub(r"^/\*+|\*+/$", "", s).strip(" *")
            if cleaned:
                comment_lines.append(cleaned)
                break
        elif comment_lines:
            break
    return _clean_one_line(comment_lines[0], 90) if comment_lines else ""


def _format_py_args(args: ast.arguments) -> str:
    parts: list[str] = []
    pos = list(args.posonlyargs) + list(args.args)
    defaults_offset = len(pos) - len(args.defaults)
    for idx, arg in enumerate(pos):
        piece = arg.arg
        if arg.annotation is not None:
            piece += f": {ast.unparse(arg.annotation)}"
        if idx >= defaults_offset:
            piece += "=…"
        parts.append(piece)
    if args.vararg:
        parts.append(f"*{args.vararg.arg}")
    elif args.kwonlyargs:
        parts.append("*")
    for kw, kw_def in zip(args.kwonlyargs, args.kw_defaults):
        piece = kw.arg
        if kw.annotation is not None:
            piece += f": {ast.unparse(kw.annotation)}"
        if kw_def is not None:
            piece += "=…"
        parts.append(piece)
    if args.kwarg:
        parts.append(f"**{args.kwarg.arg}")
    joined = ", ".join(parts)
    return joined if len(joined) <= 56 else joined[:53] + "…"


def _format_py_func(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    ret = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    return _clean_one_line(f"{prefix} {node.name}({_format_py_args(node.args)}){ret}")


def outline_python(text: str) -> list[SymbolItem]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return fallback_code_symbols(text.splitlines(), ".py")

    items: list[SymbolItem] = []
    for node in tree.body:
        start = getattr(node, "lineno", 1)
        end = getattr(node, "end_lineno", start)
        if isinstance(node, ast.ClassDef):
            bases = ", ".join(ast.unparse(b) for b in node.bases)
            base_str = f"({bases})" if bases else ""
            items.append(SymbolItem(start, end, 0, _clean_one_line(f"class {node.name}{base_str}")))
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    c_start = getattr(child, "lineno", start)
                    c_end = getattr(child, "end_lineno", c_start)
                    items.append(SymbolItem(c_start, c_end, 1, _format_py_func(child)))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            items.append(SymbolItem(start, end, 0, _format_py_func(node)))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and (end - start) >= 3:
            target = node.targets[0] if isinstance(node, ast.Assign) else node.target
            if isinstance(target, ast.Name) and target.id.isupper():
                items.append(SymbolItem(start, end, 0, f"{target.id} = <声明块>"))
    return items


def _signature_head(raw_lines: str) -> str:
    """把 AST 节点源码首部裁成干净的单行声明签名。"""
    head_parts: list[str] = []
    for ln in raw_lines.splitlines()[:4]:
        s = ln.strip()
        if not s or s.startswith(("//", "/*", "*", "#[", "@")):
            continue
        head_parts.append(s)
        if "{" in s or "=>" in s or s.endswith(";"):
            break
    joined = " ".join(head_parts) if head_parts else raw_lines.splitlines()[0].strip()
    if "{" in joined:
        joined = joined.split("{", 1)[0].strip()
    if "=>" in joined:
        joined = joined.split("=>", 1)[0].strip() + " => …"
    return _clean_one_line(joined.rstrip("; "), 92)


def run_ast_grep_batch(paths: list[Path]) -> dict[Path, list[SymbolItem]]:
    sg_bin = find_ast_grep_bin()
    if not sg_bin or not paths:
        return {}
    try:
        proc = subprocess.run(
            [sg_bin, "scan", "--inline-rules", AST_GREP_RULES_YAML, "--json=stream", *[str(p) for p in paths]],
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}

    raw_by_file: dict[Path, list[tuple[int, int, int, str]]] = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            fpath = Path(obj["file"]).resolve()
            rng = obj["range"]
            start = int(rng["start"]["line"]) + 1
            end = int(rng["end"]["line"]) + 1
            col = int(rng["start"]["column"])
            sig = _signature_head(obj.get("lines") or obj.get("text") or "")
            if sig:
                raw_by_file.setdefault(fpath, []).append((start, end, col, sig))
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            continue

    result: dict[Path, list[SymbolItem]] = {}
    for fpath, entries in raw_by_file.items():
        entries.sort(key=lambda x: (x[0], -(x[1] - x[0])))
        kept: list[SymbolItem] = []
        containers: list[tuple[int, int]] = []
        seen_starts: set[int] = set()
        for start, end, col, sig in entries:
            if start in seen_starts:
                continue
            containers = [c for c in containers if c[1] >= end]
            depth = len(containers)
            # 只保留顶层符号和类/impl 下一层直接成员，过滤函数体内部的局部箭头函数噪声
            if depth > 1 or (depth == 1 and col > 8):
                continue
            seen_starts.add(start)
            kept.append(SymbolItem(start, end, depth, sig))
            if any(sig.startswith(k) or f" {k} " in f" {sig} " for k in ("class", "interface", "impl", "trait", "protocol", "struct", "object", "enum")):
                containers.append((start, end))
        result[fpath] = kept
    return result


DECL_PATTERNS = [
    re.compile(r"^(\s*)(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function|class|interface|type|enum)\s+[A-Za-z_$][\w$]*"),
    re.compile(r"^(\s*)(?:export\s+)const\s+[A-Za-z_$][\w$]*(?:\s*:[^=]+)?\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>"),
    re.compile(r"^(\s*)(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:fn|struct|enum|trait|impl|type|mod)\s+[A-Za-z_][\w]*"),
    re.compile(r"^(\s*)(?:func|type)\s+(?:\([^)]+\)\s*)?[A-Za-z_][\w]*"),
    re.compile(r"^(\s*)(?:public|private|protected|internal|open|abstract|final|static|\s)*(?:class|interface|enum|record|struct|protocol|object|fun|func)\s+[A-Za-z_][\w]*"),
    re.compile(r"^(\s*)(?:async\s+)?def\s+[A-Za-z_][\w]*|^\s*class\s+[A-Za-z_][\w]*"),
]


def _estimate_block_end(lines: list[str], start_idx: int) -> int:
    brace_depth = 0
    saw_brace = False
    for idx in range(start_idx, min(len(lines), start_idx + 500)):
        ln = lines[idx]
        opens = ln.count("{")
        closes = ln.count("}")
        if opens > 0:
            saw_brace = True
        brace_depth += opens - closes
        if saw_brace and brace_depth <= 0:
            return idx + 1
        if not saw_brace and idx > start_idx and ln.strip().endswith(";"):
            return idx + 1
    return start_idx + 1


def fallback_code_symbols(lines: list[str], suffix: str) -> list[SymbolItem]:
    items: list[SymbolItem] = []
    for idx, raw in enumerate(lines):
        if len(raw) - len(raw.lstrip()) > 4:
            continue
        for pat in DECL_PATTERNS:
            m = pat.match(raw)
            if m:
                indent = 1 if len(m.group(1)) >= 2 else 0
                end = idx + 1 if suffix in (".py", ".pyi") else _estimate_block_end(lines, idx)
                items.append(SymbolItem(idx + 1, end, indent, _signature_head(raw)))
                break
    return items


def outline_markdown(lines: list[str]) -> list[SymbolItem]:
    headings: list[tuple[int, int, str]] = []
    in_fence = False
    fence_marker = ""
    for idx, raw in enumerate(lines):
        stripped = raw.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not in_fence:
                in_fence, fence_marker = True, marker
            elif stripped.startswith(fence_marker):
                in_fence = False
            continue
        if in_fence:
            continue
        m = re.match(r"^(#{1,3})\s+(.+?)\s*$", raw)
        if m:
            level = len(m.group(1))
            headings.append((idx + 1, level, _clean_one_line(f"{m.group(1)} {m.group(2)}", 88)))

    items: list[SymbolItem] = []
    total = len(lines)
    for i, (start, level, title) in enumerate(headings):
        end = total
        for next_start, next_level, _ in headings[i + 1 :]:
            if next_level <= level:
                end = next_start - 1
                break
        items.append(SymbolItem(start, max(start, end), max(0, level - 1), title))
    return items


def outline_json(text: str, lines: list[str]) -> list[SymbolItem]:
    items: list[SymbolItem] = []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return items
    if isinstance(data, list):
        sample_keys = list(data[0].keys())[:8] if data and isinstance(data[0], dict) else []
        desc = f"JSON Array: 共 {len(data)} 项" + (f"（元素字段: {', '.join(sample_keys)}）" if sample_keys else "")
        items.append(SymbolItem(1, len(lines), 0, desc))
    elif isinstance(data, dict):
        key_lines: dict[str, int] = {}
        for idx, raw in enumerate(lines):
            m = re.match(r'^\s{1,4}"([^"]+)"\s*:', raw)
            if m and m.group(1) not in key_lines:
                key_lines[m.group(1)] = idx + 1
        keys = list(data.keys())
        for i, k in enumerate(keys[:35]):
            start = key_lines.get(k, 1)
            end = key_lines.get(keys[i + 1], len(lines) + 1) - 1 if i + 1 < len(keys) and keys[i + 1] in key_lines else len(lines)
            val = data[k]
            vdesc = f"Array[{len(val)}]" if isinstance(val, list) else f"Object[{len(val)}]" if isinstance(val, dict) else type(val).__name__
            items.append(SymbolItem(start, max(start, end), 0, f'"{k}": {vdesc}'))
    return items


def outline_html_or_sql(lines: list[str], suffix: str) -> list[SymbolItem]:
    items: list[SymbolItem] = []
    if suffix == ".sql":
        pat = re.compile(r"^\s*(CREATE\s+(?:OR\s+REPLACE\s+)?(?:UNIQUE\s+)?(?:TABLE|VIEW|INDEX|FUNCTION|TYPE)\s+[^;(]+|ALTER\s+TABLE\s+\S+)", re.IGNORECASE)
        for idx, raw in enumerate(lines):
            m = pat.match(raw)
            if m:
                items.append(SymbolItem(idx + 1, idx + 1, 0, _clean_one_line(m.group(1))))
        return items

    tag_pat = re.compile(r'<\s*(section|main|header|nav|template|script|style|div)\b([^>]*)>', re.IGNORECASE)
    for idx, raw in enumerate(lines):
        for m in tag_pat.finditer(raw):
            tag, attrs = m.group(1).lower(), m.group(2)
            if tag == "div" and not any(k in attrs for k in ("data-view", "data-component", "id=")):
                continue
            label = f"<{tag}>"
            for attr in ("data-view", "data-component", "id"):
                am = re.search(rf'{attr}=["\']([^"\']+)["\']', attrs)
                if am:
                    label += f' {attr}="{am.group(1)}"'
            items.append(SymbolItem(idx + 1, idx + 1, 0, label))
    return items


def extract_symbols_for_file(
    path: Path,
    text: str,
    lines: list[str],
    precomputed_ast: dict[Path, list[SymbolItem]] | None = None,
) -> list[SymbolItem]:
    suffix = path.suffix.lower()
    if suffix in (".py", ".pyi"):
        return outline_python(text)
    if suffix in (".md", ".mdx"):
        return outline_markdown(lines)
    if suffix == ".json":
        return outline_json(text, lines)
    if suffix in (".html", ".htm", ".sql"):
        return outline_html_or_sql(lines, suffix)
    resolved = path.resolve()
    if precomputed_ast is not None and resolved in precomputed_ast:
        return precomputed_ast[resolved]
    if suffix in AST_GREP_LANG_BY_SUFFIX:
        batch = run_ast_grep_batch([resolved])
        if resolved in batch and batch[resolved]:
            return batch[resolved]
    return fallback_code_symbols(lines, suffix)


def format_symbol_lines(items: list[SymbolItem], max_items: int) -> list[str]:
    out: list[str] = []
    for sym in items[:max_items]:
        span = f"L{sym.start_line}" if sym.start_line == sym.end_line else f"L{sym.start_line}-L{sym.end_line}"
        pad = "  " * (sym.indent + 1)
        out.append(f"{pad}[{span}] {sym.signature}")
    if len(items) > max_items:
        out.append(f"  … 其余 {len(items) - max_items} 个符号已省略（可缩小路径或用 `rg -n` 搜索）")
    return out


def outline_file(path: Path, max_symbols: int = 50, display_path: str | None = None) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    kb = len(text.encode("utf-8", errors="replace")) / 1024.0
    label = display_path or str(path)
    summary = extract_top_summary(lines, path.suffix.lower())
    header = f"{label} ({len(lines)} 行, {kb:.1f} KB)" + (f" — {summary}" if summary else "")
    symbols = extract_symbols_for_file(path, text, lines)
    body = format_symbol_lines(symbols, max_symbols)
    if not body:
        preview = [_clean_one_line(ln, 88) for ln in lines[:6] if ln.strip()]
        body = [f"  [L1-L{min(len(lines), 6)}] 首段预览: " + " | ".join(preview)] if preview else ["  （空文件）"]
    return "\n".join([header, *body])


def _is_scannable_file(path: Path) -> bool:
    if path.name in IGNORE_FILENAMES or path.name.startswith("."):
        return False
    if path.name.endswith((".min.js", ".min.css", ".d.ts.map", ".js.map")):
        return False
    if path.suffix.lower() in IGNORE_SUFFIXES:
        return False
    try:
        if path.stat().st_size > 512_000:
            return False
        with path.open("rb") as fh:
            chunk = fh.read(2048)
            if b"\x00" in chunk:
                return False
    except OSError:
        return False
    return True


def collect_directory_files(root: Path, max_depth: int = 4) -> list[Path]:
    collected: list[Path] = []

    def _walk(cur: Path, depth: int):
        if depth > max_depth:
            return
        try:
            entries = sorted(cur.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except OSError:
            return
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name not in IGNORE_DIRS and not entry.name.startswith("."):
                    _walk(entry, depth + 1)
            elif entry.is_file() and _is_scannable_file(entry):
                collected.append(entry)

    _walk(root, 1)
    def _file_priority(p: Path) -> tuple[int, str]:
        rel = p.relative_to(root)
        rel_str = rel.as_posix()
        parts_lower = {part.lower() for part in rel.parts[:-1]}
        name_lower = p.name.lower()
        if p.name in ("AGENTS.md", "README.md") and len(rel.parts) == 1:
            return (0, rel_str)
        if (
            parts_lower & {"generated", "__generated__", "gen"}
            or ".gen." in name_lower
            or ".generated." in name_lower
        ):
            return (3, rel_str)
        if (
            parts_lower & {"tests", "test", "__tests__", "migrations", "alembic"}
            or name_lower.startswith("test_")
            or ".test." in name_lower
            or ".spec." in name_lower
        ):
            return (2, rel_str)
        if p.name in ("AGENTS.md", "README.md"):
            return (0, rel_str)
        return (1, rel_str)

    collected.sort(key=_file_priority)
    return collected


def map_target(target: Path, max_lines: int = 180, max_depth: int = 4) -> str:
    resolved = target.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"路径不存在：{target}")
    if resolved.is_file():
        return outline_file(resolved, max_symbols=max(40, max_lines - 5), display_path=str(target))

    files = collect_directory_files(resolved, max_depth=max_depth)
    if not files:
        return f"{target}：未发现可索引的源码或文档文件。"

    ast_candidates = [f.resolve() for f in files if f.suffix.lower() in AST_GREP_LANG_BY_SUFFIX]
    precomputed = run_ast_grep_batch(ast_candidates) if ast_candidates else {}

    output_lines = [f"# 代码骨架地图：{resolved}（共扫描 {len(files)} 个文件）"]
    shown_files = 0
    for fpath in files:
        rel = fpath.relative_to(resolved).as_posix()
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        summary = extract_top_summary(lines, fpath.suffix.lower())
        file_hdr = f"\n- {rel} ({len(lines)} 行)" + (f" — {summary}" if summary else "")
        symbols = extract_symbols_for_file(fpath, text, lines, precomputed_ast=precomputed)
        sym_lines = format_symbol_lines(symbols, max_items=16)

        projected = len(output_lines) + 1 + len(sym_lines)
        if projected > max_lines and shown_files > 0:
            remaining = len(files) - shown_files
            output_lines.append(
                f"\n… 已达 {max_lines} 行输出上限，其余 {remaining} 个文件未展开；请指定子目录（如 `ws map <subdir>`）或调大 `--max-lines`。"
            )
            break
        output_lines.append(file_hdr)
        output_lines.extend(sym_lines)
        shown_files += 1

    return "\n".join(output_lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成文件或目录的 AST 符号与章节行号骨架图")
    parser.add_argument("target", nargs="?", default=".", help="目标目录或文件路径（默认当前目录）")
    parser.add_argument("--max-lines", type=int, default=180, help="最大输出行数（默认 180）")
    parser.add_argument("--depth", type=int, default=4, help="目录递归最大深度（默认 4）")
    args = parser.parse_args(argv)
    try:
        print(map_target(Path(args.target), max_lines=args.max_lines, max_depth=args.depth))
        return 0
    except FileNotFoundError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
