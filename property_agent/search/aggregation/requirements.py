"""将真实检索结果包装为 A→B 的需求履约响应。

SearchResult 保留候选供 C 的 screen 使用；这里逐条核对用户条件并标明缺口，
不会把未知字段当作满足、删除未知候选或从用户期望生成房源 Evidence。
applied_filters 仍表示来源实际执行的过滤，不能用本地检查冒充来源过滤。
"""
from copy import deepcopy
from datetime import date
from pathlib import Path
import sys
from time import monotonic


from property_agent.contracts import (
    ConstraintCheck, ContractViolation, Listing, ListingConstraint,
    RequirementFulfillment, RequirementRequest, Result, RunContext, SearchResult,
)
from property_agent.search.execution.history import fingerprint
from property_agent.domain.requirements import source_currency_candidates, source_period_candidates
from property_agent.domain.validation import validate_context, validate_listing, validate_result_envelope, validate_type
from property_agent.search.providers.base import issue


def _value(listing, field):
    value = listing
    for key in field.split('.'):
        value = value[key]
    return value


def _equal(left, right):
    # JSON 的 false 不等于 0；整数与浮点数仍按数值比较。
    numeric = lambda value: type(value) in (int, float)
    return (type(left) is type(right) or numeric(left) and numeric(right)) and left == right


def _compare(actual, operator, expected, field):
    if field == 'price.currency':
        actual = actual.upper()
        expected = ([v.upper() if isinstance(v, str) else v for v in expected]
                    if isinstance(expected, list) else expected.upper() if isinstance(expected, str) else expected)
    if operator in ('eq', 'neq'):
        equal = _equal(actual, expected)
        return equal if operator == 'eq' else not equal
    if operator == 'in':
        if type(expected) is not list:
            return None
        return any(_equal(actual, member) for member in expected)
    if operator == 'contains':
        return expected in actual if isinstance(actual, str) and isinstance(expected, str) else None
    values = [actual, *(expected if operator == 'between' and isinstance(expected, list) else [expected])]
    if operator == 'between' and len(values) != 3:
        return None
    if field == 'listed_date':
        try:
            values = [date.fromisoformat(value) for value in values]
        except (TypeError, ValueError):
            return None
    elif not all(type(value) in (int, float) for value in values):
        return None
    if operator == 'between':
        return values[1] <= values[0] <= values[2]
    left, right = values
    return {'lt': lambda: left < right, 'lte': lambda: left <= right,
            'gt': lambda: left > right, 'gte': lambda: left >= right}.get(operator, lambda: None)()


def _amount_units(constraint, constraints, filters):
    """预算自己的单位优先；软预算不能借用计划的默认 SGD/月租口径。"""
    units = []
    for field, plan_key, parse in (
        ('price.currency', 'currency', source_currency_candidates),
        ('price.period', 'price_period', source_period_candidates),
    ):
        stated = parse(constraint['source']['text'])
        explicit = {item['value'].upper() if field == 'price.currency' else item['value']
            for item in constraints if item['field_path'] == field and item['operator'] == 'eq'
            and item['strength'] == constraint['strength']}
        # 多种或互相冲突的单位没有安全的数值比较含义，不能任取一个。
        if len(stated | explicit) > 1:
            return None, None
        chosen = next(iter(stated or explicit), None)
        if chosen is None and constraint['strength'] == 'hard':
            chosen = (filters or {}).get(plan_key)
        units.append(chosen)
    return tuple(units)


def evaluate_listing_constraints(listing: Listing, constraints: list[ListingConstraint], *, filters=None) -> list[ConstraintCheck]:
    """按请求顺序返回 pass/fail/unknown；仅引用输入房源中已有的字段证据。"""
    validate_listing(listing)
    validate_type(list[ListingConstraint], constraints, 'constraints')
    checks = []
    for constraint in constraints:
        field, actual = constraint['field_path'], _value(listing, constraint['field_path'])
        facts = [fact for fact in listing['evidence'] if fact['field'] == field]
        evidence_ids = [fact['evidence_id'] for fact in facts if _equal(fact['value'], actual)]
        conflict = (field + ':conflict' in listing['field_issues']
                    or any(not _equal(fact['value'], actual) for fact in facts))
        if field.startswith('price.') and listing['price']['status'] == 'conflict':
            conflict = True
        reason = None
        if actual is None or actual in ('unknown', 'conflict') or conflict:
            reason = '字段缺失、未知或证据冲突，不能判定满足'
        elif not evidence_ids:
            reason = '结构化字段缺少对应证据，待核实'
        elif field == 'price.amount':
            currency, period = _amount_units(constraint, constraints, filters)
            if not currency or period is None:
                reason = '预算的币种或计价周期未明确或互相冲突，不能直接比较金额'
            elif listing['price']['currency'].upper() != currency.upper() or listing['price']['period'] != period:
                reason = '房源与预算的币种或计价周期不一致或缺失，不能直接比较金额'
        comparison = None if reason else _compare(actual, constraint['operator'], constraint['value'], field)
        status = 'unknown' if comparison is None else 'pass' if comparison else 'fail'
        checks.append(dict(field=field, status=status,
            reason=f"{constraint['constraint_id']}: " + (reason or ('条件满足' if comparison else
                '条件不满足' if comparison is False else '操作符与字段值无法比较')),
            evidence_ids=evidence_ids))
    return checks


def requirement_coverage(request: RequirementRequest, search=None):
    """每个返回候选都有调查证据才完成；查询完成不等于房源满足阈值。"""
    from property_agent.search.capabilities.amenities import supports_requirement
    coverage = dict(fulfilled_requirement_ids=[], unsupported_requirement_ids=[], unverified_requirement_ids=[],
        skipped_best_effort_requirement_ids=[item['requirement_id'] for item in request['open_data_requirements']])
    for requirement in request['derived_data_requirements']:
        rid = requirement['requirement_id']
        if not supports_requirement(requirement):
            key = 'unsupported_requirement_ids'
        else:
            items = search['items'] if search else []
            fulfilled = bool(items) and all(any(e['field'] == 'derived_requirement.' + rid
                and isinstance(e['value'], dict) and e['value'].get('investigation_status') == 'fulfilled'
                and e['source_url'] for e in item['evidence']) for item in items)
            # 完整核心查询没有候选时没有房源可调查，不制造阻塞性的缺口。
            empty_complete = search is not None and not items and search['coverage']['queries_completed'] and not search['coverage']['truncated']
            key = 'fulfilled_requirement_ids' if fulfilled or empty_complete else 'unverified_requirement_ids'
        coverage[key].append(rid)
    return coverage


def _validate_search_data(data, request, ctx):
    if data['profile_version'] != request['profile_version']:
        raise ContractViolation('INVALID_OUTPUT', 'result.data.search_result.profile_version', '检索与需求版本不一致')
    keys = set()
    for listing in data['items']:
        validate_listing(listing)
        if listing['listing_key'] in keys or listing['source_mode'] != ctx['source_mode']:
            raise ContractViolation('INVALID_OUTPUT', 'result.data.search_result.items', '房源 ID 重复或数据来源模式不一致')
        keys.add(listing['listing_key'])
    coverage = data['coverage']
    if (not set(coverage['failed_sources']) <= set(coverage['queried_sources'])
            or set(coverage['applied_filters']) & set(coverage['unsupported_filters'])
            or coverage['has_more'] != bool(coverage['next_pages'])
            or coverage['queries_completed'] and coverage['has_more']):
        raise ContractViolation('INVALID_OUTPUT', 'result.data.search_result.coverage', '检索覆盖字段相互矛盾')


def validate_fulfillment_result(result, request: RequirementRequest, ctx: RunContext) -> None:
    """验证公开输出及每条派生/开放需求的唯一去向；error 不能携带伪成功数据。"""
    try:
        validate_result_envelope(result, RequirementFulfillment, ctx)
        data = result['data']
        if data is None:
            return
        if any(data[key] != request[key] for key in ('request_id', 'profile_version')):
            raise ContractViolation('INVALID_OUTPUT', 'result.data', '输出与需求 ID/版本不一致')
        derived = {item['requirement_id'] for item in request['derived_data_requirements']}
        opened = {item['requirement_id'] for item in request['open_data_requirements']}
        seen = set()
        for key, ids in data['coverage'].items():
            allowed = opened if key == 'skipped_best_effort_requirement_ids' else (
                derived | opened if key == 'fulfilled_requirement_ids' else derived)
            if len(set(ids)) != len(ids) or not set(ids) <= allowed or seen & set(ids):
                raise ContractViolation('INVALID_OUTPUT', 'result.data.coverage.' + key, '需求 ID 不属于请求、重复或覆盖分类冲突')
            seen.update(ids)
        if seen != derived | opened:
            raise ContractViolation('INVALID_OUTPUT', 'result.data.coverage', '派生或开放需求遗漏覆盖状态')
        search = data['search_result']
        if data['status'] == 'needs_clarification':
            if not data['clarification_questions']:
                raise ContractViolation('INVALID_OUTPUT', 'result.data.clarification_questions', '需要澄清必须给出问题')
        elif search is None or data['clarification_questions']:
            raise ContractViolation('INVALID_OUTPUT', 'result.data', '完成/部分完成必须保留搜索结果，且不能夹带阻塞澄清')
        if search is not None:
            _validate_search_data(search, request, ctx)
        if data['status'] == 'partial' and result['status'] != 'partial':
            raise ContractViolation('INVALID_OUTPUT', 'result.status', '部分履约必须说明未完成问题')
        if data['status'] == 'completed' and (data['coverage']['unverified_requirement_ids']
                or search is None or not search['coverage']['queries_completed']
                or search['coverage']['truncated'] or search['coverage']['failed_sources']):
            raise ContractViolation('INVALID_OUTPUT', 'result.data.status', '核心或受支持派生检索未完成时不能声明 completed')
    except ContractViolation as exc:
        raise ContractViolation('INVALID_OUTPUT', exc.field_path, str(exc)) from exc


def build_fulfillment(request: RequirementRequest, search_result: Result[SearchResult], *,
                      ctx: RunContext, started: float, filters=None) -> Result[RequirementFulfillment]:
    """包装内部 search 返回值；不修改请求、上游结果或其中的原始事实。"""
    validate_context(ctx)
    validate_type(RequirementRequest, request, 'request')
    validate_result_envelope(search_result, SearchResult, ctx)
    result = deepcopy(search_result)
    result['meta']['duration_ms'] = max(result['meta']['duration_ms'], int((monotonic() - started) * 1000), 0)
    if result['data'] is None:
        validate_fulfillment_result(result, request, ctx)
        return result
    search = result['data']
    _validate_search_data(search, request, ctx)
    requested_fields = {constraint['field_path'] for constraint in request['listing_constraints']}
    search['coverage']['unsupported_filters'] = sorted(set(search['coverage']['unsupported_filters'])
        | (requested_fields - set(search['coverage']['applied_filters'])))
    for index, listing in enumerate(search['items']):
        for constraint, check in zip(request['listing_constraints'], evaluate_listing_constraints(
                listing, request['listing_constraints'], filters=filters)):
            if check['status'] == 'pass':
                continue
            marker = f"constraint:{constraint['constraint_id']}:{check['status']}"
            if marker not in listing['field_issues']:
                listing['field_issues'].append(marker)
            if check['status'] == 'unknown' and constraint['strength'] == 'hard':
                result['issues'].append(issue('RETRIEVAL_DEGRADED', check['reason'],
                    field_path=f'result.data.search_result.items[{index}].{check["field"]}', source=listing['source']))
        expected_intent = 'sale' if request['intent'] == 'buy' else 'rent'
        if listing['transaction_type'] != expected_intent and 'transaction_type:intent_mismatch' not in listing['field_issues']:
            listing['field_issues'].append('transaction_type:intent_mismatch')
    coverage = search['coverage']
    derived_coverage = requirement_coverage(request, search)
    if derived_coverage['unverified_requirement_ids']:
        result['issues'].append(issue('RETRIEVAL_DEGRADED', '受支持派生需求尚未完整核实：' +
            ', '.join(derived_coverage['unverified_requirement_ids']), source=None))
    if (not coverage['queries_completed'] or coverage['truncated'] or coverage['failed_sources']) and not result['issues']:
        result['issues'].append(issue('RETRIEVAL_DEGRADED', '核心检索未完整执行，保留当前取得的候选', source=None))
    result['issues'] = list({fingerprint(problem): problem for problem in result['issues']}.values())
    result['status'] = 'partial' if result['issues'] else 'success'
    result['data'] = dict(request_id=request['request_id'], profile_version=request['profile_version'],
        status='partial' if result['status'] == 'partial' else 'completed', search_result=search,
        coverage=derived_coverage, clarification_questions=[])
    validate_fulfillment_result(result, request, ctx)
    return result


if __name__ == '__main__':
    import argparse
    import json

    parser = argparse.ArgumentParser(description='以至少三组真实上游产物检查需求履约汇总，不生成虚拟房源')
    parser.add_argument('--input', type=Path, required=True,
        help='JSON 数组，每项为 {request, search_result, ctx, filters?}；search_result 必须为实际 live 检索结果')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    records = json.loads(args.input.read_text())
    if not isinstance(records, list) or len(records) < 3:
        parser.error('需要至少三组真实业务输入')
    outputs = []
    for record in records:
        request, upstream, ctx = record['request'], record['search_result'], record['ctx']
        if ctx['source_mode'] != 'live':
            parser.error('只接受真实 live 检索输入')
        before = deepcopy(record)
        output = build_fulfillment(request, upstream, ctx=ctx, started=monotonic(), filters=record.get('filters'))
        validate_fulfillment_result(output, request, ctx)
        assert record == before, '汇总不得改变上游数据'
        if upstream['data'] is None:
            assert output['status'] == 'error' and output['data'] is None
        else:
            actual = output['data']['search_result']['items']
            assert [item['listing_key'] for item in actual] == [item['listing_key'] for item in upstream['data']['items']]
            assert [item['evidence'] for item in actual] == [item['evidence'] for item in upstream['data']['items']]
            assert output['data']['coverage']['skipped_best_effort_requirement_ids'] == [
                item['requirement_id'] for item in request['open_data_requirements']]
        outputs.append(dict(input=record, output=output, passed=True))
        print(json.dumps(dict(request_id=request['request_id'], status=output['status'], passed=True), ensure_ascii=False))
    if args.output:
        args.output.write_text(json.dumps(outputs, ensure_ascii=False, indent=2))
    print(f'真实需求汇总校验通过 {len(outputs)}/{len(records)}')
