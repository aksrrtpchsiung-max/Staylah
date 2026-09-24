# Contract v0 修改说明（2026/09/14）

## 2026/09/24：搜索卡片图片的 Evidence 约定

不增加 `Listing` 顶层字段，不改变公开函数签名。B 在搜索阶段通过 guru_search
保留卡片提供的全部图片链接，写入 `Listing.evidence` 的
`field="media.search_card_photos"`，`value.version=1`。
`value.images` 按来源顺序保存 `{image_id, kind, urls, caption}`；同一图片的已观察尺寸
链接全部保留在 `urls`，相同 URL 去重。`kind` 区分 `photo`、`floor_plan`、`site_plan`、
`thumbnail`、`video_thumbnail`。
`reported_count` 为卡片标示的照片数或 null，`extracted_count` 为唯一 photo 数量；
`status` 为 complete/partial/unknown/unavailable，`issues` 保存图片解析问题。
照片数不计入附加图和独立缩略图；complete 只表示本次卡片照片数量对齐，不代表链接永久可用。
图片缺失不阻断搜索、不单独降级核心搜索状态。展示端选取最新 `observed_at` 的记录，
逐张优先加载 `images[].urls[0]`，失败时尝试剩余链接或显示占位图。
详细字段语义、消费示例与真实测试入口见 README 的“搜索卡片图片”。

---

本次修改统一了数据库房源字段与跨模块接口。数据库仍可使用规范化的
`listing + hdb_detail/condo_detail/landed_detail`，B 在返回 `SearchResult` 前将查询结果
转换为 `contracts_v0.Listing`；C 只依赖 contract，不直接依赖数据库子表。

## A：conversation 画像与用户确认

- 原 `UserProfile` 更名为 `ConversationProfile`。一条 conversation 只有一个 profile；
  `user_id` 仅用于归属和权限，不代表跨 conversation 的长期用户画像。
- LLM 每轮先生成 `ProfileChange`，由确定性服务合并为 draft profile；LLM 不直接覆盖
  已确认版本。
- 用户需求分为三组：
  - `user_context`：人数、是否有孩子、工作地点等本轮找房背景；
  - `listing_constraints`：针对允许查询的 Listing 字段的条件；
  - `derived_data_requirements`：通勤、学校、环境、可达性等需要 B 取数或计算的数据。
- A 必须通过 `RequirementConfirmation` 让用户确认。只有
  `confirmed_version == version` 的 profile 才能创建 `RequirementRequest` 交给 B。
- 用户修订条件会产生新的 draft version，必须重新确认。

## A 到 B：唯一公开边界

- A 只调用 `fulfill_requirements(request, ctx)`，不向 B 提供 SQL、CLI 命令、Provider、
  `QueryFeatures` 或 `SearchPlan`。
- `RequirementRequest` 只包含已确认的意图、用户背景、Listing 条件、派生数据需求和
  非阻塞开放需求。
- 无法映射到已知字段或派生类别的条件进入 `open_data_requirements`，固定使用
  `handling="best_effort"`。它只能用于补充验证或排序，不能阻塞 B 返回核心条件已匹配的房源。
- B 通过 `RequirementFulfillment` 返回已完成、部分完成或需要澄清，并报告每条派生需求
  是 fulfilled、unsupported 还是 unverified。
- B 无法处理开放需求时，将其 ID 写入 `skipped_best_effort_requirement_ids`。这不会单独把
  `status` 降为 `partial`，也不能把已有房源结果改成“没有符合要求的房源”。
- `completed` 表示核心检索已完成，可以同时包含被跳过的开放需求；`partial` 只用于核心
  检索或受支持派生需求未完整执行；`needs_clarification` 只用于缺少会阻断核心检索的信息。
- B 需要澄清时只返回结构化 `Clarification`；由 A 负责继续与用户对话。
- `prepare_query`、`build_search_plan` 和 `search` 保留为 B 的内部 contract，不是 A 需要构造的对象。

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
