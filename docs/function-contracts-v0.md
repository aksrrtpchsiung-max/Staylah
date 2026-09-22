# 函数签名与输入输出示例 V0

状态：接口评审草案，未冻结，业务函数尚未实现。日期：2026-09-09。

本稿补充《接口冻结会议准备稿》的九个函数。类型定义、签名、正常/边界/错误示例已提供；不涉及人员分工。示例采用新加坡整套租房，首版实际业务范围仍由团队确认。

## 1. 配套文件与阅读方式

| 文件 | 用途 |
|---|---|
| [contracts_v0.py](contracts_v0.py) | 全部类型与九个函数的完整 Python 签名；Python 3.11+，函数体均明确未实现 |
| [shared-fixtures.json](examples/shared-fixtures.json) | 共同需求、候选、快照和评价对象 |
| [function-contract-cases.json](examples/function-contract-cases.json) | 37 个完整用例；每例的输入与预期输出已完全展开，无省略号或外部引用 |
| [build_contract_examples.py](examples/build_contract_examples.py) | 生成用例并检查签名绑定、类型结构、返回封装与三类覆盖 |

下文为了可读性使用共享对象别名，并在表格中展示关键结果字段。JSON 用例是完整的数据级示例：按 case_id 可找到该函数所有参数、依赖模拟行为、预期返回或异常及断言。

函数中的 `*` 表示其后参数只能按名称传递。async 函数调用需要 await；screen、decide_next 为纯规则函数。模型、Provider、词表和日志等依赖由服务初始化时注入，不让调用方传 API 密钥或数据库连接。

TypedDict 提供类型说明，不承担运行时验证；落地时应转为团队共享的校验模型。这里的 Python 3.11+ 仅是契约文件的语言要求，不代表整个项目技术选型已冻结。

## 2. 共同对象与公共规则

### 2.1 共同输入

- `CTX`：run-001 / conv-001 / attempt-001；source_mode=mock；call-001 / trace-001；deadline=2026-09-09T10:02:00+08:00。日期仅为示例，执行时由后端重新注入有效 deadline。
- `P0`：version=0，交易类型、预算和搜索范围尚待补充。
- `P1`：version=1；租房，SGD 月租最多 3500.00，整套，TAMPINES，至少 2 卧；各条件保留用户消息来源。
- `Q1`：profile_version=1；识别 TAMPINES，别名 Tampines/淡滨尼，无待澄清实体。
- `PLAN1`：plan-001，保持 P1 全部硬条件；查询 demo_a；总页数上限 3、候选上限 60。
- `POLICY`：min_matches=3、display_limit=10、max_search_attempts=3、max_repairs=1；均为待确认的会议示例值。

| 候选 | 月租 SGD | 卧室 | 地点/范围 | 预期筛选 |
|---|---:|---:|---|---|
| L1 | 3400.00 | 2 | TAMPINES / 整套 | pass |
| L2 | 3500.00 | 2 | TAMPINES / 整套 | pass，恰好预算上限 |
| L3 | 3500.01 | 2 | TAMPINES / 整套 | fail，超过预算 |
| L4 | null | 2 | TAMPINES / 整套 | unknown，页面同口径金额 3200 与 3800 冲突 |
| L5 | 3300.00 | null | TAMPINES / 整套 | unknown，卧室数未确认 |
| L6 | 3300.00 | 2 | TAMPINES / 整套 | pass |

`SNAPSHOT1` 包含以上六套房源和字段证据；`SCREEN1` 的 eligible=[L1,L2,L6]、rejected=[L3]、needs_verification=[L4,L5]；`RETRIEVAL1` 引用三个通过候选。所有 URL 使用 example.com，均为虚构数据。

### 2.2 公共结果封装

```python
class Result(TypedDict, Generic[T]):
    status: Literal["success", "partial", "error"]
    data: T | None
    issues: list[Issue]
    meta: ResultMeta
```

完整错误返回示例：

```json
{
  "status": "error",
  "data": null,
  "issues": [{
    "code": "TIMEOUT",
    "message": "示例：TIMEOUT",
    "field_path": null,
    "source": "demo_a",
    "retryable": true,
    "retry_after_seconds": null
  }],
  "meta": {"trace_id": "trace-001", "call_id": "call-001", "duration_ms": 10}
}
```

- success：data 非 null，可以是空候选列表等合法业务结果。
- partial：data 非 null，至少一条 issue；仅用于保留可用结果的降级场景。
- error：data=null，至少一条 issue；不得混入看似正常的候选。
- retryable 只描述故障性质，调用方仍须检查该调用已用额度，不能叠加新的无界重试。
- `screen`、`decide_next` 不用 Result 封装。非法输入或不可能状态抛 `ContractViolation(code, field_path, message)`；调用边界记录并转换为系统错误。
- 正常发现不符合条件、发现坏草稿属于函数成功执行，不应当作函数调用失败。
- 输入按只读处理；任何函数都不直接修改客户偏好、发布消息或写入业务库。编排器/Repository 负责提交和 checkpoint。

### 2.3 本轮冻结的字段范围

为使所有示例可闭合，这份最小契约只覆盖交易类型、币种、金额/周期/整租口径、地区、卧室数等硬条件。`Listing.price.scope` 是租赁口径的唯一字段，避免与顶层 rental_scope 重复；`Listing.location_id` 使用规范化地区 ID。

面积、入住时间、宠物等条件若纳入首版，必须同步扩展 UserProfile、Listing、证据、screen 和测试用例。在扩展完成前，这些要求应进入 unresolved 并明确提示当前不支持，不能把未检查的要求宣称为满足。软偏好可经 preferences 表达，但没有房源证据时只能保留未知。

## 3. onboard：形成需求变更与澄清结果

```python
async def onboard(
    request: UserMessage,
    profile: UserProfile,
    messages: list[ChatMessage],
    *,
    ctx: RunContext,
) -> Result[OnboardResult]:
    raise NotImplementedError
```

`request` 仅包含本次新消息，`messages` 不重复包含它。ready_for_search 描述假设应用合法 patch 后的完整性，必须由程序复核。base_profile_version 用于后续原子更新档案，函数本身不递增版本。

| 用例 ID | 输入 | 预期输出 |
|---|---|---|
| onboard.normal | P0；“整套租房，SGD 月租最多3500，淡滨尼，至少两个卧室。” | success；base_profile_version=0；patch 设置 intent=rent、币种 SGD、预算 3500.00、周期 month、整租、地区 TAMPINES、卧室下限 2；ready_for_search=true；questions=[] |
| onboard.boundary | P0；相同需求但明确“预算还没确定” | success；patch 不写预算；missing_required_fields=[hard_constraints.max_price]；ready_for_search=false；询问月租上限 |
| onboard.error | request.text="   " | error；INVALID_INPUT，field_path=request.text；不调用模型 |

验收：patch 每项包括 field/value/source_message_id；不得把推荐的预算或推测偏好写入已确认条件。若出现互相矛盾的预算，同样返回澄清结果。

## 4. prepare_query：识别查询实体和别名

```python
async def prepare_query(
    profile: UserProfile,
    *,
    ctx: RunContext,
) -> Result[QueryFeatures]:
    raise NotImplementedError
```

| 用例 ID | 输入 | 预期输出 |
|---|---|---|
| prepare_query.normal | P1 | success；profile_version=1；entity canonical_id=TAMPINES；aliases=[Tampines,淡滨尼]；unresolved=[] |
| prepare_query.boundary | 地区表达“裕廊”，本例词表存在多个候选，profile.unresolved 标明地区尚不明确 | success；canonical_id=null；unresolved 包含“裕廊东、裕廊西还是两者”；编排器暂停澄清 |
| prepare_query.error | profile.version=-1 | error；INVALID_INPUT，field_path=profile.version |

验收：canonical_id 来自实际词表；不能将一个地区替换成另一个地区。无地点限制时 locations=[] 且没有对应 unresolved；unknown 与用户明确不限地区不可混淆。

## 5. build_search_plan：生成保持约束的查询计划

```python
async def build_search_plan(
    profile: UserProfile,
    query: QueryFeatures,
    previous_attempts: list[AttemptSummary],
    directive: SearchDirective | None,
    *,
    ctx: RunContext,
) -> Result[SearchPlan]:
    raise NotImplementedError
```

前提：需求可用于搜索、query.unresolved 为空、版本一致，ctx.attempt_id 非 null。page_limit 是整个 search 调用的总页数上限，多个来源共享额度。

| 用例 ID | 输入 | 预期输出 |
|---|---|---|
| build_search_plan.normal | P1、Q1、previous_attempts=[]、directive=null | success；PLAN1；required_filters 与 P1 一致；source_mode=mock |
| build_search_plan.boundary | 已完成 attempt-001；下一页指令 cursor=page-2；ctx.attempt_id=attempt-002 | success；plan-002 / attempt-002；相同约束，查询 cursor=page-2 |
| build_search_plan.error | profile.version=1，query.profile_version=0 | error；STATE_CONFLICT，field_path=query.profile_version |

验收：不得重复同一个已完成查询指纹；无新计划时返回 NO_NEW_QUERY。指纹应覆盖来源、规范化查询、cursor 和硬条件。不得接受将月租 3500 变为 3800 的计划输出；此类输出返回 CONSTRAINT_CHANGE_NOT_ALLOWED。用户明确改变需求后由外层产生新版档案和新 run。

## 6. search：返回标准化结果与覆盖情况

```python
async def search(
    plan: SearchPlan,
    *,
    ctx: RunContext,
) -> Result[SearchResult]:
    raise NotImplementedError
```

前提：plan 与 ctx 的 attempt_id/source_mode 一致，页数/候选上限为正整数。search 的空结果、超时等依赖行为通过用例中的 dependency_fixture 指定，不能仅凭同一 plan 判断返回结果。

| 用例 ID | 输入与来源行为 | 预期输出 |
|---|---|---|
| search.normal | PLAN1；模拟来源返回 L1–L6 和重复 L1 | success；items=L1–L6，共六条；queries_completed=true；has_more=false |
| search.boundary | 同一 PLAN1；来源成功返回 [] | success；items=[]；不能返回 TIMEOUT，也不能声称全市场无房源 |
| search.error | 来源首次和一次重试均超时 | error；data=null；TIMEOUT、source=demo_a；内部重试已用完 |
| search.partial | demo_a 成功，demo_b 限流；要求等待 60 秒但活动预算只剩 20 秒 | partial；保留 demo_a 候选；failed_sources=[demo_b]；queries_completed=false；RATE_LIMITED |

验收：返回查询时刻、来源 URL 和证据；冲突价格保留 unknown/conflict，不能选择更低金额。不同来源的疑似重复不做无依据的物理房产合并。所有来源失败必须 error，不能返回 success+[]。

## 7. screen：检查硬条件，明确三态

```python
def screen(
    listings: list[Listing],
    profile: UserProfile,
) -> ScreenResult:
    raise NotImplementedError
```

三类划分规则：任意硬条件 fail 则 rejected；无 fail 但存在 unknown 则 needs_verification；全部通过才 eligible。输入 key 必须唯一，字段格式合法；没有预算等未完成需求不得调用该函数。

| 用例 ID | 输入 | 预期输出 |
|---|---|---|
| screen.normal | [L1,L3]、P1 | eligible=[L1]；rejected=[L3]；L3 的 price.amount 检查为 fail |
| screen.boundary | [L2,L4,L5]、P1 | eligible=[L2]；needs_verification=[L4,L5]；分别指出价格冲突、卧室未知 |
| screen.error | 月租 amount="-1.00" 的房源 | 抛 ContractViolation；code=INVALID_INPUT；field_path=listings[0].price.amount |
| screen.empty | []、P1 | 三个分类数组均为空，profile_version=1 |

验收：金额使用 Decimal 比较，3500.00 允许，3500.01 拒绝；不是用浮点近似或模型判断。每项检查包括 field/status/reason/evidence_ids。三组互斥且覆盖全部输入；不删除未知候选的记录。

## 8. retrieve：在给定候选内匹配

```python
async def retrieve(
    query: QueryFeatures,
    eligible_listings: list[Listing],
    *,
    top_k: int,
    ctx: RunContext,
) -> Result[RetrievalResult]:
    raise NotImplementedError
```

前提：query 已消歧、top_k>=1；调用方从 snapshot 按 screen.eligible 的 key 提取完整 Listing 传入，不能只传分数或裸 ID。

| 用例 ID | 输入 | 预期输出 |
|---|---|---|
| retrieve.normal | Q1、[L1,L2,L6]、top_k=30 | success；三个候选；input_count=returned_count=3；truncated=false；精确命中 TAMPINES |
| retrieve.boundary | Q1、[]、top_k=30 | success；candidates=[]；两项 count 均为 0；truncated=false |
| retrieve.error | top_k=0 | error；INVALID_INPUT，field_path=top_k |
| retrieve.truncated | 三个候选，top_k=2 | success；input_count=3、returned_count=2、truncated=true |

验收：仅引用输入 key，无重复、rank 连续。模型/Embedding 分数值在示例中仅作格式演示；冻结实际检索实现和依赖后才要求固定输入稳定排序。truncated=true 时，调用方仍按三条 eligible 统计实际匹配数量。

## 9. evaluate：使用候选事实生成推荐

```python
async def evaluate(
    profile: UserProfile,
    retrieval: RetrievalResult,
    screen_result: ScreenResult,
    listing_snapshot: ListingSnapshot,
    coverage: Coverage,
    repair_context: ReviewResult | None,
    *,
    policy: RoutingPolicy,
    ctx: RunContext,
) -> Result[EvaluationResult]:
    raise NotImplementedError
```

这是对上一稿的必要补充：listing_snapshot 提供实际事实与证据，screen_result 提供符合、失败、未知记录，不能只凭候选 ID 和检索分数写推荐。输入版本必须一致；retrieval 的 key 必须属于 eligible 且存在于 snapshot。

| 用例 ID | 输入 | 预期输出 |
|---|---|---|
| evaluate.normal | P1、RETRIEVAL1、SCREEN1、SNAPSHOT1、成功覆盖、repair_context=null、POLICY | success；有序推荐引用 L1/L2/L6；理由引用各自 price.amount 证据；profile_version=1，snapshot_id=snapshot-001 |
| evaluate.boundary | eligible=[]；本轮仅 L3 因超预算被排除 | success；ordered_items=[]；说明本轮筛除原因；返回预算调整提案，requires_user_confirmation=true；不修改 P1 |
| evaluate.error | retrieval 引用了 snapshot 中不存在的 L999 | error；INVALID_INPUT，field_path=retrieval.candidates[0].listing_key；不调用模型编造事实 |

验收：正常成功时推荐覆盖检索候选直到 display_limit；如评价发现候选数据疑点，返回具体审查问题，不能静默减少数量却仍宣称充分匹配。推荐主列表只来自 eligible 与 retrieval 的交集；待核实项不凑数。价格等事实需证据，主观判断标为 judgment；无证据的实际可租状态保留 unknown。

repair_context 非 null 表示编排器授权的一次修复调用；函数自身不递归调用。是否重搜、询问用户与发布结果由编排器决定。

## 10. review：区分发现缺陷与审查服务失败

```python
async def review(
    profile: UserProfile,
    evaluation: EvaluationResult,
    listing_snapshot: ListingSnapshot,
    *,
    policy: RoutingPolicy,
    ctx: RunContext,
) -> Result[ReviewResult]:
    raise NotImplementedError
```

前提：evaluation 的 profile_version 与 snapshot_id 匹配本次输入。结合事实证据复核硬条件、rank、数量、事实引用和限制说明；review 本身不改写推荐。

| 用例 ID | 输入/依赖 | 预期输出 |
|---|---|---|
| review.normal | 合法三条推荐及对应 SNAPSHOT1 | success；passed=true；issues=[] |
| review.boundary | 空推荐草稿，结构合法且没有编造事实 | success；passed=true；数量不足交给 decide_next 判断 |
| review.error | 审查模型服务不可用 | error；data=null；MODEL_UNAVAILABLE；不能假装审查通过 |
| review.detected_defect | L1 增加“步行到地铁只需 5 分钟”，无证据 | success；passed=false；UNSUPPORTED_CLAIM；指出 reasons[1] 并建议删除或补充证据 |

关键区别：`status=success, data.passed=false` 表示审查完成并发现问题；`status=error` 表示审查未能完成。前者使用内容修复预算，后者按服务故障策略处理，不能一律算作模型内容修复。

## 11. decide_next：执行唯一的路由政策

```python
def decide_next(
    state: DecisionState,
    policy: RoutingPolicy,
) -> RouteDecision:
    raise NotImplementedError
```

DecisionState 是从 AgentState 提取的只读决策视图，不把原始页面、模型客户端或完整聊天传进来。它包含：版本、取消/拒绝标记、deadline 状态、来源状态、搜索/修复计数、eligible_count、review、错误、合法补搜指令、待问问题。

| 用例 ID | 输入关键字段 | 预期输出 |
|---|---|---|
| decide_next.normal | eligible_count=3、review.passed=true、版本一致 | action=publish，reason_code=enough_matches |
| decide_next.boundary | eligible_count=2、search_attempts_used=3，存在已准备的调整问题 | action=ask_user，reason_code=insufficient_candidates；返回带 question_id 和版本的问题，不安排第四次搜索 |
| decide_next.error | eligible_count=-1 | 抛 ContractViolation；INVALID_STATE，field_path=state.eligible_count |
| decide_next.research | 两条结果、已搜索一次，存在合法下一页指令 | action=research；返回 SearchDirective |
| decide_next.repair | 审查有阻断问题、repairs_used=0 | action=repair，reason_code=review_blocked |
| decide_next.repair_exhausted | 审查有阻断问题、repairs_used=1 | action=stop，reason_code=repair_exhausted |
| decide_next.source_failure | search_status=error、TIMEOUT、review=null | action=stop，reason_code=source_failure；不因 0 条结果要求提高预算 |
| decide_next.stale | profile_version=1，current_profile_version=2 | action=stop，reason_code=profile_superseded |
| decide_next.declined | user_declined=true、已有两条合法结果 | action=finish，reason_code=user_declined |

程序校验与决策顺序：非法计数/策略/版本字段先拒绝；取消和需求被取代先停止；用户拒绝则结束；准备阶段缺信息则询问；来源或审查服务失败按错误终止；内容阻断先修复；合法且足量则发布；不足量且可合法补搜则 research；否则有具体问题则 ask_user，无问题则 finish。

补充约束：

- min_matches/display_limit>=1，display_limit>=min_matches；预算上限非负，max_search_attempts>=1。
- deadline_exhausted=true 时禁止 research/repair；可结束并保留已审查结果，或将待确认提案交给用户。
- 非准备阶段、没有系统错误却缺少 review 时是 INVALID_STATE，不能发布未审查结果。
- pending_question 的 base_profile_version/state_version 必须匹配当前视图；不匹配时 INVALID_STATE。question_id 由编排器预先分配，纯函数不生成随机 ID。
- 输出 research 时只有 search_directive 非 null；ask_user 时只有 pending_question 非 null；其他动作这两项均 null。
- publish/finish 只决定动作，实际保存和发出结果由编排器执行；finish 不等于“找到了足量房源”。

## 12. 验证边界与团队下一步

运行生成器可以验证每个用例参数与真实签名一致，完整 JSON 符合类型结构，Result 的 error/partial 规则一致，九个函数都至少包含正常、边界、错误示例。负数预算/版本、非法 top_k 等语义错误故意作为负例保留。

这些检查不表示业务实现已通过测试：业务函数还没有实现，模型输出质量、金额比较逻辑、真实来源、并发与 checkpoint 恢复均未执行。用例中的模型回复是合法输出样本，验收应检查事实、约束和结构，不能逐字比对模型文案或强制示例中的软偏好顺序。

接口冻结前需由团队确认：首版硬条件集合、阈值与预算、具体排名政策，以及类型模型的实现方式。随后将这些 JSON 场景接入各模块测试，Provider/模型响应由 dependency_fixture 模拟；第一轮以同一组 fixture 连通九个函数。
