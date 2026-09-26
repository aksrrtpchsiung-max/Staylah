"""4/5：整理已取得的事实并返回 Result[SearchResult]，不派工、不筛选或推荐。

价格保留原币种和 month/week/total 口径，面积沿用 3a 的 sqft；不猜测汇率、
租金周期或缺失字段。日期时间统一为 UTC，原始页面文字及证据原文保留。
"""
from copy import deepcopy
from datetime import timezone
from pathlib import Path
import sys
from time import monotonic
from typing import get_args, get_type_hints


from property_agent.contracts import ContractViolation, Listing, ListingAttributes, Result, SearchResult
from property_agent.search.execution.history import fingerprint
from property_agent.domain.validation import timestamp, validate_listing, validate_search_result
from property_agent.search.providers.base import issue


def _unique(values):
    return list(dict.fromkeys(values))


def normalize_listing(listing: Listing) -> Listing:
    """只规范已有结构化值；不从自由文本生成新事实，不修改调用方数据。"""
    validate_listing(listing)
    result = deepcopy(listing)
    result['price']['currency'] = result['price']['currency'].strip().upper()
    for key in ('fetched_at', 'source_updated_at', 'last_verified_at'):
        if result[key] is not None:
            result[key] = timestamp(result[key], key).astimezone(timezone.utc).isoformat()
    for fact in result['evidence']:
        fact['observed_at'] = timestamp(fact['observed_at'], 'evidence.observed_at').astimezone(timezone.utc).isoformat()
        if fact['field'] == 'price.currency' and isinstance(fact['value'], str):
            fact['value'] = fact['value'].strip().upper()
    for key in ('raw_details', 'field_issues'):
        result[key] = _unique(result[key])
    result['price']['evidence_ids'] = _unique(result['price']['evidence_ids'])
    return result


def merge_listing(existing: Listing, incoming: Listing) -> Listing:
    """同一来源、同一房源合并；重命名冲突证据时同步更新所有价格引用。"""
    result, incoming = normalize_listing(existing), normalize_listing(incoming)
    if any(result[k] != incoming[k] for k in ('listing_key', 'source', 'source_mode')):
        raise ContractViolation('INVALID_OUTPUT', 'listing.listing_key', '不能合并不同来源或身份的房源')
    evidence = {e['evidence_id']: e for e in result['evidence']}
    remapped = {}
    for fact in incoming['evidence']:
        original_id = fact['evidence_id']
        eid = original_id
        if eid in evidence and evidence[eid] != fact:
            eid = original_id + ':' + fingerprint(fact)
            suffix = 1
            # 同样的事实再次合并时复用同一个 ID，不无限追加证据。
            while eid in evidence and dict(evidence[eid], evidence_id=original_id) != fact:
                eid = original_id + ':' + fingerprint(fact) + ':' + str(suffix)
                suffix += 1
        remapped[original_id] = eid
        evidence[eid] = dict(fact, evidence_id=eid)
    result['evidence'] = list(evidence.values())
    result['raw_details'] = _unique(result['raw_details'] + incoming['raw_details'])
    result['field_issues'] = _unique(result['field_issues'] + incoming['field_issues'])
    price_ids = _unique(result['price']['evidence_ids'] + [remapped[eid] for eid in incoming['price']['evidence_ids']])
    for field in ('amount', 'currency', 'period'):
        old, new = result['price'][field], incoming['price'][field]
        if old is not None and new is not None and old != new:
            result['field_issues'].append('price.' + field + ':conflict')
        elif old is None:
            result['price'][field] = new
    if (result['price']['status'] == 'conflict' or incoming['price']['status'] == 'conflict'
            or any(f.startswith('price.') and f.endswith(':conflict') for f in result['field_issues'])):
        result['price'].update(amount=None, status='conflict')
        if 'price.period:conflict' in result['field_issues']:
            result['price']['period'] = None
    else:
        result['price']['status'] = 'known' if result['price']['amount'] is not None else 'unknown'
    result['price']['evidence_ids'] = price_ids
    for target, source, prefix, schema in (
        (result, incoming, '', Listing), (result['attributes'], incoming['attributes'], 'attributes.', ListingAttributes)
    ):
        fields = ('bedrooms', 'location_id', 'listing_status', 'listed_date', 'source_listing_id') if not prefix else tuple(source)
        hints = get_type_hints(schema)
        for key in fields:
            old, new = target[key], source[key]
            conflict = prefix + key + ':conflict'
            if conflict in result['field_issues'] or (old not in (None, 'unknown') and new not in (None, 'unknown') and old != new):
                result['field_issues'].append(conflict)
                target[key] = 'unknown' if 'unknown' in get_args(hints[key]) else None
            elif old in (None, 'unknown'):
                target[key] = new
    if result['transaction_type'] != incoming['transaction_type']:
        # 契约不允许 unknown 交易类型，保留首条并明确冲突，不能判定匹配。
        result['field_issues'].append('transaction_type:conflict')
    if not result['source_url']:
        result['source_url'] = incoming['source_url']
    if timestamp(incoming['fetched_at'], 'incoming.fetched_at') >= timestamp(result['fetched_at'], 'existing.fetched_at'):
        if incoming['raw_description']:
            result['raw_description'] = incoming['raw_description']
        if incoming['title'].strip():
            result['title'] = incoming['title']
    elif not result['raw_description']:
        result['raw_description'] = incoming['raw_description']
    for key in ('fetched_at', 'source_updated_at', 'last_verified_at'):
        dates = [value for value in (result[key], incoming[key]) if value is not None]
        result[key] = max(dates, key=lambda value: timestamp(value, key)) if dates else None
    result['field_issues'] = _unique(result['field_issues'])
    validate_listing(result)
    return result


def error_result(problem, ctx, started) -> Result[SearchResult]:
    """边界输入不合法时也返回契约外壳，且不回显密钥或异常堆栈。"""
    context = ctx if isinstance(ctx, dict) else {}
    return dict(status='error', data=None, issues=[deepcopy(problem)], meta=dict(
        trace_id=context.get('trace_id', '') if isinstance(context.get('trace_id', ''), str) else '',
        call_id=context.get('call_id', '') if isinstance(context.get('call_id', ''), str) else '',
        duration_ms=max(0, int((monotonic() - started) * 1000))))


def aggregate(state, started) -> Result[SearchResult]:
    """从管理层最终状态构建给 C 的快照；完整无匹配仍返回 success 空列表。"""
    plan, ctx = state['plan'], state['ctx']
    queries, pages = state['queries'], state['pages']
    problems = deepcopy(state['issues'] + [p for group in state['task_issues'].values() for p in group])
    queried_sources = _unique(entry['source'] for entry in state['history'] if entry['kind'] == 'search_page')
    # 只统计最终仍失败的尝试；已成功重试的历史错误不污染最终覆盖。
    latest = {entry['task_id']: entry for entry in state['history']}
    failed_sources = _unique(entry['source'] for tid, entry in latest.items()
        if entry['source'] in queried_sources and entry['status'] != 'success'
        and any(p['code'] not in {'RETRIEVAL_DEGRADED', 'BUDGET_EXHAUSTED'}
                for p in state['task_issues'].get(tid, [])))
    applied = set(pages[0]['applied_filters']) if pages else set()
    reported = set()
    unsupported = set()
    for page in pages:
        applied.intersection_update(page['applied_filters'])
        reported.update(page['applied_filters'])
        unsupported.update(page['unsupported_filters'])
    unsupported.update(reported - applied)
    applied -= unsupported
    next_pages = [dict(kind='next_page', query_id=qid, cursor=q['cursor'])
        for qid, q in queries.items() if not q['done'] and not q['blocked'] and q['cursor'] is not None]
    completed = all(q['done'] for q in queries.values())
    coverage = dict(queried_sources=queried_sources, failed_sources=failed_sources,
        queries_completed=completed, has_more=bool(next_pages), next_pages=next_pages,
        truncated=any(p['truncated'] for p in pages) or state['stop_reason'] in {'budget', 'deadline'},
        applied_filters=sorted(applied), unsupported_filters=sorted(unsupported))
    items_by_key = {}
    for listing in state['listings'].values():
        key = listing['listing_key']
        items_by_key[key] = merge_listing(items_by_key[key], listing) if key in items_by_key else normalize_listing(listing)
    items = list(items_by_key.values())
    for index, listing in enumerate(items):
        if listing['field_issues']:
            problems.append(issue('RETRIEVAL_DEGRADED', '房源仍有未解决字段：' + ', '.join(listing['field_issues']),
                field_path=f'result.data.items[{index}].field_issues', source=listing['source']))
    if not completed and not problems:
        problems.append(issue('RETRIEVAL_DEGRADED', '仍有查询未完成，现有覆盖不能视为完整搜索', source=None))
    if coverage['truncated'] and not problems:
        problems.append(issue('RETRIEVAL_DEGRADED', '来源结果被截断，保留当前已取得的房源', source=None))
    if not pages and not items and plan['queries'] and not problems:
        problems.append(issue('INVALID_STATE', '没有取得任何有效搜索页，不能声明搜索成功', source=None))
    problems = list({fingerprint(p): p for p in problems}.values())
    data = dict(plan_id=plan['plan_id'], profile_version=plan['profile_version'], items=items, coverage=coverage)
    status = 'partial' if problems or not completed else 'success'
    if not pages and problems and not items:
        data, status = None, 'error'
    result = dict(status=status, data=data, issues=problems, meta=dict(trace_id=ctx['trace_id'], call_id=ctx['call_id'],
        duration_ms=max(0, int((monotonic() - started) * 1000))))
    validate_search_result(result, plan, ctx)
    return result


if __name__ == '__main__':
    import argparse
    import asyncio
    import json
    from property_agent.search.api import create_live_search_service

    parser = argparse.ArgumentParser(description='用真实 2/3a/3b 的最终状态检查 4/5；不生成虚拟房源')
    parser.add_argument('--input', type=Path, required=True, help='至少三组实际 {plan, ctx} JSON 输入')
    parser.add_argument('--output', type=Path, help='保存实际内部状态及给 C 的返回值')
    args = parser.parse_args()

    async def main():
        cases = json.loads(args.input.read_text())
        if not isinstance(cases, list) or len(cases) < 3:
            parser.error('需要至少三组真实业务输入')
        service, records, passed = create_live_search_service(), [], 0
        for case in cases:
            if case['ctx']['source_mode'] != 'live':
                parser.error('这里只接受真实 live 输入')
            started = monotonic()
            state = await service.run(case['plan'], ctx=case['ctx'])
            before = deepcopy(state)
            result = aggregate(state, started)
            validate_search_result(result, case['plan'], case['ctx'])
            accepted = state == before and result['status'] != 'error'
            for item in result['data']['items'] if result['data'] else []:
                # 实际房源重复汇总必须幂等；不构造预设响应或篡改页面事实。
                normalized = normalize_listing(item)
                accepted = accepted and merge_listing(normalized, normalized) == normalized
            passed += accepted
            record = dict(input=case, aggregation_input=before, output=result, verified=accepted)
            records.append(record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
        if args.output:
            args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2))
        print(f'真实 4/5 汇总通过 {passed}/{len(cases)}')
        return 0 if passed == len(cases) else 1

    raise SystemExit(asyncio.run(main()))
