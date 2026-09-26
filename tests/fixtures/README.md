# 回归输入

`search/` 的 6 个 JSON 从清理前提交 `a72653f` 的历史合成数据包按字节提取，
用于边界、分页、超时和会话测试；它们不代表当前真实挂牌。生成器及未使用的大批量输出已经移除。

`decision_routes.json` 保留同一提交中接口评审样例里的全部 9 个 `decide_next` 用例，
输入和预期值逐项相同；由 `test_decide_next.py` 执行。其余未被调用的文档样例与签名桩已删除。

`refactor/` 的来源、归一化规则与真实搜索记录见其 [说明](refactor/README.md)。
