---
name: slice-build
description: >-
  当原型与切片范围获用户拍板后，规划垂直切片（`design/slices/NN-xxx.md`）、搭建 `Slice 00` 技术底座与真环境入口、
  执行子代理真数据探针（Spike）或在 `Mode: build` 下实现与验证业务代码时激活。
---

# AI 原生垂直切片开发闭环（Slice Plan & Build）

面向 AI 智能体上下文特性设计：用**模块就近契约**替代冗长历史文档，用**子代理隔离脏探针**保护主窗口质量，用**可执行 `scripts/check`** 阻断半成品交付，确保每一片做完立刻进入真实日常使用。

## 1. 切分原则与开工前准备（切片规划）

1. **最薄可用闭环**：每片控制在 **3–6 个原子步骤**（1–3 次对话内必交付真用），优先切“每天必用 + 验证最危险外部假设”的主干路径。
2. **切换前门禁与开线分流**：确认本片关联的 `SYSTEM.md` 单向门已定、核心界面已在 `design/prototype/` 经用户拍板（若有 UI）、且已通过 `tech-architect` 锁定代码与数据工程架构 `design/TECH.md` → 按 [templates.md](./references/templates.md) 创建 `design/slices/NN-<slug>.md`（含本片技术方案设计） → 调用 `adversarial-review` 做开工前挑刺（Builder Check） → 用户确认开工后按阶段分流：
   - **首片地基 `Slice 00`（仓库尚无初始提交）**：在主目录运行 `<Hub-Ref>/bin/ws mode build --slice design/slices/00-foundation.md` 落地全栈底座；验收通过并经用户确认完成**首次基线提交（Initial Commit）**后，将主目录切回 `ws mode design` 守门。
   - **后续切片 `Slice 01+` 与多需求并行（默认开线）**：主目录保持干净 `Mode: design` 只做设计与合并；运行 `<Hub-Ref>/bin/ws line new <slug> --slice design/slices/NN-<slug>.md [--scope <独占目录>...]`（若项目自带 `scripts/line` 则优先用项目脚本），自动在 `../<项目>-lines/<slug>` 创建 `line/<slug>` 工作树、软链 `.env` 与三端门禁钩子并写入工作树本地 `Mode: build`（免改 `AGENTS.md` 零合并冲突）。多线并行期间可用 `ws line list` / `ws line check` 检查文件碰撞。

## 2. 首次进入 Build 必做：`Slice 00`（架构守卫落地与日常真用入口）

新仓库第一次进入 `Mode: build` 时，先将 `design/TECH.md` 的工程架构转化为可运行的地基与自动化门禁，严禁把部署与分层守卫留到几周后：

1. **工程骨架与静态守卫兑现**：若为 L1 轻量工具尚未建 `TECH.md`，先补齐精简版 `design/TECH.md`；按 `TECH.md` 建立单向依赖目录分层（如纯计算 `domain/` 与 `infra/` 物理隔离）及分层静态检查规则。
2. **三件套脚本与零容器云端节流到位**：
   - `scripts/dev`：一键启动应用；
   - `scripts/check`：一键串联项目纯内存单测/集成测试 + 架构分层守卫 + `<Hub-Ref>/bin/ws check`（全量通过时追加 `--record-full` 签发跨工作树共享代码树硬凭证；打版本/发布脚本前置 `<Hub-Ref>/bin/ws check --verify-full HEAD` 锁死未验代码）；
   - **云端 CI（若配置 `.github/workflows/check.yml`）**：严格遵循 [templates.md](./references/templates.md) 的**“本机全量凭证 + 云端 <1 分钟零容器底线”**模式，仅在 `push tags: ["v*"]`、`pull_request`、`workflow_dispatch` 触发并开启 `concurrency` 取消旧跑，**严禁在 `push: branches: ["**"]` 上重复跑重型容器测试双倍消耗免费配额**；
   - **日常真用入口打通**：把用户本机每天打开它的真实方式（Docker Compose / 本地常驻服务 / 快捷命令）当场装好跑通。

## 3. 每个业务切片的四步 AI 原生执行流

### Step 1：切片技术方案设计与真数据探针（Slice Tech Design & Spike）
- **严禁照着原型图直接翻译面条代码**：涉及状态机、算法、并发流或数据管道的切片，动刀前必先对照业内成熟工程做法与 `design/TECH.md`，在 `slice-NN.md` 写明**本片核心数据结构、状态转移/幂等规则与异常降级策略**。
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
- 每完成一个原子步，跑绿 `scripts/check` 后做一次小颗粒度本地整理（仅在用户要求“提交”时执行 `git commit`）。

### Step 4：零上下文对抗审查与真用刹车（Real-Use Gate）
- 报完工前必做两关：① 本机 `scripts/check` 返回退出码 `0`（无未备案 `TODO/MOCK_DATA`、无吞错）；② 调用 `adversarial-review` 派零上下文子代理（关键资金/正本模块可跨模型 CLI）审 `git diff`。
- 交付后将切片状态标为 `in-real-use`（若在独立开发线中，经用户确认提交后运行 `<Hub-Ref>/bin/ws line sync` 变基过检合入 `main` 并用 `ws line rm <slug>` 收线；若单线在主目录 `Mode: build` 直改，凡后续新增子系统/独立视图/改数据正本须先提醒切回 `ws mode design`）：**在用户实际用过并反馈体感之前，若准备开下一片，必须先提醒确认是否已真用验证。**
