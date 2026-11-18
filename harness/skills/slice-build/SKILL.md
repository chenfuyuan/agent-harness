---
name: slice-build
description: >-
  当原型与切片范围获用户拍板后，规划垂直切片（`design/slices/NN-xxx.md`）、搭建 `Slice 00` 技术底座与真环境入口、
  执行子代理真数据探针（Spike）或在 `Mode: build` 下实现与验证业务代码时激活。
---

# AI 原生垂直切片开发闭环（Slice Plan & Build）

面向 AI 智能体上下文特性设计：用**模块就近契约**替代冗长历史文档，用**子代理隔离脏探针**保护主窗口质量，用**可执行 `scripts/check`** 阻断半成品交付，确保每一片做完立刻进入真实日常使用。

## 1. 领域架构师多方案比选与开工前门禁（严禁单方案抢跑）

每次开启**下一阶段切片**或收到**新功能需求**时（纯局部确定性 Bug 修复除外），哪怕 `design/SYSTEM.md` 已有宏观大纲，**严禁把 AI 当成照单抓药的功能翻译机直接写死单一方案或抢跑开线**：

1. **领域架构师四维推演与多方案比选（Step 0 · 必先停下等用户选）**：
   - **① 领域专家视角点破隐性暗坑**：结合既有 `design/SYSTEM.md`、`design/TECH.md`、`src/*/AGENTS.md` 与 `<Hub-Ref>/harness/playbooks/INDEX.md`，主动识别用户一句话需求背后最易踩死的 **2–3 个领域与工程死穴**（例如量化回测的未来函数/资金费率失真/回测实盘双套代码漂移、并发流的幂等竞态、多工作树共享库迁移兼容等），客观审视前提假设。
   - **② 对标业内顶级开源与工业最佳实践**：检索/参照该子域 2–3 个公认标杆开源项目或成熟工业范式（如交易/回测对标 NautilusTrader、Freqtrade、VectorBT 同构内核；高可靠流状态对标 LMAX 单写者状态机；数据底座对标 Medallion 铜银金分层），剥离大厂重型运维壳，提纯适合当前项目体量的架构内功。
   - **③ 提出 2–3 套「业务范围 × 代码架构」协同方案（拒绝凑数假选项）**：在对话正文清晰呈现 **2–3 套真正成立的差异化方案**（首推项标 `（推荐）`），每套方案必须同时讲透：
     - **业务闭环与厚薄切法**（每片控制在 **3–6 个原子步**，优先切每天高频用 + 验证最危险假设的主干，明确本片做什么与延后什么）；
     - **代码与数据架构映射**（如何落入 `domain/` 纯计算核/状态机 ↔ `app/` 编排 ↔ `infra/` 外部适配与只加不删表迁移，核心不变量与容错降级边界）；
     - **利弊取舍与演进代价**（复杂度、性能、后续扩展弹性对比）。
   - **④ 硬性停顿等待用户选择**：**输出多方案对比后当轮必须到此停下（若先落盘提案草稿，切片状态须标为 `proposed`），严禁在同一轮未获用户选择前顺手执行 `ws line new` 或编写业务代码！**
2. **用户选定后的定稿、挑刺与开线分流**：
   - 待用户明确回复选定方案（或组合微调）后 → 按 [templates.md](./references/templates.md) 将选定方案、核心代码架构与取舍理由落盘至 `design/slices/NN-<slug>.md`，将切片状态更新为 **`approved`** → 调用 `adversarial-review` 做开工前挑刺（Builder Check，核对 `SYSTEM.md` + `TECH.md` 零硬伤） → 按阶段分流：
     - **首片地基 `Slice 00`（仓库尚无初始提交）**：在主目录运行 `<Hub-Ref>/bin/ws mode build --slice design/slices/00-foundation.md` 落地全栈底座；验收通过并经用户确认完成**首次基线提交（Initial Commit）**后，将主目录切回 `ws mode design` 守门。
     - **后续切片 `Slice 01+` 与多需求并行（默认开线，`ws line new` 硬校验 `approved` 状态）**：主目录保持干净 `Mode: design` 只做设计与合并；运行 `<Hub-Ref>/bin/ws line new <slug> --slice design/slices/NN-<slug>.md [--scope <独占目录>...]`（若项目自带 `scripts/line` 则优先用项目脚本；**若切片缺失或仍为 `proposed/draft` 未拍板状态，`ws line new` 将硬性拦截开线**），自动在 `../<项目>-lines/<slug>` 创建 `line/<slug>` 工作树、迁入未入库切片单保持主仓干净、软链 `.env` 与三端门禁钩子并写入工作树本地 `Mode: build`。多线并行期间可用 `ws line list` / `ws line check` 检查文件碰撞。

## 2. 首次进入 Build 必做：`Slice 00`（架构守卫落地与日常真用入口）

新仓库第一次进入 `Mode: build` 时，先将 `design/TECH.md` 的工程架构转化为可运行的地基与自动化门禁，严禁把部署与分层守卫留到几周后：

1. **工程骨架与静态守卫兑现**：若为 L1 轻量工具尚未建 `TECH.md`，先补齐精简版 `design/TECH.md`；按 `TECH.md` 建立单向依赖目录分层（如纯计算 `domain/` 与 `infra/` 物理隔离）及分层静态检查规则。
2. **三件套脚本与零容器云端节流到位**：
   - `scripts/dev`：一键启动应用；接入 `<Hub-Ref>/bin/ws line env`，在并行开发线运行时自动使用槽位偏移端口（`BASE + slot`）与独立容器项目名 `<proj>-<line>`（容器/卷统一打标 `ws.repo=<proj>`、`ws.line=<line>`），默认共用主开发环境真实数据（配合**持久化表结构只加不删**铁律，1 秒开线免空库盲测）；
   - `scripts/check`：一键串联项目纯内存单测/集成测试 + 架构分层守卫 + `<Hub-Ref>/bin/ws check`（全量通过时追加 `--record-full` 签发跨工作树共享代码树硬凭证；打版本/发布脚本前置 `<Hub-Ref>/bin/ws check --verify-full HEAD` 锁死未验代码）；
   - **云端 CI（若配置 `.github/workflows/check.yml`）**：严格遵循 [templates.md](./references/templates.md) 的**“本机全量凭证 + 云端 <1 分钟零容器底线”**模式，仅在 `push tags: ["v*"]`、`pull_request`、`workflow_dispatch` 触发并开启 `concurrency` 取消旧跑，**严禁在 `push: branches: ["**"]` 上重复跑重型容器测试双倍消耗免费配额**；
   - **日常真用入口打通**：把用户本机每天打开它的真实方式（Docker Compose / 本地常驻服务 / 快捷命令）当场装好跑通。

## 3. 每个业务切片的五步 AI 原生执行与真人验收流

### Step 1：切片技术方案设计与真数据探针（Slice Tech Design & Spike）
- **严禁照着原型图直接翻译面条代码**：涉及状态机、算法、并发流或数据管道的切片，动刀前必先对照业内成熟工程做法、`design/TECH.md` 及 `<Hub-Ref>/harness/playbooks/INDEX.md` 高频错法索引，在 `slice-NN.md` 写明**本片核心数据结构、状态转移/幂等规则与异常降级策略**。
- **真数据探针隔离试错**：涉及真实接口、外部协议或历史样本数据时，经用户授权环境配置后，**派独立子代理**编写临时探测脚本跑通真实请求。子代理试错日志不进主上下文，仅将**跑通的真实请求/响应 JSON 样本与字段坑点**写回当前 `slice-NN.md`；若发现真数据推翻原架构假设，立即暂停编码向用户汇报调整。

### Step 2：按模块维护就近契约 `src/<module>/AGENTS.md`（≤40 行）与漏斗式定位
- 创建或重构核心业务模块时，在模块源码目录放置 `AGENTS.md`（格式见 [templates.md](./references/templates.md)），只写源码看不出的 4 件事：**职责与禁区、唯一公开入口、不可破坏的业务不变量/踩坑记录、单模块测试命令**。源码文件顶部只写 1–2 行职责与非显而易见的不变量，**严禁在文件头手写函数清单或行号目录**。
- 摸底存量模块优先运行 `<Hub-Ref>/bin/ws map <dir|file>` 获取实时 AST 符号与 `[Lstart-Lend]` 行号后并行按段精读；跨目录盲找 >8 个未知文件派只读子代理（单子代理 ≤15 文件），即将修改的代码须由主会话亲读原文；若会话经历过自动压缩，编辑前先重读目标位置上下 30 行。
- `agy` 与 Codex 会按路径自动加载该契约；`check_anti_stub.py` 强制守护非空行硬上限 45 行并拦截外部绕过唯一公开入口的深层导入。

### Step 3：原子实现（算账先写测试，前端源头同构不抠像素）
- **计算与资金逻辑 TDD**：凡涉及金额、仓位、止损强平、指标打分、时点过滤的代码，**先用手算具体输入输出写测试并确认红灯失败**，再写实现跑绿。
- **前端务实还原（零像素警察）**：
  - 真实前端工程直接 `import` 原型定稿的 `tokens.css` 变量体系，对照 `design/DESIGN.md` 与原型的 `data-component` 结构拆分为正式组件并绑定真实状态（正常态 / 空态 / 异常态）；
  - **不花时间纠结 2px 字号或微弱色差**；浏览器冒烟只验证 **零 JS 报错、主流程按钮与数据流点得通、无元素严重破版遮挡操作**。
- 每完成一个原子步，跑绿 `scripts/check` 后做一次小颗粒度本地整理（仅在用户要求或进入收尾流转时执行 `git commit`）。

### Step 4：零上下文对抗审查、部署 `dev` 预览与弹出图文验收报告（严禁提前合入 `main`）
- 交付真人验收前必过三关：
  1. **门禁与零上下文审计清零**：本机 `scripts/check` 返回退出码 `0`（无未备案 `TODO/MOCK_DATA`、无吞错），且调用 `adversarial-review` 派零上下文子代理审 `git diff` 达 `0 Blocker`；
  2. **部署开发线 `dev` 预览环境**：在当前开发线目录运行 `./scripts/dev` 拉起本线独立预览服务，实测健康检查与核心页面截图（零控制台报错、空库/真数据诚实展示、挑关键数值在代码外独立重算对账）；
  3. **生成本机 HTML 图文验收报告并弹浏览器**：基于 `<Hub-Ref>/harness/templates/acceptance-report.html` 在主目录生成本机 HTML 验收报告 `docs/acceptance/<日期>-<线名>/report.html`（已默认 `.gitignore` 忽略且免受 `Mode: design` 阻断，含：要你确认的事、**可点击预览链接与要亲手点的步骤**、逐场景截图与独立重算核对、代码评审大白话摘要、**拟沉淀条目草稿**），主动调用 `open` 在浏览器为用户打开，并将切片状态标为 `pending-acceptance`。
- **硬性刹车铁律**：**在用户真机点过并明确回复「验收通过」之前，严禁运行 `ws line distill`、`ws line sync` 或 `ws line rm`，确保 `main` 分支 100% 只包含真人已验收代码！** 若用户提出调整意见，直接在当前开发线修改并刷新 `dev` 预览。

### Step 5：真人确认后一键收尾（沉淀 `distill` → 合入 `sync` → 收线 `rm`）
- 待用户明确回复「验收通过」后，严格按以下物理顺序闭环：
  1. **落盘沉淀与复盘**：在当前开发线按 `compound-learn` 写入经用户确认的经验条目（融合审查拦截项 + 真机验收反馈），回填 `slice-NN.md#4 真用复盘` 并将切片状态标为 `verified`（正式环境投入日常使用后升 `used`），清理临时复现脚手架并完成提交；
  2. **锁死沉淀凭证**：运行 `<Hub-Ref>/bin/ws line distill`（若项目自带 `scripts/line` 则用项目脚本），自动执行 `ws learn check` 并将当前分支 `HEAD` 记入 `distilled_head`；
  3. **快进合入与收线**：运行 `<Hub-Ref>/bin/ws line sync`（硬门禁核验 `distilled_head == HEAD` 后变基过检快进合入 `main`），最后切回主目录运行 `<Hub-Ref>/bin/ws line rm <slug>`（自动销毁该线预览容器/网络/开发镜像并清理工作树）。
