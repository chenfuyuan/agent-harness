# 通用 AI 工程工作流母仓（Agent Harness Hub）协作约定

本仓库（`agent-harness`）是跨项目、跨领域的**通用 AI 驱动工程工作流母仓**（沉淀可复用的 Skills、安全门禁 Guards 与多端挂载 CLI），不直接存放具体下游业务、数据分析或服务器运行实例的生产代码。

## 一、两类工作场景

1. **演进工作流引擎与技能包（修改 `harness/` 或 `bin/ws`）**：
   - 保持规则与技能通用、精炼、无私有业务与本机路径硬编码，兼容 **Antigravity (`agy`)**、**Claude Code**、**OpenAI Codex** 三端；新增部署发布、运维排障、数据分析等领域方法论时按可选技能包沉淀于本仓，具体运行态配置与业务数据工程另建独立下游仓库。
   - 修改 `harness/guards/`、`harness/rules/` 或 `bin/ws` 后，必须运行 `python3 tests/test_harness.py` 确保三端门禁、软链接与命令回归全绿，再按需提示运行 `bin/ws sync` 同步已注册项目。
2. **早期项目脑暴孵化（在 `sandbox/<idea>/` 内）**：
   - 未确定是否正式立项的点子可在 `sandbox/<idea>/` 调用 `explore-architect` 做第 1 拍行业深挖与单位经济算账。
   - 决定正式立项时，运行 `bin/ws promote <idea> ~/code/<repo-name>` 迁出为独立下游仓库；不立项则留两行证伪结论或清理目录。

## 二、沟通与安全准则

- **默认中文，初学者友好，先评审后动手**：提出方案前先核查行业真实做法、`harness/rules/global-principles.md` 全局底线（及本机可选的 `PORTFOLIO.local.md` 跨仓边界）；用户提出带新想法或疑问的回复时先客观评审利弊，达成明确共识后再实施。
- **控制单轮信息密度**：单向门决策在对话正文讲清背景、业内做法与取舍（推荐置顶，必要时附 10 行级 ASCII 面包板草图），一次聚焦 1–2 个决定，不弹打断阅读的简短提问框。
- **安全与提交底线**：任何密钥/Token 不进仓库；**仅在用户明确说“提交”时执行 `git commit` / `git push`**，提交前自动触发密钥与自检钩子。
