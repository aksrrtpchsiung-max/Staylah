"""B 内部将已确认需求转换为 QueryFeatures，不调用来源、不生成房源事实。"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys
from time import monotonic


from property_agent.contracts import ConversationProfile, ContractViolation, QueryFeatures, RequirementRequest, Result, RunContext
from property_agent.search.execution.budget import remaining_seconds
from property_agent.domain.requirements import (
    location_entity, normalize_requirements, validate_conversation_profile, validate_requirement_request,
)
from property_agent.domain.validation import validate_result_envelope
from property_agent.search.aggregation.results import error_result
from property_agent.search.providers.base import ProviderError, issue


def _query_from_requirements(requirements) -> QueryFeatures:
    entities, seen = [], set()
    for requirement in requirements['derived_data_requirements']:
        if (requirement['category'] != 'accessibility' or requirement['metric'] != 'residential_area'
                or requirement['operator'] != 'eq' or requirement['value'] is False):
            continue
        target = requirement['target']
        if target is None and type(requirement['value']) is str:
            target = requirement['value']
        if target:
            entity = location_entity(target)
            identity = entity['canonical_id'] or entity['raw_text']
            if identity not in seen:
                entities.append(entity)
                seen.add(identity)
    # 序列化的是用户期望，不是检索到的事实；保留所有条件、运算符和强弱属性。
    summary = dict(intent=requirements['intent'], user_context=requirements['user_context'],
        listing_constraints=requirements['listing_constraints'],
        derived_data_requirements=requirements['derived_data_requirements'],
        open_data_requirements=requirements['open_data_requirements'])
    semantic_query = json.dumps(summary, ensure_ascii=False, separators=(',', ':'))
    return dict(profile_version=requirements['version'], entities=entities,
        semantic_query=semantic_query, unresolved=deepcopy(requirements['clarification_questions']))


async def _prepare(value, ctx, validator):
    started = monotonic()
    try:
        validator(value, ctx)
        remaining_seconds(ctx)
        query = _query_from_requirements(normalize_requirements(value))
        result = dict(status='success', data=query, issues=[], meta=dict(trace_id=ctx['trace_id'],
            call_id=ctx['call_id'], duration_ms=max(0, int((monotonic() - started) * 1000))))
        validate_result_envelope(result, QueryFeatures, ctx)
        return result
    except ContractViolation as exc:
        problem = issue(exc.code, str(exc), field_path=exc.field_path)
    except ProviderError as exc:
        problem = exc.issue
    return error_result(problem, ctx, started)


async def prepare_query(profile: ConversationProfile, *, ctx: RunContext) -> Result[QueryFeatures]:
    """共享内部契约：真实 ConversationProfile 必须是当前已确认版本。"""
    return await _prepare(profile, ctx, validate_conversation_profile)


async def prepare_request_query(request: RequirementRequest, *, ctx: RunContext) -> Result[QueryFeatures]:
    """公开入口内部适配器：直接读取 A 的确认请求，不伪造完整画像。"""
    return await _prepare(request, ctx, validate_requirement_request)


if __name__ == '__main__':
    import asyncio
    from datetime import datetime, timedelta, timezone
    from scripts.live_requirements import INPUTS

    async def main():
        for index, request in enumerate(INPUTS):
            ctx = dict(user_id='live-check', run_id=f'query-{index}', conversation_id=request['conversation_id'],
                attempt_id=None, trace_id=f'query-{index}', call_id=f'query-{index}',
                deadline_at=(datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(), source_mode='live')
            actual = await prepare_request_query(request, ctx=ctx)
            validate_result_envelope(actual, QueryFeatures, ctx)
            assert actual['status'] == 'success', actual
            assert actual['data']['profile_version'] == request['profile_version']
            assert not actual['data']['unresolved'], actual
            assert actual['data']['entities'], '当前四组业务输入均有明确居住区域'
            # 这些是内部接口的独立业务输入，仅存在于本模块测试中；产品交接路径不做此转换。
            profile = dict(profile_id=request['profile_id'], user_id=ctx['user_id'],
                conversation_id=request['conversation_id'], version=request['profile_version'],
                confirmed_version=request['profile_version'], status='confirmed', intent=request['intent'],
                user_context=deepcopy(request['user_context']),
                listing_constraints=deepcopy(request['listing_constraints']),
                derived_data_requirements=deepcopy(request['derived_data_requirements']),
                open_data_requirements=deepcopy(request['open_data_requirements']),
                unresolved=list(request['unresolved_fields']), field_sources={},
                created_at=request['confirmed_at'], updated_at=request['confirmed_at'],
                last_user_message_at=request['confirmed_at'], confirmed_at=request['confirmed_at'])
            internal = await prepare_query(profile, ctx=ctx)
            assert internal['status'] == 'success', internal
            assert internal['data'] == actual['data'], '两种真实契约输入必须保留相同需求'
            from property_agent.domain.validation import validate_planner_input, planned_attempt_id
            from property_agent.search.planning.planner import _make_menu
            from property_agent.runtime.model_client import SearchPlanSettings
            validate_planner_input(profile, internal['data'], [], None, ctx)
            requirements = normalize_requirements(profile)
            menu, groups = _make_menu(requirements, internal['data'], [], None, ctx, SearchPlanSettings())
            assert menu and groups and planned_attempt_id(requirements, ctx)
            assert ctx['attempt_id'] is None, 'B 内部生成轮次不能修改外部 ctx'
            print(json.dumps(dict(input=dict(request=request, ctx=ctx), actual_output=actual,
                internal_profile_input=profile, internal_query_output=internal,
                actual_menu=menu, actual_groups=groups), ensure_ascii=False))
        assert len(INPUTS) >= 3
        print(f'查询解析实际输入输出验证通过 {len(INPUTS)}/{len(INPUTS)}')

    asyncio.run(main())
