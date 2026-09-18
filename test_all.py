"""四组真实 A→1→search 输入输出：直接运行，使用 .env 的实际外部服务。

与 test_search.py 相同，下面的字典可以直接修改。每组包含制定计划的四项业务输入，
ctx 由运行函数补齐。打印 SearchPlan 和 SearchResult，候选交给 C 继续筛选推荐。
"""
import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import uuid4

from api import build_search_plan, search
from contracts_v0 import ContractViolation
from part1.validation import validate_plan_result, validate_search_result


search_input_1 = {
    "profile": {
        "profile_id": "profile-tampines-whole", "version": 1, "intent": "rent",
        "hard_constraints": {
            "currency": "SGD", "max_price": 4000, "price_period": "month",
            "rental_scope": "whole_unit", "locations": ["TAMPINES"], "min_bedrooms": 2,
        },
        "preferences": [], "unresolved": [],
        "field_sources": {
            "intent": "msg-tampines", "hard_constraints.currency": "msg-tampines",
            "hard_constraints.max_price": "msg-tampines", "hard_constraints.price_period": "msg-tampines",
            "hard_constraints.rental_scope": "msg-tampines", "hard_constraints.locations": "msg-tampines",
            "hard_constraints.min_bedrooms": "msg-tampines",
        },
    },
    "query": {
        "profile_version": 1,
        "entities": [{"type": "location", "raw_text": "淡滨尼", "canonical_id": "TAMPINES",
                      "aliases": ["Tampines", "淡滨尼"]}],
        "semantic_query": "在 Tampines 找整套出租，月租不超过 SGD 4000，至少两个卧室。",
        "unresolved": [],
    },
    "previous_attempts": [],
    "directive": None,
}

search_input_2 = {
    "profile": {
        "profile_id": "profile-clementi-room", "version": 1, "intent": "rent",
        "hard_constraints": {
            "currency": "SGD", "max_price": 1500, "price_period": "month",
            "rental_scope": "room", "locations": ["CLEMENTI"], "min_bedrooms": None,
        },
        "preferences": [], "unresolved": [],
        "field_sources": {
            "intent": "msg-clementi", "hard_constraints.currency": "msg-clementi",
            "hard_constraints.max_price": "msg-clementi", "hard_constraints.price_period": "msg-clementi",
            "hard_constraints.rental_scope": "msg-clementi", "hard_constraints.locations": "msg-clementi",
            "hard_constraints.min_bedrooms": "msg-clementi",
        },
    },
    "query": {
        "profile_version": 1,
        "entities": [{"type": "location", "raw_text": "金文泰", "canonical_id": "CLEMENTI",
                      "aliases": ["Clementi", "金文泰"]}],
        "semantic_query": "在 Clementi 找单间出租，月租不超过 SGD 1500，不限制整套房屋的卧室总数。",
        "unresolved": [],
    },
    "previous_attempts": [],
    "directive": None,
}

search_input_3 = {
    "profile": {
        "profile_id": "profile-punggol-whole", "version": 1, "intent": "rent",
        "hard_constraints": {
            "currency": "SGD", "max_price": 4500, "price_period": "month",
            "rental_scope": "whole_unit", "locations": ["PUNGGOL"], "min_bedrooms": 3,
        },
        "preferences": [], "unresolved": [],
        "field_sources": {
            "intent": "msg-punggol", "hard_constraints.currency": "msg-punggol",
            "hard_constraints.max_price": "msg-punggol", "hard_constraints.price_period": "msg-punggol",
            "hard_constraints.rental_scope": "msg-punggol", "hard_constraints.locations": "msg-punggol",
            "hard_constraints.min_bedrooms": "msg-punggol",
        },
    },
    "query": {
        "profile_version": 1,
        "entities": [{"type": "location", "raw_text": "榜鹅", "canonical_id": "PUNGGOL",
                      "aliases": ["Punggol", "榜鹅"]}],
        "semantic_query": "在 Punggol 找适合家庭的整套出租，月租不超过 SGD 4500，至少三个卧室。",
        "unresolved": [],
    },
    "previous_attempts": [],
    "directive": None,
}

search_input_4 = {
    "profile": {
        "profile_id": "profile-bishan-buy", "version": 1, "intent": "buy",
        "hard_constraints": {
            "currency": "SGD", "max_price": 1200000, "price_period": "total",
            "rental_scope": None, "locations": ["BISHAN"], "min_bedrooms": 2,
        },
        "preferences": [], "unresolved": [],
        "field_sources": {
            "intent": "msg-bishan", "hard_constraints.currency": "msg-bishan",
            "hard_constraints.max_price": "msg-bishan", "hard_constraints.price_period": "msg-bishan",
            "hard_constraints.rental_scope": "msg-bishan", "hard_constraints.locations": "msg-bishan",
            "hard_constraints.min_bedrooms": "msg-bishan",
        },
    },
    "query": {
        "profile_version": 1,
        "entities": [{"type": "location", "raw_text": "碧山", "canonical_id": "BISHAN",
                      "aliases": ["Bishan", "碧山"]}],
        "semantic_query": "在 Bishan 找出售房源，总价不超过 SGD 1200000，至少两个卧室；租赁范围不适用。",
        "unresolved": [],
    },
    "previous_attempts": [],
    "directive": None,
}

INPUTS = (search_input_1, search_input_2, search_input_3, search_input_4)


async def run_case(profile, query, previous_attempts, directive, *, plan_only=False):
    identity = uuid4().hex
    ctx = dict(user_id='test-all', run_id=identity, conversation_id=identity,
               attempt_id='attempt-' + identity, trace_id=identity, call_id='plan-' + uuid4().hex,
               deadline_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
               source_mode='live')
    request = dict(profile=profile, query=query, previous_attempts=previous_attempts, directive=directive)
    before = deepcopy(request)
    plan_ctx = deepcopy(ctx)
    print('\n需求：' + query['semantic_query'], flush=True)
    print('1. 四项输入 → build_search_plan', flush=True)
    print(json.dumps(dict(**before, ctx=plan_ctx), ensure_ascii=False, indent=2), flush=True)

    plan_result = await build_search_plan(profile, query, previous_attempts, directive, ctx=ctx)
    print('1. 实际生成的 SearchPlan', flush=True)
    print(json.dumps(plan_result, ensure_ascii=False, indent=2), flush=True)
    failures = []
    try:
        validate_plan_result(plan_result, profile, plan_ctx)
    except ContractViolation as exc:
        failures.append(f'{exc.code}: {exc.field_path}: {exc}')
    if request != before or ctx != plan_ctx:
        failures.append('计划生成修改了输入')
    if plan_result['status'] != 'success':
        failures.append('真实计划生成未成功，不进入搜索')
    record = dict(input=dict(**before, ctx=plan_ctx), plan_output=plan_result,
                  search_input=None, search_output=None, passed=False, failures=failures)
    if failures or plan_only:
        record['passed'] = not failures
        return record

    # 直接传递 1 的 data；不手写、不替换查询，不重置整条链路的截止时间。
    plan = plan_result['data']
    search_ctx = dict(ctx, call_id='search-' + uuid4().hex)
    record['search_input'] = deepcopy(dict(plan=plan, ctx=search_ctx))
    print('2. search 接收上一步的 SearchPlan，开始真实搜索、详情与定位', flush=True)
    result = await search(plan, ctx=search_ctx)
    record['search_output'] = result
    print('3. 实际 SearchResult（候选房源、证据与覆盖情况，供 C 筛选推荐）', flush=True)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    try:
        validate_search_result(result, plan, search_ctx)
        if dict(plan=plan, ctx=search_ctx) != record['search_input']:
            failures.append('搜索修改了输入')
        if result['status'] == 'error':
            failures.append('真实搜索失败')
        if any(p['code'] != 'BUDGET_EXHAUSTED' for p in result['issues']):
            failures.append('实际调用存在非额度问题，详见 search_output.issues')
        if result['data']:
            data = result['data']
            if not data['items'] and not data['coverage']['queries_completed']:
                failures.append('没有房源且搜索未完成')
            for listing in data['items']:
                if listing['source_mode'] != 'live' or not listing['source_url']:
                    failures.append(listing['listing_key'] + ' 缺少真实来源')
                if not any(':detail:' in e['evidence_id'] for e in listing['evidence']):
                    failures.append(listing['listing_key'] + ' 未取得真实详情证据')
                if not any(e['field'] == 'location' for e in listing['evidence']):
                    failures.append(listing['listing_key'] + ' 未取得真实定位证据')
    except ContractViolation as exc:
        failures.append(f'{exc.code}: {exc.field_path}: {exc}')
    record['passed'] = not failures
    return record


def search_all(profile, query, previous_attempts, directive):
    """编辑器中可像 test_search.search(plan) 一样单独运行一组。"""
    return asyncio.run(run_case(profile, query, previous_attempts, directive))


async def run_all(*, plan_only=False, output=None, cases=INPUTS):
    records = []
    for case in cases:
        record = await run_case(**deepcopy(case), plan_only=plan_only)
        records.append(record)
        print('本组验收：' + ('通过' if record['passed'] else '失败：' + '；'.join(record['failures'])), flush=True)
        if output is not None:
            Path(output).write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    passed = sum(r['passed'] for r in records)
    label = '真实计划生成' if plan_only else '真实计划生成 → search 联调'
    print(f'\n{label}通过 {passed}/{len(records)}', flush=True)
    return 0 if passed == len(records) else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='四组真实计划生成与搜索联合调试')
    parser.add_argument('--plan-only', action='store_true', help='只运行 1 的真实模型输入输出')
    parser.add_argument('--output', type=Path, help='保存每组实际输入、计划和搜索结果 JSON')
    parser.add_argument('--case', type=int, choices=range(1, 5), help='仅运行指定一组；默认运行全部四组')
    args = parser.parse_args()
    cases = (INPUTS[args.case - 1],) if args.case else INPUTS
    raise SystemExit(asyncio.run(run_all(plan_only=args.plan_only, output=args.output, cases=cases)))
