# Build 阶段轻文档模板集（`TECH.md` / `slice-NN.md` / 模块 `AGENTS.md`）

## 1. `design/TECH.md`（全仓技术路由图，控制在 80 行内）

```markdown
# 技术底座与架构边界（TECH）

## 1. 技术栈与运行入口
- **前端 / 后端 / 存储**：...
- **日常真用启动方式**：...
- **标准命令**：开发 `scripts/dev` ｜ 全量门禁 `scripts/check`

## 2. 目录地图与分层依赖铁律
- `src/domain/`：核心业务规则与计算（零外部 I/O 依赖）
- `src/infra/`：外部 API 适配器与持久化实现
- `src/ui/`：界面组件（直接复用 `tokens.css`，只经公开接口读写状态）
- **跨模块红线**：模块间严禁绕过 `index` 公开入口直接引用内部文件。
```

---

## 2. `design/slices/NN-<slug>.md`（单次垂直切片闭环单，用完归档）

```markdown
# Slice NN: <切片名称>

- **状态**：`proposed` | `approved` | `building` | `pending-acceptance` | `verified` | `used`
- **选定方案**：<记录用户从多方案比选中拍板的方案及核心架构取舍摘要；未获用户选择前保持 `proposed` 禁开线>
- **关联设计**：`design/SYSTEM.md#<章节>` · `design/TECH.md#<章节>` · 原型视图 `<view/state>`

## 1. 本片目标、领域暗坑与边界
- **解决什么问题 & 怎样算真用有效**：...
- **领域暗坑与业内最佳实践对标**：<资深领域架构师视角点破的 2–3 个领域死穴 + 对标的顶级开源/工业架构模式>
- **本片明确不做**：...

## 2. 代码与数据架构设计及真数据核验（Code Architecture & Pre-Build Spike）
- **分层职责与核心抽象 / 状态机 / 算式方案**（严格映射 `domain/` 纯计算核 ↔ `app/` 编排 ↔ `infra/` 只加不删迁移，禁照原型硬写面条代码）：...
- **需验证的外部接口/数据假设 & Spike 实测结论（子代理回填）**：<真实字段样例与坑点>

## 3. 原子任务与可执行验收
- [ ] T1: <核心计算手算单测与实现>
- [ ] T2: <真实接口/存储接入>
- [ ] T3: <前端组件还原与正常/空/异常三态联调>
- [ ] T4: `scripts/check` 全绿 + 零上下文 Diff 审查通过 + 真环境可用

## 4. 真用复盘记录（用后 5 分钟回填）
- **实际使用发现 & 对骨架的影响**：...
```

---

## 3. `src/<module>/AGENTS.md`（模块源码就近契约，建议 ≤40 行 / 硬上限 45 非空行）

```markdown
# Module: <模块名>

- **职责与禁区**：负责 ...；绝不负责 ...（归 `<其他模块>`）。
- **唯一公开入口**：外部只准通过 `./index.ts`（或 `./service.py`）调用。
- **核心不变量与踩坑记录（Invariants & Gotchas）**：
  1. <例如：所有下单数量与止损价必须经 `roundToLotAndTick()` 向下截断，严禁四舍五入>
  2. <例如：本模块只追加事件正本，禁止原地 UPDATE 历史成交记录>
- **单模块验证命令**：`pnpm test src/<module>`
```

---

## 4. `scripts/check` 与发布凭证（Slice 00 门禁参考模板）

```sh
#!/bin/sh
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# 1. 运行项目自身单测、集成测试与类型检查（按技术栈替换）
# pnpm --dir "$ROOT" test
# 2. 串联母仓统一门禁，并在全量通过后签发跨工作树共享代码树硬凭证（--record-full；存量仓日常增量可省去该参数并加 --diff）
HUB="${WS_HUB_ROOT:-$(awk -F '\140' '/Hub-Ref[*]*[ ]*:/{print $2; exit}' "$ROOT/AGENTS.md")}"
case "$HUB" in "~/"*) HUB="$HOME/${HUB#"~/"}" ;; /*) ;; *) HUB="$ROOT/$HUB" ;; esac
"$HUB/bin/ws" check "$ROOT" --record-full
# 注：在打标/上线脚本（如 scripts/release）中加一行 `"$HUB/bin/ws" check "$ROOT" --verify-full HEAD` 即可秒级核验本机全量凭证。
```

---

## 5. `.github/workflows/check.yml`（云端零容器节流底线模板，防 CI 配额双烧）

```yaml
# 重型全量集成测试留在本机跑并由 `ws check --record-full` / `--verify-full` 锁死发版；
# 云端不在 push main 上重复启动，仅对版本标签、PR 与手动触发跑 <1 分钟零容器硬底线。
name: check
on:
  push:
    tags: ["v*"]
  pull_request:
  workflow_dispatch:

concurrency:
  group: check-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  guardrails:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v5
        with:
          fetch-depth: 0
      - name: 零容器安全与架构底线检查（按项目纯标准库守卫脚本替换）
        run: |
          python3 tools/check_secrets.py --all
          python3 tools/check_architecture.py
```
