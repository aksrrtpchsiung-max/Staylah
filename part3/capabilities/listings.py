"""3a：供 LangGraph 执行节点 await 的找房/详情能力。

运行真实搜索/详情测试：python3 -m part3.capabilities.listings
也支持直接运行本文件，路径解析不依赖当前工作目录。
本模块不生成搜索计划、不决定翻页、不执行 C 的硬条件筛选。
"""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys
from time import monotonic
from typing import get_args, get_type_hints
from urllib.parse import urlparse

# 直接执行文件时 Python 只加入脚本目录，需在项目内导入前补上根目录。
# 正常包导入及 python -m 不修改 sys.path。
if __name__ == "__main__" and __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from contracts_v0 import ContractViolation, Listing, ListingAttributes, Price, Result, RunContext, SearchPlan
from execution.budget import SearchBudget
from execution.tasks import ListingDetail, ListingPage
from part1.validation import fail, timestamp, validate_context, validate_listing, validate_plan, validate_type
from providers.base import ListingProvider, ProviderError, issue


def _fact_type(field):
    if field.startswith("attributes."):
        return get_type_hints(ListingAttributes).get(field.removeprefix("attributes."))
    if field in {"price.amount", "price.currency", "price.period"}:
        return get_type_hints(Price)[field.removeprefix("price.")]
    if field in {"title", "bedrooms", "location_id", "listing_status", "listed_date", "source_updated_at"}:
        return get_type_hints(Listing)[field]
    return None


def merge_detail(listing: Listing, detail: ListingDetail) -> Listing:
    """保留输入快照和两侧证据；冲突价格不会被后一条来源覆盖。"""
    validate_listing(listing)
    validate_type(ListingDetail, detail, "detail")
    timestamp(detail["fetched_at"], "detail.fetched_at")
    expected_id = listing["source_listing_id"]
    if expected_id is None and listing["source_url"]:
        match = re.search(r"(?:/|-)([0-9]+)/?$", urlparse(listing["source_url"]).path)
        expected_id = match.group(1) if match else None
    if expected_id is None or detail["source_listing_id"] != expected_id:
        fail("detail.source_listing_id", "详情不属于请求的房源")
    result = deepcopy(listing)
    if detail["raw_description"]:
        result["raw_description"] = detail["raw_description"]
    result["raw_details"] = list(dict.fromkeys(result["raw_details"] + detail["raw_details"]))
    if timestamp(detail["fetched_at"], "detail.fetched_at") > timestamp(result["fetched_at"], "listing.fetched_at"):
        result["fetched_at"] = detail["fetched_at"]
    price_values = {key: set() for key in ("amount", "currency", "period")}
    for key in price_values:
        old = result["price"][key]
        if old is not None:
            price_values[key].add(old)
    evidence_ids = {e["evidence_id"] for e in result["evidence"]}
    for fact in detail["facts"]:
        field, value = fact["field"], fact["value"]
        schema = _fact_type(field)
        if schema is None:
            fail(f"detail.facts.{field}", "详情字段不在 Listing 可补充字段中")
        validate_type(schema, value, f"detail.facts.{field}")
        if value is None or value == "unknown":
            continue
        if not fact["excerpt"].strip():
            fail(f"detail.facts.{field}", "事实必须附带页面原文")
        if type(value) is int and value < 0:
            fail(f"detail.facts.{field}", "数值不能为负数")
        identity = json.dumps([field, value, detail["source_url"], detail["fetched_at"], fact["excerpt"]],
                              ensure_ascii=False, sort_keys=True)
        eid = f'{listing["listing_key"]}:detail:{hashlib.sha256(identity.encode()).hexdigest()[:20]}'
        if eid not in evidence_ids:
            result["evidence"].append(dict(evidence_id=eid, field=field, value=value,
                source_url=detail["source_url"], observed_at=detail["fetched_at"], excerpt=fact["excerpt"]))
            evidence_ids.add(eid)
        if field.startswith("price."):
            price_values[field.split(".")[1]].add(value)
            if eid not in result["price"]["evidence_ids"]:
                result["price"]["evidence_ids"].append(eid)
            continue
        target, key = (result["attributes"], field.split(".")[1]) if field.startswith("attributes.") else (result, field)
        old = target[key]
        conflict_key = f"{field}:conflict"
        if conflict_key in result["field_issues"]:
            continue  # 后续详情不能静默消除已有冲突。
        if old not in (None, "unknown", "") and old != value and field != "title":
            result["field_issues"].append(conflict_key)
            target[key] = "unknown" if "unknown" in get_args(schema) else None
        else:
            target[key] = value
    conflict = result["price"]["status"] == "conflict"
    for key, values in price_values.items():
        if len(values) > 1:
            conflict = True
            result["field_issues"].append(f"price.{key}:conflict")
        elif values:
            result["price"][key] = next(iter(values))
    if conflict:
        result["price"].update(amount=None, status="conflict")
        if len(price_values["period"]) > 1:
            result["price"]["period"] = None
    else:
        result["price"]["status"] = "known" if result["price"]["amount"] is not None else "unknown"
    # Reaching this point means the provider returned a valid detail payload
    # for this exact listing. Preserve when that verification happened.
    result["last_verified_at"] = detail["fetched_at"]
    result["field_issues"] = list(dict.fromkeys(result["field_issues"]))
    validate_listing(result)
    return result


def _result(data, problems, ctx, started):
    context = ctx if isinstance(ctx, dict) else {}
    trace_id = context.get("trace_id", "")
    call_id = context.get("call_id", "")
    return dict(status="error" if data is None else "partial" if problems else "success",
                data=data, issues=problems, meta=dict(trace_id=trace_id if isinstance(trace_id, str) else "",
                call_id=call_id if isinstance(call_id, str) else "", duration_ms=max(0, int((monotonic() - started) * 1000))))


class ListingsCapability:
    def __init__(self, provider: ListingProvider, budget: SearchBudget):
        self.provider = provider
        self.budget = budget

    def _context(self, ctx):
        validate_context(ctx)
        if self.provider.source_mode != ctx["source_mode"]:
            fail("ctx.source_mode", "上下文与注入的 Provider 模式不一致")
        self.budget.check(ctx)

    async def search_page(self, plan: SearchPlan, query_id: str, *,
                          ctx: RunContext, cursor: str | None = None, constraints=None) -> Result[ListingPage]:
        """cursor 为管理层从上次 next_cursor 得到的续页指令；不改变计划。"""
        started = monotonic()
        try:
            validate_plan(plan, ctx)
            self._context(ctx)
            query = next((deepcopy(q) for q in plan["queries"] if q["query_id"] == query_id), None)
            if query is None:
                fail("query_id", "查询不在计划中")
            if query["source"] != self.provider.source:
                fail("query.source", "查询来源与 Provider 不一致")
            if cursor is not None:
                if not isinstance(cursor, str) or not cursor.strip():
                    fail("cursor", "续页游标必须是非空字符串")
                query["cursor"] = cursor
            async with asyncio.timeout(self.budget.work_seconds(ctx)):
                async with self.budget.lock:
                    limit = self.budget.begin_page(plan, ctx)
                    page = await self.provider.search_page(query, intent=plan["intent"],
                        filters=deepcopy(plan["required_filters"]), limit=limit, ctx=ctx,
                        **({'constraints': deepcopy(constraints)} if constraints else {}))
                    try:
                        validate_type(ListingPage, page, "page")
                        if page["query_id"] != query_id or len(page["items"]) > limit:
                            fail("page", "来源查询 ID 或候选额度不符")
                        for item in page["items"]:
                            validate_listing(item)
                            if item["source_mode"] != ctx["source_mode"] or item["source"] != query["source"]:
                                fail("page.items", "返回房源来源或模式不一致")
                    except ContractViolation as exc:
                        raise ProviderError(issue("INVALID_OUTPUT", str(exc), field_path=exc.field_path)) from exc
                    self.budget.candidates_used += len(page["items"])
            problems = list(page["issues"])
            if not page["pagination_known"] and not any(p["code"] == "RETRIEVAL_DEGRADED" for p in problems):
                problems.append(issue("RETRIEVAL_DEGRADED", "无法确认搜索是否还有下一页"))
            page["issues"] = problems
            return _result(page, problems, ctx, started)
        except ContractViolation as exc:
            return _result(None, [issue(exc.code, str(exc), field_path=exc.field_path)], ctx, started)
        except ProviderError as exc:
            return _result(None, [exc.issue], ctx, started)
        except TimeoutError:
            return _result(None, [issue("TIMEOUT", "搜索达到截止时间", retryable=True)], ctx, started)

    async def read_detail(self, listing: Listing, *, ctx: RunContext) -> Result[Listing]:
        started = monotonic()
        try:
            validate_context(ctx)
            validate_listing(listing)
            if (listing["source"] != self.provider.source or listing["source_mode"] != ctx["source_mode"]
                    or self.provider.source_mode != ctx["source_mode"]):
                fail("listing.source", "房源与 Provider / 上下文不一致")
        except ContractViolation as exc:
            return _result(None, [issue(exc.code, str(exc), field_path=exc.field_path)], ctx, started)
        try:
            self._context(ctx)
            async with asyncio.timeout(self.budget.work_seconds(ctx)):
                async with self.budget.lock:
                    self.budget.check(ctx)
                    detail = await self.provider.read_detail(deepcopy(listing), ctx=ctx)
                    merged = merge_detail(listing, detail)
            return _result(merged, [], ctx, started)
        except (ProviderError, ContractViolation, TimeoutError) as exc:
            if isinstance(exc, ProviderError):
                problem = exc.issue
            elif isinstance(exc, ContractViolation):
                problem = issue("INVALID_OUTPUT", str(exc), field_path=exc.field_path)
            else:
                problem = issue("TIMEOUT", "详情达到截止时间", retryable=True)
            original = deepcopy(listing)
            original["field_issues"] = list(dict.fromkeys(original["field_issues"] + [f'detail:{problem["code"]}']))
            return _result(original, [problem], ctx, started)


if __name__ == '__main__':
    import argparse
    from datetime import datetime, timedelta, timezone
    from uuid import uuid4
    from providers.guru_search import GuruSearchProvider

    def verify_observed_details(listing):
        """对照真实页面明确条目验收字段和证据，不预设房源或服务响应。"""
        observed = {
            ('furnished-o', 'Fully furnished'): ('attributes.furnishing', 'fully'),
            ('furnished-o', 'Unfurnished'): ('attributes.furnishing', 'unfurnished'),
            ('people-o', 'Staying with owner'): ('attributes.owner_stays', True),
            ('people-o', 'No Owner Stays'): ('attributes.owner_stays', False),
            ('document-with-lines-o', 'Utilities included'): ('attributes.utilities_included', True),
            ('wifi-2-f', 'Wi-Fi included'): ('attributes.wifi_included', True),
            ('cooker-o', 'No cooking'): ('attributes.cooking_policy', 'none'),
            ('people-behind-o', 'Visitors not allowed'): ('attributes.visitors_allowed', False),
            ('pet-o', 'Pets not allowed'): ('attributes.pets_allowed', False),
            ('room-o', 'Common room (Shared bath)'): ('attributes.ensuite_bathroom', False),
            ('calendar-days-o', '99-year lease'): ('attributes.lease_years', 99),
        }
        checked, failures = 0, []
        for raw in listing['raw_details']:
            label, separator, value = raw.partition(':')
            if not separator:
                continue
            label, value = label.strip(), value.strip()
            expected = observed.get((label, value))
            if label == 'calendar-time-o' and value.startswith('Listed on '):
                try:
                    date = datetime.strptime(value.removeprefix('Listed on '), '%d %b %Y').date().isoformat()
                except ValueError:
                    failures.append('挂牌日期原文格式变化，需要检查：' + raw)
                    continue
                expected = ('listed_date', date)
            if expected is None:
                continue
            field, value = expected
            actual = listing
            for key in field.split('.'):
                actual = actual[key]
            checked += 1
            if type(actual) is not type(value) or actual != value:
                failures.append(f'{field} 未保留明确详情事实：{raw}')
            if not any(e['field'] == field and type(e['value']) is type(value) and e['value'] == value
                       and ':detail:' in e['evidence_id'] for e in listing['evidence']):
                failures.append(f'{field} 缺少对应详情证据：{raw}')
        if not checked:
            failures.append('没有取得可验证的明确详情条目')
        return checked, failures

    parser = argparse.ArgumentParser(description='3a 的真实搜索/详情检查；输入 SearchPlan，输出实际 Result')
    parser.add_argument('--input', type=Path, help='包含至少三组真实 {plan, ctx} 的 JSON 文件')
    parser.add_argument('--output', type=Path, help='保存真实输入输出记录')
    args = parser.parse_args()

    async def main():
        if args.input:
            payload = json.loads(args.input.read_text())
            cases = payload if isinstance(payload, list) else [payload]
            if len(cases) < 3:
                parser.error('需要至少三组真实业务输入')
        else:
            cases=[]
            for area, maximum in [('Tampines', 4000), ('Clementi', 4500), ('Punggol', 4000)]:
                identity=str(uuid4())
                plan=dict(plan_id=identity, profile_version=1, attempt_id=identity, intent='rent',
                    required_filters=dict(currency='SGD', max_price=maximum, price_period='month',
                        rental_scope='whole_unit', locations=[area.upper()], min_bedrooms=2),
                    queries=[dict(query_id='q-'+area.lower(), source='propertyguru', text=area, cursor=None)],
                    page_limit=1, candidate_limit=1, source_mode='live', reason='真实 3a 输入输出检查')
                ctx=dict(user_id='live-check', run_id=identity, conversation_id=identity, attempt_id=identity,
                    trace_id=identity, call_id=identity, source_mode='live',
                    deadline_at=(datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat())
                cases.append(dict(plan=plan, ctx=ctx))
        records=[]
        passed=0
        for case in cases:
            plan, ctx=case['plan'], case['ctx']
            if ctx['source_mode'] != 'live':
                parser.error('验收只接受 live 输入，不使用虚拟响应')
            cap=ListingsCapability(GuruSearchProvider(), SearchBudget(plan, ctx))
            output=await cap.search_page(plan, plan['queries'][0]['query_id'], ctx=ctx)
            details=[]
            if output['data']:
                for item in output['data']['items']:
                    details.append(await cap.read_detail(item, ctx=ctx))
            failures, checked_facts = [], 0
            if output['data']:
                page = output['data']
                filters = plan['required_filters']
                for item in page['items']:
                    expected_type = 'sale' if plan['intent'] == 'buy' else 'rent'
                    if item['transaction_type'] != expected_type:
                        failures.append(item['listing_key'] + ': 交易类型与查询不符')
                    price = item['price']
                    if ('price.amount' in page['applied_filters'] and price['status'] == 'known'
                            and price['currency'] == filters['currency']
                            and price['period'] == filters['price_period']
                            and price['amount'] > filters['max_price']):
                        failures.append(item['listing_key'] + ': 搜索结果混入超预算房源')
                if page['truncated'] and not page['next_cursor']:
                    failures.append('候选截断后必须保留真实续页游标')
            for detail in details:
                if detail['data'] is not None:
                    checked, problems = verify_observed_details(detail['data'])
                    checked_facts += checked
                    failures.extend(detail['data']['listing_key'] + ': ' + problem for problem in problems)
            accepted=bool(output['data'] and output['data']['items'] and details
                          and all(x['status']=='success' for x in details) and not failures)
            passed+=accepted
            record=dict(input=case, search_output=output, detail_outputs=details,
                        checked_detail_facts=checked_facts, failures=failures, live_verified=accepted)
            records.append(record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
        if args.output:
            args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2))
        print(f'真实 3a 搜索及详情通过 {passed}/{len(cases)}；没有真实返回就不会通过。')
        return 0 if passed==len(cases) else 1

    raise SystemExit(asyncio.run(main()))
