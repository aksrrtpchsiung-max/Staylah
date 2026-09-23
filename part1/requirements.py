"""B 内部的需求校验与过滤投影；不重建 A 拥有的 ConversationProfile。

RequirementRequest 是 A 在确认指定版本后提交的交接凭证。B 校验会话、版本和
确认时间，但没有用户确认记录库，不能据此声称独立验证过 A 的确认操作。
原始约束始终保留；SearchPlan 的六项过滤仅是可表达的必要条件，并非全部需求。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import math
import re
from pathlib import Path
import sys
from typing import TypedDict, get_type_hints

if __name__ == '__main__' and __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import contracts_v0 as contracts
from contracts_v0 import ContractViolation, HardConstraints, Clarification
from part1.validation import fail, timestamp, validate_context, validate_type


# 本地检索词表；只解释名字，不证明房源位于某个行政区。
_LOCATION_NAMES = {
    'TAMPINES': ('Tampines', '淡滨尼'),
    'CLEMENTI': ('Clementi', '金文泰'),
    'PUNGGOL': ('Punggol', '榜鹅'),
    'BEDOK': ('Bedok', '勿洛'),
    'BISHAN': ('Bishan', '碧山'),
    'JURONG_EAST': ('Jurong East', '裕廊东'),
    'JURONG_WEST': ('Jurong West', '裕廊西'),
    'SENGKANG': ('Sengkang', '盛港'),
    'WOODLANDS': ('Woodlands', '兀兰'),
    'TOA_PAYOH': ('Toa Payoh', '大巴窑'),
    'ANG_MO_KIO': ('Ang Mo Kio', '宏茂桥'),
    'HOUGANG': ('Hougang', '后港'),
    'SERANGOON': ('Serangoon', '实龙岗'),
    'PASIR_RIS': ('Pasir Ris', '巴西立'),
    'BUKIT_BATOK': ('Bukit Batok', '武吉巴督'),
    'QUEENSTOWN': ('Queenstown', '女皇镇'),
    'SINGAPORE': ('Singapore', '新加坡'),
}
_LOCATION_LOOKUP = {name.casefold().replace('_', ' '): key
                    for key, names in _LOCATION_NAMES.items() for name in (key, *names)}


class PlanningRequirements(TypedDict):
    """仅存在于 B 的显式投影，不包含伪造的画像状态、时间或确认历史。"""
    profile_id: str
    version: int
    intent: str
    user_context: list[contracts.ProfileFact]
    listing_constraints: list[contracts.ListingConstraint]
    derived_data_requirements: list[contracts.DerivedDataRequirement]
    open_data_requirements: list[contracts.OpenDataRequirement]
    unresolved: list[str]
    required_filters: HardConstraints
    clarification_questions: list[Clarification]


def location_entity(name: str) -> contracts.Entity:
    name = ' '.join(name.split())
    canonical = _LOCATION_LOOKUP.get(name.casefold().replace('_', ' '))
    return dict(type='location', raw_text=name, canonical_id=canonical,
                aliases=list(_LOCATION_NAMES.get(canonical, ())))


def _validate_source(source, path):
    if not source['message_id'].strip() or not source['text'].strip():
        fail(path, '原文及来源消息 ID 不能为空')
    # start/end 是原消息中的偏移；source.text 可以只是摘录，不能拿摘录长度校验终点。
    if source['start'] < 0 or source['end'] <= source['start']:
        fail(path, '原文起止位置必须是非负、递增的范围')


def _listing_field_schema(field):
    parent = contracts.Listing
    for component in field.split('.'):
        parent = get_type_hints(parent)[component]
    return parent


def _validate_constraint(constraint, path):
    field, operator, value = (constraint[k] for k in ('field_path', 'operator', 'value'))
    schema = _listing_field_schema(field)
    numeric = field in {'price.amount', 'bedrooms', 'attributes.area_sqft',
                        'attributes.bathrooms', 'attributes.lease_years'}
    comparable = numeric or field == 'listed_date'
    if operator in ('lt', 'lte', 'gt', 'gte', 'between') and not comparable:
        fail(path + '.operator', '此字段不支持大小或区间比较')
    if operator == 'contains' and field not in {'attributes.unit_layout', 'price.currency'}:
        fail(path + '.operator', '此字段不支持文本包含比较')
    if operator in ('between', 'in'):
        if type(value) is not list or not value or (operator == 'between' and len(value) != 2):
            fail(path + '.value', 'in 需要非空数组，between 需要两个有序端点')
        values = value
    else:
        values = [value]
    for item in values:
        if numeric:
            # Listing 是整数；用户阈值仍允许有限小数，例如面积 > 999.5。
            if type(item) not in (int, float) or not math.isfinite(item):
                fail(path + '.value', '数值比较需要有限数字')
        else:
            validate_type(schema, item, path + '.value')
        if item is None or (type(item) is str and not item.strip()):
            fail(path + '.value', '期望值不能为空；未知需求请由 A 澄清')
        if numeric and item < 0:
            fail(path + '.value', '数量和金额不能为负数')
        if field == 'listed_date':
            try:
                date.fromisoformat(item)
            except ValueError:
                fail(path + '.value', '日期条件必须使用 ISO 日期')
    if operator == 'between' and value[0] > value[1]:
        fail(path + '.value', '区间下界不能大于上界')


def _validate_requirements(value):
    ids = set()
    for field, id_key in (('listing_constraints', 'constraint_id'),
                          ('derived_data_requirements', 'requirement_id'),
                          ('open_data_requirements', 'requirement_id')):
        for index, requirement in enumerate(value[field]):
            path = f'{field}[{index}]'
            key = requirement[id_key]
            if not key.strip() or key in ids:
                fail(path + '.' + id_key, '需求 ID 不能为空，且本请求内必须唯一')
            ids.add(key)
            _validate_source(requirement['source'], path + '.source')
            if field == 'listing_constraints':
                _validate_constraint(requirement, path)
            elif field == 'derived_data_requirements':
                if not requirement['metric'].strip():
                    fail(path + '.metric', '数据指标不能为空')
                for name in ('target', 'unit'):
                    if requirement[name] is not None and not requirement[name].strip():
                        fail(path + '.' + name, '不能是空字符串')
                operator, target = requirement['operator'], requirement['value']
                if operator == 'between' and (
                    type(target) is not list or len(target) != 2
                    or any(type(x) not in (int, float) for x in target)
                    or target[0] > target[1]
                ):
                    fail(path + '.value', '派生数据区间必须包含两个递增数值')
                if operator in ('lte', 'gte') and type(target) not in (int, float):
                    fail(path + '.value', '派生数据大小比较需要数值')
            elif not requirement['description'].strip():
                fail(path + '.description', '开放需求描述不能为空')
    for index, fact in enumerate(value['user_context']):
        _validate_source(fact['source'], f'user_context[{index}].source')


def validate_requirement_request(request: contracts.RequirementRequest, ctx: contracts.RunContext) -> None:
    validate_context(ctx)
    validate_type(contracts.RequirementRequest, request, 'request')
    for key in ('request_id', 'conversation_id', 'profile_id'):
        if not request[key].strip():
            fail('request.' + key, '不能为空')
    if request['conversation_id'] != ctx['conversation_id']:
        raise ContractViolation('STATE_CONFLICT', 'request.conversation_id', '请求与执行会话不一致')
    if request['profile_version'] < 0:
        fail('request.profile_version', '版本不能为负数')
    if request['profile_version'] == 0:
        raise ContractViolation('INVALID_STATE', 'request.profile_version', '初始草稿版本不能交给 B 执行')
    timestamp(request['confirmed_at'], 'request.confirmed_at')
    _validate_requirements(request)
    if any(not field.strip() for field in request['unresolved_fields']):
        fail('request.unresolved_fields', '待澄清字段不能为空字符串')


def validate_conversation_profile(profile: contracts.ConversationProfile, ctx: contracts.RunContext) -> None:
    validate_context(ctx)
    validate_type(contracts.ConversationProfile, profile, 'profile')
    if not profile['profile_id'].strip():
        fail('profile.profile_id', '不能为空')
    if profile['version'] < 0:
        fail('profile.version', '版本不能为负数')
    if profile['conversation_id'] != ctx['conversation_id'] or profile['user_id'] != ctx['user_id']:
        raise ContractViolation('STATE_CONFLICT', 'profile', '画像与执行上下文的用户或会话不一致')
    if (profile['confirmed_version'] != profile['version'] or profile['version'] == 0
            or profile['status'] not in ('confirmed', 'idle') or profile['confirmed_at'] is None):
        raise ContractViolation('INVALID_STATE', 'profile.confirmed_version', '只能使用当前已确认版本的画像')
    for field in ('created_at', 'updated_at', 'last_user_message_at', 'confirmed_at'):
        timestamp(profile[field], 'profile.' + field)
    if profile['intent'] is None:
        fail('profile.intent', '已确认画像必须有租房或买房意图')
    _validate_requirements(profile)


def _clarify(questions, field, text):
    if not any(question['field'] == field for question in questions):
        questions.append(dict(field=field, text=text))


def _single_value(constraints, field, questions):
    matches = [item for item in constraints if item['field_path'] == field]
    candidates = None
    for item in matches:
        if item['operator'] == 'eq':
            current = {item['value']}
        elif item['operator'] == 'in':
            current = set(item['value'])
        else:
            continue
        candidates = current if candidates is None else candidates & current
    if candidates == set():
        _clarify(questions, 'listing_constraints.' + field, f'The requirements for {field} conflict. Please confirm.')
    return next(iter(candidates)) if candidates is not None and len(candidates) == 1 else None


def _bounds(constraints, field, questions):
    lower, upper = 0, None
    for item in constraints:
        if item['field_path'] != field:
            continue
        operator, value = item['operator'], item['value']
        lo, hi = None, None
        if operator == 'eq':
            lo, hi = math.ceil(value), math.floor(value)
        elif operator == 'lte':
            hi = math.floor(value)
        elif operator == 'lt':
            hi = math.ceil(value) - 1
        elif operator == 'gte':
            lo = math.ceil(value)
        elif operator == 'gt':
            lo = math.floor(value) + 1
        elif operator == 'between':
            lo, hi = math.ceil(value[0]), math.floor(value[1])
        elif operator == 'in':
            lo, hi = math.ceil(min(value)), math.floor(max(value))
        if lo is not None:
            lower = max(lower, lo)
        if hi is not None:
            upper = hi if upper is None else min(upper, hi)
    if upper is not None and lower > upper:
        _clarify(questions, 'listing_constraints.' + field, f'The ranges for {field} do not overlap. Please confirm.')
    return lower, upper


def source_currency_candidates(text: str) -> set[str]:
    """只返回原文显式出现的币种，空集合与多个候选必须由调用者区分。"""
    names = set()
    for currency, pattern in (
        ('SGD', r'\bSGD\b|(?<![A-Za-z])S\$|新币|新元|新加坡元'),
        ('USD', r'\bUSD\b|(?<![A-Za-z])US\$|美元'),
        ('CNY', r'\bCNY\b|\bRMB\b|人民币'),
        ('MYR', r'\bMYR\b|马币|令吉'),
    ):
        if re.search(pattern, text, re.I):
            names.add(currency)
    return names


def _source_currency(text):
    values = source_currency_candidates(text)
    return next(iter(values)) if len(values) == 1 else None


def source_period_candidates(text: str) -> set[str]:
    """识别用户原文的计价周期，不默认将未知租金解释为月租。"""
    values = {period for period, pattern in (
        ('month', r'月租|每月|一个月|per\s+month|monthly|\bpsf\s*/\s*month\b|\bpcm\b|/\s*month'),
        ('week', r'周租|每周|per\s+week|weekly|/\s*week'),
        ('total', r'总价|总额|total\s+price|purchase\s+price'),
    ) if re.search(pattern, text, re.I)}
    return values


def _source_period(text):
    values = source_period_candidates(text)
    return next(iter(values)) if len(values) == 1 else None


def normalize_requirements(value: contracts.ConversationProfile | contracts.RequirementRequest) -> PlanningRequirements:
    """投影可交给现有来源的必要过滤，其余完整条件由外层服务继续执行/报告。

    不对软条件、开放需求或通勤目标施加检索硬过滤；日期/布尔值/不等式等未进入
    六字段过滤的约束仍原样留在 listing_constraints 中。
    """
    questions = []
    hard = [item for item in value['listing_constraints'] if item['strength'] == 'hard']
    intent = value['intent']
    transaction = _single_value(hard, 'transaction_type', questions)
    expected = 'sale' if intent == 'buy' else 'rent'
    if transaction is not None and transaction != expected:
        _clarify(questions, 'intent', 'The transaction types conflict. Are you renting or buying?')
    currency = _single_value(hard, 'price.currency', questions)
    period = _single_value(hard, 'price.period', questions)
    price_conditions = [item for item in hard if item['field_path'] == 'price.amount']
    amount_text = '\n'.join(dict.fromkeys(item['source']['text'] for item in price_conditions))
    if currency is None:
        currency = _source_currency(amount_text)
    if period is None:
        period = _source_period(amount_text)
    if currency is not None:
        currency = currency.strip().upper()
    if price_conditions and currency is None:
        _clarify(questions, 'listing_constraints.price.currency', 'Which currency is your budget in?')
    if price_conditions and period is None:
        if intent == 'buy':
            # 购买金额对应一次总价；租房不能擅自假设月租或周租。
            period = 'total'
        else:
            _clarify(questions, 'listing_constraints.price.period', 'Is your rental budget per month or per week?')
    _, maximum = _bounds(hard, 'price.amount', questions)
    minimum, _ = _bounds(hard, 'bedrooms', questions)
    scope = _single_value(hard, 'attributes.listing_scope', questions)
    if scope == 'bedspace':
        # SearchPlan 老过滤结构只有整套/单间；保留原约束交由后续处理，不偷换为 room。
        scope = None
    locations = []
    for index, requirement in enumerate(value['derived_data_requirements']):
        if (requirement['strength'] != 'hard' or requirement['category'] != 'accessibility'
                or requirement['metric'] != 'residential_area' or requirement['operator'] != 'eq'):
            continue
        target = requirement['target']
        if target is None and type(requirement['value']) is str:
            target = requirement['value']
        if requirement['value'] is False:
            continue
        if target is None:
            _clarify(questions, f'derived_data_requirements[{index}].target', 'Which area would you like to live in?')
            continue
        if target.strip().casefold() in ('裕廊', 'jurong'):
            _clarify(questions, f'derived_data_requirements[{index}].target', 'Do you mean Jurong East, Jurong West, or either?')
            continue
        entity = location_entity(target)
        key = entity['canonical_id'] or entity['raw_text']
        if key not in locations:
            locations.append(key)
    unresolved = value.get('unresolved_fields', value.get('unresolved', []))
    # 未解决的开放/派生偏好不会阻断核心检索。未知字段不自行升级为硬条件。
    for field in unresolved:
        root = field.split(':', 1)[0]
        if root == 'intent' or root.startswith(('listing_constraints.price.', 'listing_constraints.transaction_type')):
            _clarify(questions, root, f'Please clarify this required detail: {field}.')
        elif root.startswith('derived_data_requirements.location') and any(
                r['strength'] == 'hard' and r['metric'] == 'residential_area'
                for r in value['derived_data_requirements']):
            _clarify(questions, root, f'Please confirm your preferred residential area: {field}.')
    return dict(profile_id=value['profile_id'], version=value.get('profile_version', value.get('version')),
        intent=intent, user_context=deepcopy(value['user_context']),
        listing_constraints=deepcopy(value['listing_constraints']),
        derived_data_requirements=deepcopy(value['derived_data_requirements']),
        open_data_requirements=deepcopy(value['open_data_requirements']), unresolved=list(unresolved),
        # 无预算时 SGD 仅是内部来源金额口径；缺少预算单位已在上方返回澄清。
        required_filters=dict(currency=currency or 'SGD', max_price=max(0, maximum) if maximum is not None else None,
            price_period=period, rental_scope=scope, locations=locations,
            min_bedrooms=minimum if any(item['field_path'] == 'bedrooms' for item in hard) else None),
        clarification_questions=questions)


if __name__ == '__main__':
    # 使用与真实端到端检索完全相同的三组 A→B 业务输入，只验证实际解析返回。
    # 不导入接口文档中的虚构房源，不构造 Provider 或模型的预设输出。
    from datetime import datetime, timedelta, timezone
    import json
    from test_all import INPUTS

    for index, request in enumerate(INPUTS):
        ctx = dict(user_id='live-check', run_id=f'parse-{index}', conversation_id=request['conversation_id'],
            attempt_id=None, trace_id=f'parse-{index}', call_id=f'parse-{index}',
            deadline_at=(datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(), source_mode='live')
        original = deepcopy(request)
        validate_requirement_request(request, ctx)
        actual = normalize_requirements(request)
        assert request == original, '解析不能修改 A 的请求'
        assert not actual['clarification_questions'], actual['clarification_questions']
        assert actual['listing_constraints'] == request['listing_constraints']
        assert actual['derived_data_requirements'] == request['derived_data_requirements']
        assert actual['open_data_requirements'] == request['open_data_requirements']
        print(json.dumps(dict(input=request, actual_output=actual), ensure_ascii=False))
    assert len(INPUTS) >= 3
    print(f'需求解析实际输入输出验证通过 {len(INPUTS)}/{len(INPUTS)}')
