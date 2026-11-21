<!-- HARNESS:START (由 agent-harness/bin/ws 自动维护，请勿手改此区块) -->
## 双模态工作流宪章（Design ↔ Build）

1. **阶段门禁与信任阶梯并行开线（硬约束）**：
   - **0→1 新仓与新大模块（`Mode: design`）**：严禁读写 `design/` 以外业务代码、严禁 AI 篡改钩子或自解锁；拍板原型与首片后解锁编码（无提交首片在主目录 `ws mode build --slice <path>`，首提锁基线后切回 `ws mode design` 守门）。
   - **1→N 双车道并行开发（`ws line` 主仓常驻 `design`，按风险分流）**：
     - **重验收档（`--lane full`，默认）**：适用于新功能、UI 变更、数据正本或资金风控改动。**开工前**须以领域架构师视角点破暗坑、对标业内最佳实践，给出 2–3 套「业务范围 × 代码架构」方案（推荐置顶）并停下等用户选定，落盘切片单（`approved`）后开线 `ws line new <name> --slice <path>`；**合流前**过全量门禁与零上下文审查，拉起 `scripts/dev` 并弹 HTML 图文验收报告经用户真机回复「验收通过」，再依次 `ws line distill` → `ws line sync` → `ws line rm`。
     - **快车道档（`--lane fast`）**：仅限**纯内部重构、补测试、收紧 `.harness-rules.json` 规则或局部小 Bug 治本修复**（严禁偷塞新 UI 或改数据正本）。免多方案比选与 HTML 截图仪式，直接 `ws line new <name> --lane fast`；跑绿全量门禁 + 属性/回归测试 + 零上下文审查 `0 Blocker` 后，向用户呈现一屏验收摘要确认，再依次 `distill` → `sync` → `rm`。
     - 凡触及新增子系统、全新视图或变更权威数据正本，必须切回 `ws mode design`。
2. **最小上下文接续与漏斗式读取**：新会话先读本文件 → `design/SYSTEM.md`（及 `build` 态的 `design/TECH.md`、`.harness-rules.json`）→ `Active-Slice` 汇报接续点；摸底先跑 `<Hub-Ref>/bin/ws map <dir>` 或 `rg -n` 再按行段精读（`read_guard` 自动拦截 >400 行盲读）；跨目录盲找 >8 个未知文件派只读子代理（≤15 文件），将改代码须主会话亲读原文。
3. **业务探索、个性化原型与工程架构（`Mode: design`）**：按 `explore-architect`（模糊/流程自动化需求先反向采访剥离目标与工序、明确 Anti-Job）锁 `design/SYSTEM.md`，按 `bespoke-prototype` 定制 `DESIGN.md`、`tokens.css` 与零构建原型（纯后端/数据跳过，禁套后台模板），按 `tech-architect` 拆解标杆代码架构产出 `design/TECH.md` 并在 `Slice 00` 解锁 `build` 时将分层与高危 API 禁令落盘至项目根 `.harness-rules.json`。
4. **确定性花园编码、治本排障与 Checks-over-Notes 进化（`Mode: build`）**：按 `slice-build` 推进（表结构只加不删；分层依赖、公开入口与高危 API 封禁统一收口至 `.harness-rules.json` 由 `ws check` 硬拦，全仓禁裸 `@ts-ignore`/`# type: ignore`/`# noqa` 消音；仅复杂核心模块按需留 ≤20 行 / 硬上限 25 行 `src/<mod>/AGENTS.md` 写纯业务不变量，源码头禁写函数清单；算账与状态机配手算 TDD + 属性测试，严守最小改动半径）；排障按 `systematic-debugging` 零改动取证、录带回放与「治本四校」；收尾按 `compound-learn` 将反复踩坑 30 秒毕业进 `.harness-rules.json` 并删除旧口头叮嘱，同时经 `ws learn` 清偿豁免债与过期 `stub-ok`。
5. **沟通与安全**：中文沟通，底层实现琐事（变量命名、微调间距等双向门）不打扰用户；**每次新需求/下一阶段方案比选与架构单向门**在正文讲清领域本质、业内标杆做法、代码架构映射与利弊取舍（推荐置顶）后请用户文字拍板；用户附带新疑问时先评审收敛再改代码。严禁密钥入库，未经明确指令不自动 `git commit/push`、不执行真实资金操作。
<!-- HARNESS:END -->
