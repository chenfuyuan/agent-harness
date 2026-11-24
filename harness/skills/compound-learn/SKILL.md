---
name: compound-learn
description: >-
  在真故障修复收尾、独立代码评审揪出严重缺陷（Blocker）、用户在对话中纠正 AI 思维盲区，
  或运行 `ws learn` 整理模块契约与错法模式卡时激活，驱动 AI 跨会话越用越聪明。
---

# 三级经验进化飞轮（Compounding Learning & Graduation）

单纯往一份长文档里无限追加“注意事项”，最终只会变成**没人翻的死文档坟场**或**撑爆上下文稀释注意力的噪音**。本技能通过**按需唤醒 + 事不过二强制毕业 + 硬容量淘汰**机制，把实战踩坑转化为零膨胀的永久免疫力。

## 1. 四个自动触发时机

出现以下任一情况时，在任务收尾前主动执行沉淀评估（草拟 **3–5 行**精炼提案请用户点头确认，不长篇大论）：

1. **真故障闭环**：刚走完 `systematic-debugging` 修掉一个线上或测试未覆盖的真实缺陷；
2. **对抗审查拦截**：`adversarial-review` 揪出了主对话遗漏的 `Blocker` 级漏洞；
3. **用户纠偏唤醒**：用户在对话中指出了 AI 方案的思维盲区、浅补丁惯性或领域边界误判；
4. **容量水位预警**：运行 `<Hub-Ref>/bin/ws learn status` 发现某模块 `AGENTS.md` 或母仓 `playbooks/` 接近硬上限。

## 2. 三级沉淀路由与「事不过二」强制毕业铁律

每条新经验必须对号入座放入唯一合适的一级（拒绝多处重复抄写）：

| 层级 | 落点位置 | 适用范围与唤醒机制 | 硬容量与淘汰规则 |
| :--- | :--- | :--- | :--- |
| **Tier 1：模块极薄备忘** | 项目 `src/<mod>/AGENTS.md`（仅复杂核心模块按需建） | 仅放机器静态规则拦不住的纯业务不变量与单测命令；读写该目录按路径唤醒，其他模块 **0 Token 消耗** | 建议 ≤20 行，硬上限 **25 非空行**；公开入口与机械禁区一律移入 `.harness-rules.json` |
| **Tier 2：跨项目错法卡** | 母仓 `<Hub-Ref>/harness/playbooks/Fxx-<slug>.md` | 跨项目通用的高频工程反模式（**严禁混入私有业务表名、本机路径或密钥**）；开发与排障时扫 `INDEX.md` 按场景点读 | `INDEX.md` ≤ **35 非空行**，总卡 ≤ **10 张**，单卡 ≤ **45 非空行**；满额须合并或毕业旧卡 |
| **Tier 3：代码硬防线（首选终极毕业）** | 项目根 **`.harness-rules.json`**（30 秒零摩擦毕业）、母仓 `harness/guards/` 或 `core` 共用件 | **Checks over Notes 铁律**：凡纠错后追问“下次 AI 再犯谁先拦住”，答案若是人，立刻升级为硬检查——分层越界/禁高危 API 直接加 3 行规则入 `.harness-rules.json`，跨项目通用错法进 `harness_guard.py`（**仅拦结构/语义硬错，绝不对业务代码设硬行数/字数红灯**） | **毕业即删字 + 严禁刷指标**：硬防线生效后立刻删掉旧口头叮嘱；文档容量满额须走「毕业进代码」或请人裁决，**严禁把多条压成超长单行或私自删老规则凑数**；`ws learn check` 同时硬拦过期 `stub-ok` |

## 3. 标准沉淀动作流（与开发线验收闭环联动）

1. **查水位、盘豁免债与附草稿**：运行 `<Hub-Ref>/bin/ws learn status [project_dir]` 一屏查看 Tier 1 行数、Tier 2 错法卡、Tier 3 `.harness-rules.json` 规则数及全仓豁免债（`stub-ok`/`ignore-ok`/`boundary-ok`/`rule-ok`）；在交付验收（`full` 图文报告或 `fast` 一屏摘要）时一并附上 **3–5 行**拟沉淀草稿（优先提议 3 行 `.harness-rules.json` 规则），不在用户确认前提前合入 `main`。
2. **确认后落盘并锁死 `distilled_head`**：待用户回复确认后，在开发线写入规则/沉淀并清理过期占位后提交，运行 `<Hub-Ref>/bin/ws line distill`（内部自动调用 `ws learn check` 核验容量硬顶、规则合法性、过期 `stub-ok` 与公私分离，并签发 `distilled_head` 解锁 `ws line sync`；若改了母仓则加跑 `python3 <Hub-Ref>/tests/test_harness.py` 与 `ws sync`）。
3. **母仓工作流可选自检（默认可为空，严禁凑数）**：项目级沉淀完成后，顺带自检本轮是否在 `agent-harness` 工具、护栏或流程上踩到**跨项目通用的真实卡点或盲区**；无则直接跳过，有则草拟 **≤3 行零业务信息提案**（优先修 `bin/ws` / `guards/` 代码，其次精简改技能），**经用户点头后再改母仓**并跑绿 `python3 <Hub-Ref>/tests/test_harness.py`（改宪章时同步 `ws sync`）。
