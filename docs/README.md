# 项目设计文档

当前基线：2026-09-08，Pipeline v1。

2026-09-09 接口评审补充（草案，未冻结）：[会议准备稿](interface-freeze-meeting-prep.md)、[完整签名与示例说明](function-contracts-v0.md)、[Python 类型与签名](contracts_v0.py)、[37 个完整输入输出用例](examples/function-contract-cases.json)。这组材料用于团队确认新接口，不自动替代早期架构基线。

| 文档 | 内容 |
|---|---|
| [Pipeline 设计](architecture/pipeline-v1.md) | 用户决策、系统架构、三条流程、数据库边界、可靠性、里程碑；内含五张图 |
| [接口与数据契约](architecture/contracts.md) | Web API、Agent 工具、实时来源、客户偏好、模型排序结果、任务与数据表 |
| [验收与评测](architecture/acceptance.md) | 28 个必测场景、模型评价、性能记录和可交付定义 |
| [图源](architecture/diagrams/) | 五张可编辑 Mermaid 图 |
| [追问子图集成](clarification-integration.md) | DeepSeek 追问、onboarding handoff、PostgreSQL 与 checkpoint 接口 |

已确认：独立网页、实时搜索、业务数据库、定时任务、由模型综合排序。追问 decision
子图和本地 PostgreSQL 持久化已实现；其余业务模块仍按各自里程碑推进。
