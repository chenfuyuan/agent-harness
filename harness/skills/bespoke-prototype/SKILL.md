---
name: bespoke-prototype
description: >-
  当新项目或新大模块需要确立视觉方向、生成 `design/DESIGN.md` 与 `tokens.css`、制作或迭代零构建个性化交互原型（`design/prototype/`）时激活。
  内化反 AI 廉价感（Anti-Slop）审美体系，彻底废除固定后台模板，让原型精准指导后续前端开发。
---

# 个性化高拟真原型工程（Bespoke Prototype & Design Taste）

每个系统都应长出契合其业务本质的独特界面形态，而非千篇一律的“左侧菜单 + 右侧卡片列表”后台管理系统。本技能产出**项目专属 `design/DESIGN.md` + 本地 `tokens.css` + 零构建交互原型**，直接作为后续 `Mode: build` 的前端视觉与交互基准。

## 1. 动笔前必做的“反模板化”艺术指导承诺（Art Direction）

写任何 HTML/CSS 前，先结合 `explore-architect` 的标杆拆解，向用户明确 **空间拓扑** 与 **审美三旋钮**（吸收自 `taste-skill` 与 `frontend-design`）：

1. **选择业务原生的空间拓扑（严禁无脑默认左侧栏或套错领域壳子！）**：
   - **视觉舞台 + 分镜画册（`Media Stage + Filmstrip / Gallery`）**：AI 漫剧、视频剪辑、摄影审片、设计工具（中央大尺寸预览舞台 + 大幅画格长卷/画册墙，画面占屏 ≥60%，对标 LTX Studio / Frame.io / 剪映故事板）。
   - **画布中心 + 悬浮坞（`Canvas-First + Floating Dock`）**：知识图谱、节点编排、无限白板工具（对标 Figma / Excalidraw）。
   - **命令驱动极简流（`Command-First + Split Drawer`）**：高频效率与研发工具（窄顶栏/`Cmd+K` + 主列表 + 右侧粘性上下文抽屉，对标 Linear）。
   - **沉浸单栏画卷（`Editorial / Focus Stream`）**：AI 小说写作阅读、深度研报长文（对标 Claude / Readwise）。
   - **聚焦向导或移动视口（`Focused Flow / 390px Mobile Frame`）**：C 端应用、轻量独立工具、电商前台组件。
   - **全屏高密数据座舱（`100vh Data Cockpit`）**：**仅限**量化交易终端、实时运维监控等纯数字高频盘口，非数字监控类产品严禁滥用。
2. **设定三个审美旋钮（1–10 档）**：
   - `VISUAL_DENSITY`（信息密度）：数据监控终端取 `8–9`（紧凑等宽）；影视/设计/日常生产力工具取 `5–6`（图文均衡、画面呼吸感）；阅读/消费产品取 `3–4`（充裕留白）。
   - `DESIGN_VARIANCE`（布局破格度）：精密仪器取 `2–4`（克制网格）；创意工作室/独立站取 `7–9`（画册张力、编辑排版）。
   - `MOTION_INTENSITY`（动效克制度）：高频生产力工具取 `2–3`（瞬时响应微反馈）；影视/消费展示产品取 `5–7`。
3. **绝对反模式禁令（Anti-Slop & Anti-Bleed Rules）**：
   - **严禁跨领域串味（杜绝把一切工具画成“炒币看盘终端”）**：内容创作、影视漫剧、阅读与消费类产品严禁套用冷蓝黑四宫格细线表、满屏 `JetBrains Mono` 参数胶囊、红绿涨跌色块与顶部“账户余额”栏；视觉创作类产品必须让大幅作品画面成为绝对主角（占主视野 ≥60%）。
   - 严禁白底配廉价蓝紫渐变标题、严禁随手堆砌 Emoji 图标卡片、严禁“卡片套带边框小卡片（Nested Bordered Cards）”（改用背景色阶 `--bg-surface-1/2` 或 `1px` 极淡分割线分层）、严禁同一操作区出现多个同权重主按钮。

## 2. 四步出图工作流（主屏比稿 → 锁基因 → 铺核心流 → 截图精修）

### Step 1：主屏视觉比稿（解决“做完一整套才发现风格不对味”）
- 全新系统起步时，**绝不一口气铺开画 5 个页面**。只围绕**最核心的 1 张主屏**开展：
  - 若用户已指定明确对标风格：直接生成 1 版高拟真主屏并截图确认；
  - 若风格尚未锁定：参考 [aesthetic-seeds.md](./references/aesthetic-seeds.md)，快速生成 **2 个布局与视觉气质截然不同的主屏样本**（`variant-a.html` vs `variant-b.html`），跑浏览器截图带用户直观挑选。

### Step 2：沉淀项目专属 `design/DESIGN.md` 与 `tokens.css`
- 用户选定主屏方向后，删除落选变体，按 [design-md-spec.md](./references/design-md-spec.md) 固化本项目的 **`design/DESIGN.md`** 与 **`design/prototype/tokens.css`**。
- **跨阶段价值**：进入 `Mode: build` 时，正式前端直接 `import` 这份 `tokens.css`，后续 1→N 新增页面无需再画静态 HTML 原型，读 `DESIGN.md` 即可延续一致审美。

### Step 3：展开本切片关联交互视图与零侵入状态胶囊
- **技术护栏（保证双击 `file://` 秒开）**：使用本地 `tokens.css` + 普通 `<script src="...">`（不使用在 `file://` 下会触发浏览器 CORS 拦截的 `<script type="module">` 或本地 `fetch()`；不依赖断网即崩的外部 Tailwind CDN）。
- **面向 AI 开发的组件标记**：在核心功能区标注语义属性（如 `data-component="PreviewStage"`、`data-component="InspectorDrawer"`），方便 Build 阶段直接拆分为前端组件。
- **零侵入状态切换（Shadow DOM 隔离）**：`<main id="app">` 保持 100% 纯净产品界面，不塞任何评审菜单；在 `</body>` 前配置 `window.PROTO_SPEC` 并引入已由 `ws` 同步至同目录的 `<script src="./dev-overlay.js"></script>`，提供**正常态 / 空状态 / 异常告警态**一键切换与规格简注（`Alt+D` 可隐藏）。

### Step 4：自动检查与 `distill` 截图精修
- 交付前运行（比稿期会自动检查目录下全部 `variant-*.html`，定稿期测入口或加 `--all` 测全站）：
  `node <Hub-Ref>/harness/guards/check_prototype.mjs <原型目录> --shots /tmp/proto-shots`
- **必看截图自审（吸收 `impeccable` 精修三刀）**：AI 必须亲自读取生成的 PNG 截图检视整体视觉——① **Distill（去噪）**：删掉多余边框、重复副标题与无意义装饰徽章；② **Clarify（文案）**：按钮写清“动词+结果”（如“合成本集样片”而非含糊“提交”）；③ **Harden（加固）**：确认空状态有引导出口、极端长文本或数值不撑裂容器。
