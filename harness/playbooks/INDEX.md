# 跨项目高频错法模式卡索引（Failure Playbooks Index）

> 由 `slice-build`（设计技术方案前）、`systematic-debugging`（排障溯源时）与 `adversarial-review` 按需检索。**命中触发场景时才精读对应薄卡**，平时不整库吞读。容量受 `ws learn check` 硬守卫（本索引 ≤35 非空行，总卡 ≤10 张，单卡 ≤45 非空行；同一错法犯第二次须毕业编译进 `guards/` 或共用件并从本表退役）。

| 编号 | 错法模式卡 | 当你写或排查这类逻辑时必读（触发场景） |
| :--- | :--- | :--- |
| **F01** | [F01-untrusted-input-quarantine.md](./F01-untrusted-input-quarantine.md) | 解析外部 API、用户输入、爬虫或大模型返回结果并校验落库/审计时 |
| **F02** | [F02-retry-storm-and-watermark.md](./F02-retry-storm-and-watermark.md) | 设计轮询状态接口、异常自动重试/自愈任务、多通道备用降级或共享任务队列时 |
| **F03** | [F03-stochastic-ai-and-flaky-bugs.md](./F03-stochastic-ai-and-flaky-bugs.md) | 接入概率性大模型输出、优化提示词/结构化提取，或排查偶发不复现 Bug 与并发竞态时 |
| **F04** | [F04-async-self-cancel-and-eviction-race.md](./F04-async-self-cancel-and-eviction-race.md) | 编写异步单飞协调器/取消收尾逻辑，或消费多通道乱序生命周期事件做结算归因时 |
