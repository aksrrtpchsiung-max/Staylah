# 文档索引

## 当前实现

- [项目 README](../README.md)：配置、启动和测试命令。
- [项目目录](../项目目录.md)：当前目录结构。
- [模块设计](../模块设计.md)：实际职责与调用流程。
- [模块对应表](../模块对应表.md)：唯一的原模块 / 编号 → 新实现清单。
- [共享契约](../property_agent/contracts.py)：运行时类型的唯一定义；根目录 contracts_v0.py 兼容导出。
- [网页说明](../web/README.md)：页面入口与当前 HTTP API。
- [C 函数说明](../C_EVALUATOR_FUNCTION_GUIDE.md)：检索、评估、复核与路由。
- [追问集成](clarification-integration.md)：追问、onboarding handoff 与 PostgreSQL 接线。
- [重构基线](refactoring/README.md) 与 [验收及回退](refactoring/acceptance.md)。

## 历史资料

`architecture/` 的 Pipeline v1、接口与验收方案是 2026-09-08 的设计材料，包含尚未实现的
SSE、任务队列和定时关注等设想，不是当前 HTTP 或数据库接口定义。
`interface-freeze-meeting-prep.md`、`function-contracts-v0.md`、本目录的 `contracts_v0.py`
和 `examples/` 是早期接口评审快照，保留以解释历史记录。
当前接口示例位于仓库根目录 `examples/`，由根目录 `build_contract_examples.py` 检查。
历史数据工具 `mock_property_data 2/` 自带的契约也仅服务其历史数据，不供正式应用导入。
