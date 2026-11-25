# 跨项目高频错法模式卡索引（Failure Playbooks Index）

> 由 `slice-build`（设计技术方案前）、`systematic-debugging`（排障溯源时）与 `adversarial-review` 按需检索。**命中触发场景时才精读对应薄卡**，平时不整库吞读。容量受 `ws learn check` 硬守卫（本索引 ≤35 非空行，总卡 ≤10 张，单卡 ≤45 非空行；同一错法犯第二次须毕业编译进 `guards/` 或共用件并从本表退役）。

| 编号 | 错法模式卡 | 当你写或排查这类逻辑时必读（触发场景） |
| :--- | :--- | :--- |
| **F01** | [F01-untrusted-input-quarantine.md](./F01-untrusted-input-quarantine.md) | 解析外部 API、用户输入、爬虫或大模型返回结果并校验落库/审计时 |
| **F02** | [F02-retry-storm-and-watermark.md](./F02-retry-storm-and-watermark.md) | 设计轮询状态接口、异常自动重试/自愈任务、多通道备用降级或共享任务队列时 |
| **F03** | [F03-stochastic-ai-and-flaky-bugs.md](./F03-stochastic-ai-and-flaky-bugs.md) | 接入概率性大模型输出、优化提示词/结构化提取，或排查偶发不复现 Bug 与并发竞态时 |
| **F04** | [F04-async-self-cancel-and-eviction-race.md](./F04-async-self-cancel-and-eviction-race.md) | 编写异步单飞协调器/取消收尾逻辑、外发提交后刷新对账，或消费多通道乱序生命周期事件做结算归因时 |
| **F05** | [F05-data-growth-and-read-amplification.md](./F05-data-growth-and-read-amplification.md) | 引入数据湖/扩展自建表、分区分批历史回补、组装长文本/大表只读视图或优化前端首屏与缓存时 |
| **F06** | [F06-controlled-plugin-sandbox.md](./F06-controlled-plugin-sandbox.md) | 实现受控自定义 Python 插件热加载、AST 纯度白名单、`sys.settrace` 步数预算或脚本干跑校验时 |
| **F07** | [F07-test-suite-bloat-and-slow-gate.md](./F07-test-suite-bloat-and-slow-gate.md) | 编写/优化含数据库、数据湖或长窗口历史回补的测试套件，或设计 `scripts/check` 分层与全量门禁时 |
| **F08** | [F08-blacklist-arms-race-and-review-loops.md](./F08-blacklist-arms-race-and-review-loops.md) | 实现安全拦截/命令策略解析，或零上下文对抗审查出现多轮未收敛与补丁膨胀时 |
| **F09** | [F09-parser-differential-and-ops-guardrails.md](./F09-parser-differential-and-ops-guardrails.md) | 编写部署发布脚本、容器编排、环境变量/连接串/路径边界校验或运维诊断 CLI 时 |
