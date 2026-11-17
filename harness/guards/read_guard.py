#!/usr/bin/env python3
"""大文件整本读取守卫（Read Guard）：拦截无行号范围的大文件全量读取，当场返回 AST / 章节行号骨架。

设计目标：
- 普通中小文件（默认 ≤400 行且 ≤24KB）100% 无感放行，零额外摩擦；
- 已指定合理行段（单次跨度 ≤360 行）的按需精读 100% 放行；
- 对超过阈值的大文件发起整本读取（或单次跨度 >360 行的大跨度吞读）时，拒绝全量塞入上下文，
  并在拦截原因中直接附上由 `code_outline.py` 实时提取的符号/章节 `[Lstart-Lend]` 骨架图，
  引导模型下一步直接带精确行号按段精读。

同时兼容两种运行协议：
1. Antigravity (agy) PreToolUse Hook（stdin 为 camelCase JSON: `toolCall` / `cwd`）
2. Claude Code (cc) PreToolUse Hook（stdin 为 snake_case JSON: `tool_name` / `tool_input` / `cwd`）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import NamedTuple

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from code_outline import outline_file  # noqa: E402

DEFAULT_MAX_LINES = 400
DEFAULT_MAX_BYTES = 24_000
DEFAULT_MAX_SPAN = 360

EXEMPT_FILENAMES = {
    "AGENTS.md",
    "CLAUDE.md",
    "SKILL.md",
}

EXEMPT_BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp",
    ".pdf", ".mp4", ".mov", ".avi", ".mkv", ".mp3", ".wav", ".ogg", ".m4a",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z",
    ".db", ".sqlite", ".sqlite3", ".parquet", ".pyc", ".so", ".dylib", ".dll",
}

CLI_SESSION_PARTS = (
    "/.gemini/",
    "/.gemini-accounts/",
    "/.claude/",
    "/.claude-accounts/",
)

TEMP_PREFIXES = (
    "/tmp/",
    "/private/tmp/",
    "/var/folders/",
    "/private/var/folders/",
)


class ReadRequest(NamedTuple):
    protocol: str  # "agy" | "claude"
    tool_name: str
    raw_path: str
    start_line: int | None
    end_line: int | None
    has_explicit_range: bool
    has_byte_offset: bool


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(50, int(raw))
    except ValueError:
        return default


def _to_int(val) -> int | None:
    if val is None or isinstance(val, bool):
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def parse_agy_request(payload: dict) -> ReadRequest | None:
    tc = payload.get("toolCall") or {}
    name = str(tc.get("name") or "")
    if name not in ("read_file", "view_file"):
        return None
    args = tc.get("args") or {}
    raw_path = str(
        args.get("Path")
        or args.get("AbsolutePath")
        or args.get("file_path")
        or args.get("path")
        or ""
    )
    start = _to_int(args.get("StartLine") if "StartLine" in args else args.get("StartLineNumber"))
    end = _to_int(args.get("EndLine") if "EndLine" in args else args.get("EndLineNumber"))
    byte_offset = _to_int(args.get("ByteOffset"))
    has_range = start is not None or end is not None
    return ReadRequest(
        protocol="agy",
        tool_name=name,
        raw_path=raw_path,
        start_line=start,
        end_line=end,
        has_explicit_range=has_range,
        has_byte_offset=byte_offset is not None and byte_offset > 0,
    )


def parse_claude_request(payload: dict) -> ReadRequest | None:
    name = str(payload.get("tool_name") or "")
    if name != "Read":
        return None
    inp = payload.get("tool_input") or {}
    if inp.get("pages"):
        return None
    raw_path = str(inp.get("file_path") or inp.get("path") or "")
    offset = _to_int(inp.get("offset"))
    limit = _to_int(inp.get("limit"))
    start = max(1, offset) if offset is not None else (1 if limit is not None else None)
    end = (start + limit - 1) if (start is not None and limit is not None and limit > 0) else None
    has_range = offset is not None or limit is not None
    return ReadRequest(
        protocol="claude",
        tool_name=name,
        raw_path=raw_path,
        start_line=start,
        end_line=end,
        has_explicit_range=has_range,
        has_byte_offset=False,
    )


def _inside_project_repo(resolved: Path) -> bool:
    for parent in resolved.parents:
        if (parent / ".git").exists() or (parent / "AGENTS.md").is_file():
            return True
    return False


def _is_exempt_path(resolved: Path, raw_path: str) -> bool:
    if resolved.name in EXEMPT_FILENAMES:
        return True
    if resolved.suffix.lower() in EXEMPT_BINARY_SUFFIXES:
        return True
    candidates = (resolved.as_posix(), os.path.abspath(os.path.expanduser(raw_path)))
    if any(any(seg in c for seg in CLI_SESSION_PARTS) for c in candidates):
        return True
    if any(c.startswith(TEMP_PREFIXES) for c in candidates) and not _inside_project_repo(resolved):
        return True
    return False


def evaluate_read(req: ReadRequest, cwd: str) -> tuple[str, str]:
    if os.environ.get("WS_DISABLE_READ_GUARD") == "1":
        return "allow", ""
    if not req.raw_path:
        return "allow", ""

    base = Path(cwd or os.getcwd()).expanduser()
    raw_p = Path(req.raw_path).expanduser()
    target = (raw_p if raw_p.is_absolute() else (base / raw_p)).resolve()

    if not target.exists() or not target.is_file():
        return "allow", ""
    if _is_exempt_path(target, req.raw_path):
        return "allow", ""

    try:
        raw_bytes = target.read_bytes()
    except OSError:
        return "allow", ""

    # 二进制文件放行交给底层工具自身处理
    if b"\x00" in raw_bytes[:2048]:
        return "allow", ""

    total_bytes = len(raw_bytes)
    text = raw_bytes.decode("utf-8", errors="replace")
    lines = text.splitlines()
    total_lines = len(lines)

    max_lines = _env_int("WS_READ_GUARD_MAX_LINES", DEFAULT_MAX_LINES)
    max_bytes = _env_int("WS_READ_GUARD_MAX_BYTES", DEFAULT_MAX_BYTES)
    max_span = _env_int("WS_READ_GUARD_MAX_SPAN", DEFAULT_MAX_SPAN)

    # 中小文件全量放行
    if total_lines <= max_lines and total_bytes <= max_bytes:
        return "allow", ""

    # 若显式传了行号范围，计算实际要读取的行跨度与片段字节数
    if req.has_explicit_range or req.has_byte_offset:
        eff_start = max(1, req.start_line or 1)
        eff_end = min(total_lines, req.end_line if req.end_line is not None else total_lines)
        if eff_start > total_lines:
            return "allow", ""
        actual_span = max(0, eff_end - eff_start + 1)
        if actual_span <= max_span:
            slice_bytes = sum(len(ln.encode("utf-8", errors="replace")) + 1 for ln in lines[eff_start - 1 : eff_end])
            # 单行超长压缩文件若已带 ByteOffset 分块读取，或片段字节可控（≤40KB），均放行
            if slice_bytes <= 40_000 or req.has_byte_offset:
                return "allow", ""

    outline = outline_file(target, max_symbols=45, display_path=req.raw_path)
    if req.protocol == "agy":
        how_to = (
            f"请改用按段精读：在 `{req.tool_name}` 中传入 `StartLine` 与 `EndLine`（单次跨度 ≤ {max_span} 行），"
            f"或先用 `rg -n` 搜索定位。"
        )
    else:
        how_to = (
            f"请改用按段精读：在 `Read` 中传入 `offset`（起始行）与 `limit`（行数 ≤ {max_span}），"
            f"或先用 `Grep` 搜索定位。"
        )

    if req.has_explicit_range:
        eff_start = max(1, req.start_line or 1)
        eff_end = min(total_lines, req.end_line if req.end_line is not None else total_lines)
        header = (
            f"[Read Guard] 单次读取跨度过大（请求 L{eff_start}-L{eff_end}，共 {max(0, eff_end - eff_start + 1)} 行；"
            f"文件共 {total_lines} 行 / {total_bytes / 1024:.1f} KB）。\n{how_to}"
        )
    else:
        header = (
            f"[Read Guard] 已拦截大文件整本读取（共 {total_lines} 行 / {total_bytes / 1024:.1f} KB，"
            f"直读上限 {max_lines} 行 / {max_bytes // 1000} KB），防止撑爆上下文触发会话压缩。\n{how_to}"
        )

    reason = f"{header}\n\n以下是该文件的实时结构骨架（含 1-based 行号）：\n{outline}"
    return "deny", reason


def main() -> int:
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
        req = parse_agy_request(payload)
        if req is None:
            print(json.dumps({"decision": "allow"}))
            return 0
        decision, reason = evaluate_read(req, cwd)
        if decision == "allow":
            print(json.dumps({"decision": "allow"}))
        else:
            print(json.dumps({"decision": "deny", "reason": reason}, ensure_ascii=False))
        return 0

    # 2. Claude Code (cc) PreToolUse 协议
    if "tool_name" in payload or payload.get("hook_event_name") == "PreToolUse":
        req = parse_claude_request(payload)
        if req is None:
            print(json.dumps({}))
            return 0
        decision, reason = evaluate_read(req, cwd)
        if decision == "allow":
            print(json.dumps({}))
        else:
            print(
                json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
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
