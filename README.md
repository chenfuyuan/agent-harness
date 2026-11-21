# 通用 AI 工程工作流母仓（Agent Harness Hub）

面向全生命周期研发的**通用 AI 驱动工作流母仓**（当前内置**领域架构 → 个性化原型 → 切片开发**完整闭环，并支持按技能包扩展部署发布、运维排障、数据分析等领域方法论），同时兼容 **Antigravity (`agy`)**、**Claude Code** 与 **OpenAI Codex**。

- **架构演进与核心设计决定**：见 [BLUEPRINT.md](./BLUEPRINT.md)
- **跨项目全局工程与安全底线**：见 [global-principles.md](./harness/rules/global-principles.md)
- **本机私有跨仓地图模板（可选）**：见 [PORTFOLIO.example.md](./PORTFOLIO.example.md)（复制为 `PORTFOLIO.local.md` 使用，默认不入库）
- **开源协议**：[MIT License](./LICENSE)

---

## 一、核心理念

1. **通用引擎与私有实例解耦**：母仓只沉淀零业务硬编码的通用技能（Skills）、安全与质量门禁（Guards）及同步工具（`bin/ws`），可无缝赋能个人工具、商业 SaaS、开源库或团队协作项目。
2. **单项目仓闭环，零跨仓搬运税**：产品研发项目在同一仓库内包含 `design/`（系统骨架 + 视觉基因 + 个性化原型 + 切片工单）与 `src/`（生产代码），由 **`Mode: design | build` 阶段门禁**（事前拦截 + 事后工作区核对 + 提交兜底，见下方“三道防线”）防止讨论阶段抢跑误改业务代码。
3. **零模板个性化原型先行**：彻底废除固定“左侧栏后台外壳”；根据业务本质选空间拓扑、做主屏视觉比稿，沉淀专属 `design/DESIGN.md` 与 `tokens.css` 持续指导后续真实前端还原。
4. **顺应 AI 特性的确定性工程防护（Checks over Notes）**：单技能软链接零拷贝共享；模块公开入口、单向分层禁令与高危 API 封禁统一收口在项目根 `.harness-rules.json`，由 `harness_guard.py` 全仓强执（同时拦截裸 `@ts-ignore`/`# type: ignore`/`# noqa` 消音、未备案 `TODO/mock` 与过期 `stub-ok` 占位债）；仅复杂核心模块按需维护极薄 `src/<mod>/AGENTS.md`（≤20 行 / 硬上限 25 行）；内置多语言 AST 代码骨架地图（`ws map`）与双端大文件吞读守卫（`read_guard.py`）；支持信任阶梯双车道（`ws line new --lane full|fast`）、属性自验证与零上下文 Diff 审查。

---

## 二、快速开始（Quick Start）

### 环境要求
- **Python 3.9+**（`bin/ws` 与核心安全/门禁检查器均基于 Python 标准库，零三方 Python 依赖）
- **Git**（支持 `git worktree` 与 `core.hooksPath`）
- **Node.js + Chrome / Chromium（可选）**：仅用于原型浏览器自检（`harness/guards/check_prototype.mjs`）及完整回归测试中的无头浏览器用例

### 安装与自检
```bash
# 1. 克隆母仓
git clone https://github.com/chenfuyuan/agent-harness.git ~/code/agent-harness
cd ~/code/agent-harness

# 2. （可选）安装原型浏览器自检依赖（playwright-core）
npm --prefix harness/guards install

# 3. （可选）若在本地演进母仓自身，启用预提交密钥扫描与回归钩子
git config core.hooksPath .githooks

# 4. 环境体检与母仓回归测试
./bin/ws doctor
python3 tests/test_harness.py
```

> 建议将 `~/code/agent-harness/bin` 加入 `PATH`（或直接通过 `./bin/ws` 调用），即可在任意新旧项目目录中使用 `ws init` 挂载工作流。

---

## 三、常用命令（`bin/ws`）

```bash
# 1. 初始化新项目仓（可传目标路径，也可进入新建目录后直接运行默认初始化当前目录）
./bin/ws init ~/code/my-app --name "示例应用"
# （或在新建的项目目录内直接运行：ws init --name "示例应用"）

# 2. 原型拍板后解锁首片地基；首提锁基线后切回 design 并用 ws line 开启双车道并行开发线
./bin/ws mode build ~/code/my-app --slice design/slices/00-foundation.md
./bin/ws line new 01-order ~/code/my-app --slice design/slices/01-order.md --scope src/orders
./bin/ws line new fix-calc ~/code/my-app --lane fast --scope src/domain
./bin/ws line list ~/code/my-app
./bin/ws line check ~/code/my-app
./bin/ws line sync 01-order ~/code/my-app
./bin/ws line rm 01-order ~/code/my-app

# 3. 按需生成目录或大文件的 AST 类/函数/章节行号骨架图（省上下文防盲读）
./bin/ws map ~/code/my-app/src

# 4. 开发期运行全套质量与安全门禁；全量通过后签发共享代码树凭证，同代码免重跑、发版前秒级核验（配合云端零容器节流 CI）
./bin/ws check ~/code/my-app --record-full
./bin/ws check ~/code/my-app --full-covered
./bin/ws check ~/code/my-app --verify-full HEAD

# 5. 查看与校验模块契约及母仓错法模式卡（playbooks/）经验水位
./bin/ws learn status ~/code/my-app
./bin/ws learn check ~/code/my-app

# 6. 母仓升级技能或宪章后，一键同步/修复所有已挂载项目
./bin/ws sync
./bin/ws link

# 7. 将 sandbox/<idea> 里的早期探索毕业迁出为独立下游仓库
./bin/ws promote my-idea ~/code/my-new-app

# 8. 环境与三端挂载体检 / 母仓回归测试
./bin/ws doctor
python3 tests/test_harness.py
```

### 阶段门禁的三道防线（`Mode: design` 下）

| 防线 | 时机 | 做什么 | 生效范围 |
| :--- | :--- | :--- | :--- |
| 事前拦截 | 工具调用前 | 文件写入按白名单硬拦（只放行 `design/`、`docs/` 等）；终端命令按词元识别写入目标（重定向、`mkdir/rm/mv/cp/tee`、`sed -i`、`git mv/rm`，并跟踪 `cd`）；改 `AGENTS.md` 会离开 `design` 时升级为人工确认 | Claude Code、agy |
| 事后核对 | 终端命令跑完后 | 比对工作区实际改动，兜住脚本、脚手架等事前看不穿的写入；只上报给 AI 与用户，不自动撤销 | Claude Code（PostToolUse 钩子）；其余工具运行 `ws check` |
| 提交兜底 | `git commit` 前 | 拒绝提交白名单外文件，并扫描密钥 | 三端通用（pre-commit） |

门禁防的是 AI 抢跑，不是沙箱：事前拦不住的写法由后两道防线发现。`ws doctor` 会核实钩子是否真的会被调用，而不只是文件存在。

---

## 四、当前内置核心技能（全闭环研发与自进化包）

| 技能名 | 阶段 | 职责摘要 |
| :--- | :--- | :--- |
| [`explore-architect`](./harness/skills/explore-architect/SKILL.md) | `design` | L1–L3 复杂度自适应分流、四源交叉深挖、面包板单向门决策、生成 `design/SYSTEM.md` |
| [`bespoke-prototype`](./harness/skills/bespoke-prototype/SKILL.md) | `design` | 反后台化空间拓扑、三旋钮审美定调、主屏双变体比稿、生成 `DESIGN.md` + `tokens.css` + 零构建交互原型 |
| [`tech-architect`](./harness/skills/tech-architect/SKILL.md) | `design` | 业内标杆开源代码架构与工程模式深挖（奖章数据流、状态机、同构引擎）、轻量化裁剪、生成可执行 `design/TECH.md` |
| [`slice-build`](./harness/skills/slice-build/SKILL.md) | `build` | 切片级技术方案设计（联动 `playbooks/` 防坑）、子代理隔离真数据探针、模块就近 `AGENTS.md`、算账 TDD 与务实 UI 还原 |
| [`systematic-debugging`](./harness/skills/systematic-debugging/SKILL.md) | 排障治本 | 零改动 RED 取证、AI/竞态录带定格回放、违约归属与级联溯源、动手前「治本四校」防症状消音补丁 |
| [`adversarial-review`](./harness/skills/adversarial-review/SKILL.md) | 跨关卡 | 开工前工程师挑刺（Builder Check）+ 交付前零上下文 Diff 审计 + 故障修复治本审查（Cure Audit） |
| [`compound-learn`](./harness/skills/compound-learn/SKILL.md) | 自进化 | 三级经验飞轮（模块 `AGENTS.md` → 母仓 `playbooks/` 错法卡 → 事不过二编译进代码守卫，毕业即删旧字） |

---

## 五、多领域扩展边界（通用内核 + 可选技能包）

后续扩展数据分析、部署发布、运维排障等能力时，遵循**“方法论进母仓、运行态进下游仓”**原则：

- **沉淀在本母仓的内容**：教 AI 标准作业与防呆的通用技能及检查器（如上线前核查与回滚 SOP、线上故障止血流程、数据口径对齐与确定性统计规范）；新增非产品类技能包时，计划通过 `ws init --profile <pack>` 按项目类型选配挂载（**该参数尚未实现**），避免纯数据或纯运维仓库被迫走前端 HTML 原型门禁。
- **另建独立下游仓库的内容**：真实服务器容器编排、网关监控与备份配置（如 `infra-ops` 仓），以及具体业务的数据管道与分析报表工程（如 `data-lab` 仓）。

---

## 六、开源协议

本项目基于 [MIT License](./LICENSE) 开源。
