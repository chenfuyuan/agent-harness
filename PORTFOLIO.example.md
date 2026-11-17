# 跨仓库项目地图模板（Portfolio Example）

> **用法说明**：如需在本机维护多个关联仓库（如主应用、基础设施运维仓、数据分析仓）的职责边界，可执行 `cp PORTFOLIO.example.md PORTFOLIO.local.md` 按实际情况填写。`PORTFOLIO.local.md` 默认被 `.gitignore` 忽略，不会将本机路径或私有业务信息提交入库。

## 一、项目仓库映射表

| 仓库名称 | 本机/远端路径 | 领域类型 | 定位与职责边界 |
| :--- | :--- | :--- | :--- |
| **agent-harness** | `~/code/agent-harness` | 工作流母仓 | 提供跨项目 AI 技能、阶段门禁与安全检查器，不存放业务或实例代码 |
| **my-product-app** | `~/code/my-product-app` | 产品研发 | 核心业务前后端代码与专属 `design/` 原型规格 |
| **my-infra-ops** | `~/code/my-infra-ops` | 部署与运维 | 服务器容器编排、网关、监控告警与备份脚本（与业务代码仓解耦） |
| **my-data-lab** | `~/code/my-data-lab` | 数据分析 | 离线统计脚本、实验 Notebook 与可复现评估报告 |

## 二、组织/个人特定补充约定

- （在此记录只属于你个人或团队的部署偏好、私有基础设施约定或行业特殊合规要求，控制在 20 行以内；通用工程与安全红线统一维护在 `harness/rules/global-principles.md`。）
