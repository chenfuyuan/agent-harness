@AGENTS.md

# Claude Code 桥接说明

上方导入的 `AGENTS.md` 为唯一协作规则真源（Antigravity、Claude Code、Codex 共用）。
- 技能目录 `.claude/skills` 软链接指向 `.agents/skills`，统一在 `harness/skills/` 维护。
- 联网检索优先使用已配置的全局 MCP（如 Tavily / Context7）或内置搜索工具。
