# Contract v0 修改说明（面向 B、C）

本次修改统一了数据库房源字段与跨模块接口。数据库仍可使用规范化的
`listing + hdb_detail/condo_detail/landed_detail`，B 在返回 `SearchResult` 前将查询结果
转换为 `contracts_v0.Listing`；C 只依赖 contract，不直接依赖数据库子表。

## B：数据获取与检索

### 需要修改的输出

1. 金额由字符串改为整数 SGD：
   - `HardConstraints.max_price: int | None`
   - `Price.amount: int | None`
   - 不再返回 `"3500.00"`，改为 `3500`。
2. 用户意图和房源交易类型分开：
   - 搜索意图 `SearchPlan.intent` 仍为 `rent/buy`；
   - 房源 `Listing.transaction_type` 为 `rent/sale`；
   - `intent=buy` 搜索 `transaction_type=sale` 的房源。
3. `Price.scope` 已删除，改用 `Listing.attributes.listing_scope`。
4. 每条 Listing 必须增加完整的 `attributes`：物业类别、户型、面积、卫生间、
   房东同住、烹饪、访客、宠物、家具和产权等字段。
5. 每条 Listing 必须增加：
   - `listing_status: active/inactive/unknown`
   - `listed_date`
   - `last_verified_at`
   - `raw_description`
   - `raw_details`
6. Mock 或无法取得原链接时，`Listing.source_url` 和 `Evidence.source_url` 可以为 `None`。

### 数据质量约定

- 未提及的布尔字段返回 `None`，不能返回 `False`。
- 无法判断物业类别时使用 `property_type="unknown"`；无法判断出租范围时使用
  `listing_scope=None`。
- 刚抓取只更新 `fetched_at`；确认房源仍有效后才更新 `last_verified_at`。
- 明确撤回、售出或租出才设为 `inactive`；无法确认设为 `unknown`。
- `Studio` 是 `attributes.unit_layout="studio"`，不是独立物业类别。
- `listing_key` 仍是 A、B、C 对齐房源的唯一稳定键。

## C：评价与检查

### 筛选字段路径变化

旧字段：

```text
price.scope
```

新字段：

```text
attributes.listing_scope
```

新增可检查字段主要包括：

```text
attributes.property_type
attributes.unit_layout
attributes.area_sqft
attributes.owner_stays
attributes.cooking_policy
attributes.visitors_allowed
attributes.pets_allowed
attributes.furnishing
listing_status
last_verified_at
```

### 检查规则

- `listing_status=inactive`：不得进入推荐候选。
- `listing_status=unknown`：可以进入 `needs_verification`，不能假定为 active。
- 硬条件字段为 `None/unknown/conflict`：进入 `needs_verification`，不能判定 pass。
- `last_verified_at` 超过团队规定的新鲜度阈值：产生 `STALE_EVIDENCE`。
- 推荐中的事实仍必须引用 `evidence_ids`；`raw_description/raw_details` 只是原始材料，
  不能替代结构化 Evidence。

## RunContext

`RunContext` 新增 `user_id`。MVP 统一规定：

```text
RunContext.conversation_id == LangGraph thread_id
```

B、C 不负责读取用户长期 memory，但必须原样传递 `ctx`，并继续使用其中的
`trace_id`、`call_id`、`deadline_at` 和 `source_mode`。

## 最小迁移检查清单

- [ ] 将所有金额 fixture 从字符串改为整数。
- [ ] 将 Listing 的 `buy` 改为 `sale`；SearchPlan 的 `buy` 保持不变。
- [ ] 将 `price.scope` 改为 `attributes.listing_scope`。
- [ ] Provider 输出完整 `Listing.attributes`。
- [ ] 筛选器正确处理 `None/unknown/inactive`。
- [ ] 所有函数调用的 `RunContext` 增加 `user_id`。
- [ ] 使用 Python 3.11+ 运行 `python3.11 build_contract_examples.py`。
