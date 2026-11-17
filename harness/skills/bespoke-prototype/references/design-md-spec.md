# `design/DESIGN.md` 与 `tokens.css` 编写规范

参考 Google Stitch / `VoltAgent/awesome-design-md` 范式，用精炼 Markdown + 标准 CSS 变量锁住当前项目的独特视觉灵魂。控制在 **80–120 行**，写给原型 AI 与后续前端开发 AI 共同遵守。

## 1. `design/DESIGN.md` 标准章节

```markdown
# Design System: <项目名称>

## 01. 视觉气质与审美旋钮（Vibe & Dials）
- **核心隐喻**：<例如：暗夜精密航空仪表盘 / 克制温暖的独立出版编辑室 / 极速键盘优先工作台>
- **空间拓扑**：<例如：100vh 全屏四舱停靠终端，零整页滚动，无左侧导航栏>
- **三旋钮设定**：`VISUAL_DENSITY: 8/10` · `DESIGN_VARIANCE: 3/10` · `MOTION_INTENSITY: 2/10`
- **默认主题**：<Dark-only / Light-first / 双主题>

## 02. 色彩角色与表面层级（Color & Surfaces）
- 画布底色 `--bg-canvas` / 一级容器 `--bg-surface-1` / 二级内嵌块 `--bg-surface-2` / 悬浮层 `--bg-elevated`
- 分割线 `--border-subtle`（仅用于面板分界，内部子块靠底色差区分，禁止框套框）
- 主强调色 `--accent`（严格限制仅用于唯一主操作按钮与激活焦点）
- 领域语义色（明确业务惯例，例如：加密合约绿涨 `#0ECB81` / 红跌 `#F6465D`；A股反之）

## 03. 字体与排版节奏（Typography）
- 正文字体栈 `--font-sans`、标题字体栈 `--font-display`、数据/代码等宽栈 `--font-mono`
- 字阶层级（核心大数字 / 面板标题 / 正文 / 辅助小字）及 `font-variant-numeric: tabular-nums` 规则

## 04. 空间网格与圆角性格（Spacing & Geometry）
- 基准网格（`4px` 紧凑格 / `8px` 舒展格）、面板内边距、圆角性格（如硬朗工业风 `2px–4px` vs 亲和现代风 `8px–12px`）

## 05. 核心控件与反模式禁令（Component Rules & Don'ts）
- 主按钮、次按钮、数据行悬停/选中态、告警条的具体形态
- 本项目严禁出现的 3 条视觉败笔
```

## 2. `design/prototype/tokens.css` 约定

`tokens.css` 只放 `:root`（及可选 `[data-theme="dark"]`）语义变量与极薄的基础 Reset / 工具类，命名对齐现代前端通用习惯，确保 `Mode: build` 时可直接被 React / Vue / Tailwind 工程 `@import` 零翻译复用：

```css
:root {
  --bg-canvas: #0b0e11;
  --bg-surface-1: #12161c;
  --bg-surface-2: #1b2028;
  --bg-elevated: #232a34;
  --border-subtle: rgba(255, 255, 255, 0.07);
  --text-primary: #eaecef;
  --text-secondary: #848e9c;
  --text-muted: #5e6673;
  --accent: #f0b90b;
  --accent-contrast: #0b0e11;
  --up: #0ecb81;
  --down: #f6465d;
  --warn: #f59e0b;
  --radius-sm: 3px;
  --radius-md: 6px;
  --font-sans: "Inter", -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
  --font-mono: "JetBrains Mono", "SF Mono", Menlo, monospace;
}
.num { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
```
