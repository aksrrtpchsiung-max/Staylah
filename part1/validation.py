"""从共享 TypedDict 派生运行时校验，避免维护第二套字段定义。"""
import math
import types
import typing
from datetime import date, datetime
from functools import lru_cache

import contracts_v0 as contracts
from contracts_v0 import ContractViolation, Listing, RunContext, SearchPlan


def fail(path: str, message: str) -> typing.NoReturn:
    raise ContractViolation("INVALID_INPUT", path, message)


@lru_cache(maxsize=None)
def _hints(schema):
    return typing.get_type_hints(schema)


def validate_type(schema, value, path="value") -> None:
    """严格检查字段集合、类型和 JSON 数值；bool 不当作 int。"""
    if isinstance(schema, typing.ForwardRef):
        schema = eval(schema.__forward_arg__, vars(contracts))
    origin, args = typing.get_origin(schema), typing.get_args(schema)
    if origin in (typing.Union, types.UnionType):
        for option in args:
            try:
                validate_type(option, value, path)
                return
            except ContractViolation:
                pass
        fail(path, "值不符合约定的类型或枚举")
    elif origin is typing.Literal:
        if not any(type(value) is type(v) and value == v for v in args):
            fail(path, "未知枚举值")
    elif typing.is_typeddict(schema):
        fields = _hints(schema)
        if type(value) is not dict or set(value) != set(fields):
            fail(path, "字段集合与契约不一致")
        for key, child in fields.items():
            validate_type(child, value[key], f"{path}.{key}")
    elif origin is list:
        if type(value) is not list:
            fail(path, "必须是数组")
        for index, child in enumerate(value):
            validate_type(args[0], child, f"{path}[{index}]")
    elif origin is dict:
        if type(value) is not dict:
            fail(path, "必须是对象")
        for key, child in value.items():
            validate_type(args[0], key, f"{path}.key")
            validate_type(args[1], child, f"{path}.{key}")
    elif schema is float:
        if type(value) not in (int, float) or not math.isfinite(value):
            fail(path, "必须是有限数值")
    elif type(value) is not schema:
        fail(path, f"必须是 {schema.__name__}")


def timestamp(value: str, path: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed
    except (ValueError, TypeError):
        fail(path, "必须是带时区的 ISO 日期时间")


def validate_context(ctx: RunContext) -> None:
    validate_type(RunContext, ctx, "ctx")
    for key in ("user_id", "run_id", "conversation_id", "trace_id", "call_id"):
        if not ctx[key].strip():
            fail(f"ctx.{key}", "不能为空")
    timestamp(ctx["deadline_at"], "ctx.deadline_at")


def validate_plan(plan: SearchPlan, ctx: RunContext) -> None:
    validate_context(ctx)
    validate_type(SearchPlan, plan, "plan")
    for key in ("plan_id", "attempt_id"):
        if not plan[key].strip():
            fail(f"plan.{key}", "不能为空")
    if plan["source_mode"] != ctx["source_mode"] or plan["attempt_id"] != ctx["attempt_id"]:
        fail("plan", "计划与上下文的 source_mode / attempt_id 不一致")
    if plan["profile_version"] < 0:
        fail("plan.profile_version", "不能为负数")
    for key in ("page_limit", "candidate_limit"):
        if plan[key] <= 0:
            fail(f"plan.{key}", "必须大于零")
    filters = plan["required_filters"]
    if not filters["currency"].strip():
        fail("plan.required_filters.currency", "不能为空")
    for key in ("max_price", "min_bedrooms"):
        if filters[key] is not None and filters[key] < 0:
            fail(f"plan.required_filters.{key}", "不能为负数")
    ids = set()
    for query in plan["queries"]:
        if not all(query[key].strip() for key in ("query_id", "source", "text")):
            fail("plan.queries", "查询 ID、来源和文本不能为空")
        if query["query_id"] in ids:
            fail("plan.queries", "query_id 必须唯一")
        ids.add(query["query_id"])


def validate_listing(item: Listing) -> None:
    validate_type(Listing, item, "listing")
    for key in ("listing_key", "source"):
        if not item[key].strip():
            fail(f"listing.{key}", "不能为空")
    for path, number in [("price.amount", item["price"]["amount"]),
                         ("bedrooms", item["bedrooms"]),
                         *[(f"attributes.{key}", item["attributes"][key]) for key in
                           ("area_sqft", "bathrooms", "lease_years")]]:
        if number is not None and number < 0:
            fail(f"listing.{path}", "不能为负数")
    price = item["price"]
    if not price["currency"].strip():
        fail("listing.price.currency", "不能为空")
    if (price["status"] == "known") != (price["amount"] is not None):
        fail("listing.price", "known 必须有金额；unknown/conflict 不得保留确定金额")
    ids = set()
    for evidence in item["evidence"]:
        if not evidence["evidence_id"] or evidence["evidence_id"] in ids:
            fail("listing.evidence", "证据 ID 不能为空或重复")
        ids.add(evidence["evidence_id"])
        timestamp(evidence["observed_at"], "listing.evidence.observed_at")
    if not set(price["evidence_ids"]) <= ids:
        fail("listing.price.evidence_ids", "引用了不存在的证据")
    if price["status"] == "known" and not any(
        e["evidence_id"] in price["evidence_ids"] and e["field"] == "price.amount"
        and e["value"] == price["amount"] for e in item["evidence"]
    ):
        fail("listing.price.evidence_ids", "已知价格必须有金额证据")
    for key in ("fetched_at", "source_updated_at", "last_verified_at"):
        if item[key] is not None:
            timestamp(item[key], f"listing.{key}")
    if item["listed_date"] is not None:
        try:
            date.fromisoformat(item["listed_date"])
        except ValueError:
            fail("listing.listed_date", "必须是 ISO 日期")


def validate_search_result(result, plan: SearchPlan, ctx: RunContext) -> None:
    """检查给 C 的完整返回值；输出问题统一使用 INVALID_OUTPUT。"""
    try:
        if type(result) is not dict or set(result) != {'status', 'data', 'issues', 'meta'}:
            fail('result', '字段集合与 Result 契约不一致')
        validate_type(typing.Literal['success', 'partial', 'error'], result['status'], 'result.status')
        validate_type(contracts.SearchResult | None, result['data'], 'result.data')
        validate_type(list[contracts.Issue], result['issues'], 'result.issues')
        validate_type(contracts.ResultMeta, result['meta'], 'result.meta')
        if any(result['meta'][k] != ctx[k] for k in ('trace_id', 'call_id')):
            fail('result.meta', '追踪标识与本次调用不一致')
        if result['meta']['duration_ms'] < 0:
            fail('result.meta.duration_ms', '不能为负数')
        if (result['status'] == 'error') != (result['data'] is None):
            fail('result.data', 'error 不携带数据；success/partial 必须携带数据')
        if result['status'] != 'success' and not result['issues']:
            fail('result.issues', 'partial/error 必须说明未完成的原因')
        if result['status'] == 'success' and result['issues']:
            fail('result.issues', '存在未解决问题时不能返回 success')
        data = result['data']
        if data is None:
            return
        if any(data[k] != plan[k] for k in ('plan_id', 'profile_version')):
            fail('result.data', '输出的计划或画像版本不一致')
        if len(data['items']) > plan['candidate_limit']:
            fail('result.data.items', '超过本次候选上限')
        keys = set()
        for index, listing in enumerate(data['items']):
            validate_listing(listing)
            if listing['source_mode'] != ctx['source_mode'] or listing['listing_key'] in keys:
                fail(f'result.data.items[{index}]', '房源模式不一致或 listing_key 重复')
            keys.add(listing['listing_key'])
        coverage = data['coverage']
        if not set(coverage['failed_sources']) <= set(coverage['queried_sources']):
            fail('result.data.coverage.failed_sources', '失败来源必须是实际查询过的来源')
        if set(coverage['applied_filters']) & set(coverage['unsupported_filters']):
            fail('result.data.coverage', '同一条件不能同时声明已应用与不支持')
        if coverage['has_more'] != bool(coverage['next_pages']):
            fail('result.data.coverage.has_more', '必须与可靠续页游标一致')
        query_ids, next_ids = {q['query_id'] for q in plan['queries']}, set()
        for page in coverage['next_pages']:
            if page['query_id'] not in query_ids or page['query_id'] in next_ids or not page['cursor'].strip():
                fail('result.data.coverage.next_pages', '续页必须对应唯一的计划内查询和非空游标')
            next_ids.add(page['query_id'])
        if coverage['queries_completed'] and coverage['has_more']:
            fail('result.data.coverage', '已查完不能仍有未执行续页')
        if result['status'] == 'success' and (
                not coverage['queries_completed'] or coverage['truncated'] or coverage['failed_sources']):
            fail('result.status', '搜索未完成、截断或来源失败时不能返回 success')
    except ContractViolation as exc:
        raise ContractViolation('INVALID_OUTPUT', exc.field_path, str(exc)) from exc
