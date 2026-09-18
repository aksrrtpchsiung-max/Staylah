"""搜索入口。业务参数遵守共享契约；模型与 Provider 在构造服务时注入。"""
import os
from pathlib import Path
from time import monotonic

from dotenv import dotenv_values

from contracts_v0 import ContractViolation, Result, RunContext, SearchPlan, SearchResult
from graph import SearchService
from part1.validation import validate_plan
from execution.budget import remaining_seconds
from part45.aggregation import error_result
from providers.base import ProviderError, issue


def create_live_search_service(*, env_file=None, model=None) -> SearchService:
    """默认使用现有 LLM Gateway、guru_search 和 OneMap；创建时不联网。"""
    from config import create_chat_model, load_model_settings
    from dataclasses import replace
    from providers.guru_search import GuruSearchProvider
    from providers.onemap import OneMapProvider
    path = Path(env_file) if env_file is not None else Path(__file__).resolve().with_name('.env')
    values = dict(dotenv_values(path, interpolate=False))
    values.update(os.environ)
    location_provider = OneMapProvider.from_env(path)
    location_provider.check_configuration()
    executable = values.get('OPENCLI_BIN')
    settings = load_model_settings(path) if model is None else None
    return SearchService(listing_providers=[GuruSearchProvider((executable,) if executable else None)],
        location_provider=location_provider,
        model=model if model is not None else create_chat_model(replace(settings, max_tokens=min(settings.max_tokens, 256))),
        model_timeout_seconds=min(settings.timeout_seconds, 60) if settings else 60)


async def search(plan: SearchPlan, *, ctx: RunContext) -> Result[SearchResult]:
    """便捷 live 入口；批量调用应复用 create_live_search_service() 的实例。"""
    from config import ModelConfigurationError
    started = monotonic()
    try:
        validate_plan(plan, ctx)
        remaining_seconds(ctx)
        if ctx['source_mode'] == 'mock':
            problem = issue('SOURCE_UNAVAILABLE', 'mock 模式请构造 SearchService 并注入模拟来源响应', source=None)
        else:
            result = await create_live_search_service().search(plan, ctx=ctx)
            result['meta']['duration_ms'] = max(0, int((monotonic() - started) * 1000))
            return result
    except ContractViolation as exc:
        problem = issue(exc.code, str(exc), field_path=exc.field_path, source=None)
    except ModelConfigurationError as exc:
        problem = issue('MODEL_UNAVAILABLE', str(exc), source='model')
    except ProviderError as exc:
        problem = exc.issue
    except Exception:
        problem = issue('INTERNAL_ERROR', '搜索服务初始化或调用失败', source=None)
    return error_result(problem, ctx, started)


if __name__ == '__main__':
    import argparse
    import asyncio
    from copy import deepcopy
    from datetime import datetime, timedelta, timezone
    import json
    from uuid import uuid4
    from part1.validation import validate_search_result
    from part45.aggregation import merge_listing, normalize_listing

    parser = argparse.ArgumentParser(description='公开 search 的真实全链路输入输出测试：2→3a→3b→4→5')
    parser.add_argument('--input', type=Path, help='至少三组实际 {plan, ctx} 输入；省略时查询三个实际区域')
    parser.add_argument('--output', type=Path, help='保存实际输入和交给 C 的完整返回值 JSON')
    args = parser.parse_args()

    async def main():
        if args.input:
            cases = json.loads(args.input.read_text())
            if not isinstance(cases, list) or len(cases) < 3:
                parser.error('需要至少三组真实业务输入')
        else:
            cases = []
            for area, maximum in [('Tampines', 4000), ('Clementi', 4500), ('Punggol', 4000)]:
                identity = str(uuid4())
                cases.append(dict(plan=dict(plan_id=identity, profile_version=1, attempt_id=identity, intent='rent',
                    required_filters=dict(currency='SGD', max_price=maximum, price_period='month',
                        rental_scope='whole_unit', locations=[area.upper()], min_bedrooms=2),
                    queries=[dict(query_id='q-' + area.lower(), source='propertyguru', text=area, cursor=None)],
                    page_limit=1, candidate_limit=1, source_mode='live', reason='真实 search 全链路验收：按已确认需求取回候选'),
                    ctx=dict(user_id='live-check', run_id=identity, conversation_id=identity, attempt_id=identity,
                        trace_id=identity, call_id=identity, source_mode='live', deadline_at='')))
        records, passed = [], 0
        for case in cases:
            if not args.input:
                # 每组开始时才计算截止时间，前一组耗时不挤占下一组预算。
                case['ctx']['deadline_at'] = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
            if case['ctx']['source_mode'] != 'live':
                parser.error('验收只接受 live 输入，不使用虚拟响应')
            before = deepcopy(case)
            print('开始真实 search：' + ', '.join(q['text'] for q in case['plan']['queries']), flush=True)
            result = await search(case['plan'], ctx=case['ctx'])
            failures = []
            try:
                validate_search_result(result, case['plan'], case['ctx'])
                if case != before:
                    failures.append('search 修改了输入')
                if result['status'] == 'error':
                    failures.append('真实搜索失败')
                if any(p['source'] == 'model' for p in result['issues']):
                    failures.append('模型未完成真实任务选择')
                data = result['data']
                if data:
                    if not data['items'] and not data['coverage']['queries_completed']:
                        failures.append('没有取得候选且搜索未完成')
                    for item in data['items']:
                        if not any(':detail:' in e['evidence_id'] for e in item['evidence']):
                            failures.append(item['listing_key'] + ' 缺少实际详情证据')
                        if not any(e['field'] == 'location' for e in item['evidence']):
                            failures.append(item['listing_key'] + ' 缺少实际定位证据')
                        normalized = normalize_listing(item)
                        if merge_listing(normalized, normalized) != normalized:
                            failures.append(item['listing_key'] + ' 实际重复房源未幂等合并')
                    if any(p['code'] != 'BUDGET_EXHAUSTED' for p in result['issues']):
                        failures.append('真实链路存在非额度缺口')
                if not args.input and not (data and data['items']):
                    failures.append('默认区域输入未获得可验证的房源')
            except ContractViolation as exc:
                failures.append(f'{exc.code}: {exc.field_path}: {exc}')
            record = dict(input=before, output=result, live_chain_verified=not failures, failures=failures)
            records.append(record)
            passed += not failures
            print(json.dumps(record, ensure_ascii=False), flush=True)
            if args.output:
                args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2))
        print(f'真实 search 全链路通过 {passed}/{len(cases)}；实际调用模型、guru_search 和 OneMap。', flush=True)
        return 0 if passed == len(cases) else 1

    raise SystemExit(asyncio.run(main()))
