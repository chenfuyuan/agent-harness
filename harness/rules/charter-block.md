<!-- HARNESS:START (由 agent-harness/bin/ws 自动维护，请勿手改此区块) -->
## 双模态工作流宪章（Design ↔ Build）

1. **阶段门禁与并行开线流转（硬约束）**：
   - **0→1 新仓与新大模块（`Mode: design`）**：严禁创建、修改或删除 `design/` 以外的业务代码，严禁 AI 擅自篡改钩子或自改 `Mode` 解锁；仅在用户拍板原型与切片范围后，经用户确认解锁编码（尚无提交的 `Slice 00` 在主目录执行 `ws mode build --slice <path>`，首提锁基线后切回 `ws mode design` 守门）。
   - **1→N 功能切片与多线并行（`ws line`）**：有 `main` 基线提交后，主目录默认保持干净 `Mode: design`（只做设计与合流）；`Slice 01+` 功能开发与多需求并行通过 `<Hub-Ref>/bin/ws line new <name> --slice <path>`（若项目自带 `scripts/line` 则优先用项目脚本）开独立工作树编码，跑通后 `ws line sync` 过门禁快进合入 `main`、`ws line rm` 收线；单线轻量修 Bug 也可保持 `build` 直改，但**凡触及新增子系统、全新独立视图或变更权威数据正本**，必须先停下提醒切回 `ws mode design`。
2. **最小上下文接续与漏斗式读取**：新会话先读本文件 → `design/SYSTEM.md`（及 `Mode: build` 下的 `design/TECH.md`）→ 当前 `Active-Slice` 指向的切片文件直接汇报接续点；摸底陌生目录先跑 `<Hub-Ref>/bin/ws map <dir>` 或 `rg -n` 定位再并行按行段精读（`read_guard` 自动拦截 >400 行无行号整本吞读并返回 AST 骨架）；跨目录盲找 >8 个未知文件时派只读子代理隔离噪音（单子代理 ≤15 文件），即将修改的代码与核心算式须由主会话亲读原文。
3. **业务设计、原型与工程架构探索（`Mode: design`）**：按 `explore-architect` 锁业务骨架 `design/SYSTEM.md`，按 `bespoke-prototype` 定制 `design/DESIGN.md`、`tokens.css` 与零构建个性化交互原型（纯后端/数据项目可跳过，**严禁套用千篇一律的左侧栏后台模板**），再按 `tech-architect` 拆解业内顶级开源代码架构与工程模式（如奖章数据管道、状态机、同构内核）产出 `design/TECH.md`。
4. **AI 原生切片开发（`Mode: build`）**：按 `slice-build` 推进——复杂切片先定技术方案与子代理真数据探针（**严禁跳过代码架构设计直接照着原型图画皮**）；核心模块维护 ≤40 行（硬上限 45 非空行）就近契约 `src/<mod>/AGENTS.md`，源码文件头仅写 1–2 行职责（禁手写函数清单）；算钱/算指标逻辑先写手算测试；前端直接导入 `tokens.css` 但不做像素级苛刻纠偏；跑绿 `scripts/check`（串联 `ws check` 防密钥泄露、防 `TODO/mock` 假实现、防吞错与模块越权导入）并经 `adversarial-review` 零上下文审 Diff 后交付真用。
5. **沟通与安全**：中文沟通，不为双向门琐事出题；单向门在正文讲清业内做法与取舍后请用户文字拍板；用户附带新疑问时先评审收敛再改代码。严禁密钥入库，未经明确指令不自动 `git commit/push`、不执行真实资金操作。
<!-- HARNESS:END -->
