# `design/DESIGN.md` 与 `tokens.css` 编写规范

参考 Google Stitch / `VoltAgent/awesome-design-md` 范式，用精炼 Markdown + 标准 CSS 变量锁住当前项目的独特视觉灵魂。控制在 **80–120 行**，写给原型 AI 与后续前端开发 AI 共同遵守。

## 1. `design/DESIGN.md` 标准章节

```markdown
# Design System: <项目名称>

## 01. 视觉气质与审美旋钮（Vibe & Dials）
- **核心隐喻**：<例如：温润的电影分镜审片室 / 克制温暖的独立出版编辑室 / 极速键盘优先工作台>
- **空间拓扑**：<例如：中央成片预览大舞台 + 底部胶片分镜画卷 / 主从双栏画卷，无传统左侧大菜单>
- **三旋钮设定**：`VISUAL_DENSITY: 5/10` · `DESIGN_VARIANCE: 6/10` · `MOTION_INTENSITY: 4/10`
- **默认主题**：<Light-first / Warm-Dark / 双主题切换>

## 02. 色彩角色与表面层级（Color & Surfaces）
- 画布底色 `--bg-canvas` / 一级容器 `--bg-surface-1` / 二级内嵌块 `--bg-surface-2` / 悬浮层 `--bg-elevated`
- 分割线 `--border-subtle`（仅用于区域分界，内部子块靠底色差区分，禁止框套框）
- 主强调色 `--accent`（严格限制仅用于唯一主操作按钮与激活焦点）
- 领域语义色（结合具体领域定制状态色，避免跨领域照搬证券红绿涨跌色）

## 03. 字体与排版节奏（Typography）
- 正文字体栈 `--font-sans`、标题/阅读字体栈 `--font-display`、时间码或局部编号栈 `--font-mono`（注：非纯数字终端切勿满屏滥用等宽字体）
- 字阶层级（主标题 / 分区标题 / 正文 / 辅助说明）及数字对齐规则

## 04. 空间网格与圆角性格（Spacing & Geometry）
- 基准网格（`8px` 舒展创作格 / `4px` 紧凑工具格）、面板留白、圆角性格（如画册温润风 `10px–14px` vs 现代效率风 `6px–8px`）

## 05. 核心控件与反模式禁令（Component Rules & Don'ts）
- 主按钮、次按钮、核心对象卡片/画格选中态、异常提示的具体形态
- 本项目严禁出现的 3 条视觉败笔（含防跨领域串味红线）
```

## 2. `design/prototype/tokens.css` 约定

`tokens.css` 只放 `:root`（及可选 `[data-theme="dark"]`）语义变量与极薄的基础 Reset / 工具类，命名对齐现代前端通用习惯，确保 `Mode: build` 时可直接被 React / Vue / Tailwind 工程 `@import` 零翻译复用。**注意：以下仅为变量命名结构示意，色相、明暗与圆角必须按当前项目真实领域重新调制，严禁直接照抄示例色值！**

```css
:root {
  --bg-canvas: #f6f5f1;
  --bg-surface-1: #ffffff;
  --bg-surface-2: #efede7;
  --bg-elevated: #ffffff;
  --border-subtle: rgba(28, 27, 24, 0.08);
  --text-primary: #1c1b18;
  --text-secondary: #5f5c54;
  --text-muted: #8c887e;
  --accent: #d95d39;
  --accent-contrast: #ffffff;
  --status-ok: #1f8a5b;
  --status-warn: #c87d16;
  --status-danger: #c93b3b;
  --radius-sm: 6px;
  --radius-md: 10px;
  --font-sans: "Plus Jakarta Sans", -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
  --font-mono: "JetBrains Mono", "SF Mono", Menlo, monospace;
}
```
