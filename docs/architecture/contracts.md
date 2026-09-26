# Pipeline v1：接口与数据契约

> 历史设计资料，保留原方案。当前实现请看 [模块设计](../../模块设计.md)、[网页 API](../../web/README.md) 和 [共享契约](../../property_agent/contracts.py)，不要将本文中的未实现功能当成现有行为。

日期：2026-09-08。状态：设计契约，尚未实现；以下 JSON 均为虚构示例。  
上位设计：[pipeline-v1.md](pipeline-v1.md)。JSON 字段使用 snake_case，时间使用带时区的 ISO 8601，数据库采用 timestamptz。金额使用十进制字符串或数据库 NUMERIC，禁止浮点金额运算。

## 1. Web API

前缀 /api/v1；所有资源接口要求登录并检查归属。部署建议 Web 与 API 同站点，使用 HttpOnly 会话 cookie，并为写入操作实施 CSRF 防护。

| 方法与路径 | 请求要点 | 响应与语义 |
|---|---|---|
| POST /clients | display_name | 201，创建属于当前用户的客户 |
| GET /clients | 游标分页 | 仅返回当前用户可访问客户 |
| GET /clients/{client_id}/profile | 无 | 当前偏好、version、待确认项 |
| PATCH /clients/{client_id}/profile | patch、expected_version | 用户面板明确修改；成功 version+1，冲突 409 |
| POST /clients/{client_id}/conversations | 可选 title | 201，创建会话 |
| GET /conversations/{conversation_id}/messages | cursor、limit | 按顺序恢复聊天 |
| POST /conversations/{conversation_id}/messages | content、client_message_id、Idempotency-Key | 202，原子保存消息、run、job |
| GET /runs/{run_id} | 无 | 状态、需求版本、coverage、结果引用 |
| GET /runs/{run_id}/events | SSE，Last-Event-ID 可选 | 回放已持久化事件；保留窗口外需重新获取状态 |
| POST /runs/{run_id}/cancel | Idempotency-Key | 请求取消；已完成则返回当前状态 |
| GET /recommendations/{recommendation_id} | 无 | 已保存的有序结果、证据快照与查询时间 |
| POST /clients/{client_id}/feedback | message_id 或用户输入、snapshot_id 可选 | 保存反馈；不自动把一次拒绝转为通用偏好 |
| POST /clients/{client_id}/watch-rules | schedule、enabled、notify_policy | 201，持久化关注规则 |
| PATCH /watch-rules/{watch_id} | patch、expected_version | 修改或暂停规则，版本递增 |
| POST /watch-rules/{watch_id}/run | Idempotency-Key | 手动检查，仍走任务队列 |
| GET /notifications | cursor、read_status | 读取当前用户的应用内提醒 |
| PATCH /notifications/{notification_id} | read=true | 标为已读 |
| DELETE /clients/{client_id} | 无 | 取消运行/规则并按删除策略移除客户关联数据 |

请求幂等键与当前用户、操作类型绑定。相同键和相同 payload 返回原 run；相同键但不同 payload 返回 409。归属不匹配返回 404，避免泄露其他用户资源存在性。

发送消息示例：

~~~json
{
  "client_message_id": "d29b11c2-d596-4c31-a463-0c26bde8cbce",
  "content": "预算最多80万新币，想买HDB，优先考虑淡滨尼。"
}
~~~

响应：

~~~json
{
  "message_id": "8f377bd7-ff6f-4d4b-a7af-02c4e0a59a29",
  "run_id": "8870f45d-70b4-41db-b425-8ecaf61fdc67",
  "status": "queued",
  "events_url": "/api/v1/runs/8870f45d-70b4-41db-b425-8ecaf61fdc67/events"
}
~~~

需求 PATCH 与消息式更新共用 ProfileService。面板编辑不是与聊天各维护一份偏好。面板修改使依赖旧版本的运行不可发布；可由前端另发明确的重新搜索请求。

## 2. 客户偏好

~~~json
{
  "version": 4,
  "intent": "buy",
  "hard_constraints": {
    "currency": "SGD",
    "max_price": "800000.00",
    "budget_scope": "purchase_price",
    "property_types": ["HDB"],
    "min_bedrooms": 3
  },
  "preferences": [
    {
      "dimension": "location",
      "value": "TAMPINES",
      "priority": "high",
      "origin": "explicit",
      "source_message_id": "8f377bd7-ff6f-4d4b-a7af-02c4e0a59a29"
    }
  ],
  "unresolved": [],
  "hypotheses": []
}
~~~

说明：

- priority 表示用户表达的优先程度，不映射固定数值权重。
- 硬条件的语义须具体：min_bedrooms 与 HDB flat_type 分开。
- budget_scope 未明确时保留 unresolved；不能用购房总开支预算直接假定为叫价上限。
- 硬条件变更和明确偏好来源要可追踪；从用户反馈推测的 hypothesis 不自动变成 hard_constraints。
- 首版通过当前 profile.version 加每次 run 的 profile_snapshot 复现推荐；更完整的档案变更历史可后续增加独立版本表。

## 3. Agent 工具边界

模型可请求以下有类型能力，运行器注入身份、client_id、run_id 与调用预算。模型不得自行指定授权身份。

| 工具 | 输入 | 输出 |
|---|---|---|
| search_listings | QuerySpec、cursor 可选 | SearchResult |
| get_listing_detail | 来自本次结果或本人关注记录的 ListingRef | 标准化详情与证据；明确错误或未知状态 |
| get_commute | 已验证坐标/地址、目的地、模式、日期时段 | 路线估计、来源、查询时间、未知项 |
| get_nearby_amenities | 已验证位置、类别、范围 | 配套事实与来源 |
| compare_candidates | 本 run 候选引用 | 可比较事实矩阵；不输出程序加权排名 |

以下是运行器内部服务节点，模型可以提出结构化意图，但不能直接绕过校验提交：

- ProfileService：应用用户明确修改，做 expected_version 检查。
- FeedbackService：保存客户反馈及来源。
- RecommendationService：验证模型结果并保持其顺序保存。
- WatchService：根据用户明确操作创建、暂停或修改关注。

现有数据库读写与外部工具实现独立。替换 ListingProvider 不改变 Onboard、推荐 prompt 或 Web API。

## 4. ListingProvider

必需接口：search(query, cursor) 和 detail(listing_ref)。这两个接口由运行器调用，返回规范化对象。用于展示的动态页面字段与内部类型在此层转换。

QuerySpec 示例：

~~~json
{
  "listing_type": "sale",
  "location_query": "Tampines",
  "currency": "SGD",
  "max_price": "800000.00",
  "min_bedrooms": 3,
  "property_types": ["HDB"],
  "page_size": 20,
  "cursor": null
}
~~~

来源不支持某过滤字段时，在 applied_filters / unsupported_filters 中明确返回，由后端对可验证字段复核。不能把未应用的条件当作已生效。

SearchResult 示例：

~~~json
{
  "source": "demo_provider",
  "source_mode": "demo",
  "fetched_at": "2026-09-08T12:00:00Z",
  "status": "success",
  "items": [
    {
      "source_listing_id": "demo-flat-001",
      "source_url": "https://example.com/demo-flat-001",
      "listing_type": "sale",
      "title": "示例房源 A",
      "price": {"amount": "780000.00", "currency": "SGD", "period": null},
      "bedrooms": 3,
      "flat_type": "4_ROOM",
      "floor_area": {"value": "93.0", "unit": "sqm"},
      "location": {"address": "示例地址", "lat": null, "lon": null},
      "source_status": "listed",
      "verification_status": "source_reported",
      "source_updated_at": null,
      "source_posted_at": null,
      "missing_fields": ["location.lat", "location.lon"],
      "evidence": [
        {
          "id": "price",
          "field": "price.amount",
          "value": "780000.00",
          "source_url": "https://example.com/demo-flat-001",
          "observed_at": "2026-09-08T12:00:00Z"
        }
      ]
    }
  ],
  "coverage": {
    "query_completed": true,
    "has_more": false,
    "truncated": false,
    "next_cursor": null,
    "applied_filters": ["listing_type", "location_query", "max_price", "min_bedrooms"],
    "unsupported_filters": [],
    "failed_operations": []
  }
}
~~~

success 只表示本次查询正常完成。items 为空是成功空结果；认证、解析、网络失败返回对应错误，不能伪装成空结果。coverage.query_completed 不表示查遍全市场。

verification_status 为 source_reported、agent_confirmed 或 unknown。agent_confirmed 必须有经纪人明确核实记录与时间。source_status=listed 只反映来源展示状态，不构成实际可售保证。

统一错误码：

| 错误码 | 处理 |
|---|---|
| INVALID_ARGUMENT | 修复参数，不重复发送相同无效请求 |
| AUTH_REQUIRED | 标记来源需要配置；不要求普通 Web 用户安装扩展 |
| RATE_LIMITED | 尊重来源重试时间，受总预算限制 |
| TIMEOUT / TEMPORARY_UNAVAILABLE | 有界重试 |
| PARSE_ERROR | 记录适配器异常，不当作无房源 |
| NOT_FOUND | 记录该引用未找到，不直接推断已售 |
| BUDGET_EXHAUSTED | 返回已完成范围与限制 |

## 5. 候选证据与模型排序输出

运行器为候选分配 snapshot_id，并生成结构化硬条件检查结果。给模型的输入包括 profile_snapshot、候选事实/证据、evaluation_dimensions、coverage 和待核实项。

模型输出只引用本 run 的 snapshot_id，不产生新房源 ID。推荐主列表包含硬条件可验证通过的候选；待核实候选单独保存在 review_candidates，并按模型给出的顺序展示。

~~~json
{
  "summary": "优先推荐示例房源 A；实际可售状态仍需核实。",
  "ranked_items": [
    {
      "snapshot_id": "8a258a2b-96ee-43ac-a247-a66b2a9bd409",
      "rank": 1,
      "fit_reasons": [
        {
          "text": "挂牌价低于已确认的房价预算上限。",
          "evidence_refs": ["8a258a2b-96ee-43ac-a247-a66b2a9bd409:price"]
        }
      ],
      "tradeoffs": [],
      "unknowns": ["未独立确认当前可售状态"],
      "why_over_next": null
    }
  ],
  "review_candidates": [],
  "search_limitations": ["结果仅覆盖本次查询范围"]
}
~~~

此示例省略完整候选集。真实输入中的主列表候选需有所有硬条件的证据；若客户把已核实可售状态列为硬条件，source_reported 的候选只能进入 review_candidates。

校验要求：

1. 主列表 rank 连续且唯一，snapshot_id 不重复且属于本 run。
2. 推荐引用的候选属于当前客户授权范围。
3. 所有主列表候选通过已知硬条件校验；缺失必需字段不能通过。
4. evidence_refs 实际存在；声明的数值与对应事实一致。
5. 模型不能把未确认信息写成已核实事实。
6. 前端数值卡片从 snapshot 读取，不从解释文本反向解析。
7. 校验不通过时有界修复；不能计算程序总分作为隐藏兜底。

review_candidates 作为 recommendation 的 JSONB 补充项保存，不与主列表 rank 混用。推荐发布后主列表顺序固定；浏览旧报告不重新调用模型。

## 6. 运行事件

SSE 示例：

~~~text
id: 8870f45d-70b4-41db-b425-8ecaf61fdc67:12
event: tool_completed
data: {"run_id":"8870f45d-70b4-41db-b425-8ecaf61fdc67","seq":12,"tool":"search_listings","result_count":18,"source_mode":"demo"}
~~~

事件类型：run_started、clarification_needed、profile_updated、search_started、tool_completed、tool_failed、ranking_started、validation_retry、recommendation_ready、run_finished。

run_events 唯一键为 (run_id, seq)。事件内容为脱敏的参数摘要、执行状态、结果引用和耗时；不存 cookie、API key 或隐藏推理过程。recommendation_ready 必须在推荐事务提交之后可见。

## 7. 数据表与关键约束

| 表 | 最小字段与约束 |
|---|---|
| users | id、登录身份引用；可由身份提供方映射 |
| clients | id、owner_id、display_name、created_at |
| buyer_profiles | client_id 主键、version、hard_constraints/preferences/unresolved JSONB、updated_at |
| conversations | id、client_id、title、created_at |
| messages | id、conversation_id、role、content、client_message_id、created_at；请求 ID 在会话内唯一 |
| agent_runs | id、client_id、conversation_id 可空、watch_rule_id 可空、trigger、status、profile_version/snapshot、model/prompt/toolset 版本、coverage、usage、timestamps |
| candidate_snapshots | id、run_id、source、source_listing_id、fetched_at、source_updated_at、verification_status、facts/evidence JSONB；(run_id,source,source_listing_id) 唯一 |
| recommendations | id、run_id 唯一、summary、review_candidates、limitations、created_at |
| recommendation_items | recommendation_id、rank、snapshot_id、eligibility、rationale/evidence JSONB；(recommendation_id,rank) 主键，snapshot_id 唯一；校验同一 run |
| feedback | id、client_id、message_id 可空、snapshot_id 可空、kind、reason、created_at |
| watch_rules | id、client_id、version、status、schedule、next_run_at、notify_policy、successful_baseline、last_status |
| jobs | id、run_id 唯一、kind、priority、idempotency_key 唯一、available_at、status、attempts、lease_owner/until/version |
| run_events | run_id、seq、type、payload、created_at；(run_id,seq) 主键 |
| notifications | id、owner_id、watch_rule_id、run_id、change_signature、read_at；(watch_rule_id,change_signature) 唯一 |

框架 checkpoint 表由相应迁移管理。候选数据在同一次搜索收集阶段可合并 search/detail 证据，冻结后写入不可变 snapshot；后续再查询同一引用产生新 run 中的新快照，保留旧报告所依据的内容。

因异步任务可能重复执行，candidate_snapshots 写入需支持同一 run 的幂等重放：已有冻结快照复用其内容，不覆盖为不同事实。如确需整轮重新取数，则创建新的 run，并把旧 run 标为 superseded。

归属校验、同一 run 引用约束由服务层与适当复合外键共同实现，不能只依赖模型。客户删除时先禁止新任务，取消活动规则，防止旧 worker 恢复写入；敏感业务数据按删除策略级联处理。

## 8. 关注规则与基线

~~~json
{
  "enabled": true,
  "schedule": {
    "type": "daily",
    "local_time": "08:00",
    "timezone": "Asia/Singapore"
  },
  "profile_mode": "latest_confirmed",
  "notify_policy": "meaningful_changes"
}
~~~

另一种 schedule 为 interval，使用 interval_minutes。下一执行时间由服务端计算成 UTC；daily 保留本地时区语义。用户暂停时 version+1，并在任务发布阶段再次检查。

successful_baseline 按来源和规范化 query_signature 分组，保存最近成功检查的 run_id、profile_version、比较范围、少量 listing ID、price、source_status、证据摘要与 hash。它不是外部来源的完整库存。

产生变化事件前进行确定性差异检测；由模型判断变化与客户需求的关系。只有模型换了表达或同证据名次变化时不提醒。

任务发布事务：有效租约 + 当前 profile_version + 当前规则版本/启用状态 → 推荐结果 + 基线 + 去重通知 + run 完成状态。条件不满足则不发布过期提醒。
