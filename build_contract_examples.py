"""生成完整接口示例并检查结构；不运行尚未实现的业务函数。
执行（Python 3.11+）：python3.11 build_contract_examples.py
"""
import copy
import importlib.util
import inspect
import json
from pathlib import Path
import types
import typing

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('contracts_v0', ROOT / 'contracts_v0.py')
contracts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contracts)
D = copy.deepcopy
NOW = '2026-09-09T10:00:00+08:00'
CTX = dict(user_id='user-001', run_id='run-001', conversation_id='thread-001',
           attempt_id='attempt-001',
           trace_id='trace-001', call_id='call-001',
           deadline_at='2026-09-09T10:02:00+08:00', source_mode='mock')
POLICY = dict(min_matches=3, display_limit=10, max_search_attempts=3, max_repairs=1)
HC = dict(currency='SGD', max_price=3500, price_period='month',
          rental_scope='whole_unit', locations=['TAMPINES'], min_bedrooms=2)
SRC = dict(message_id='msg-001', text='整套租房，SGD 月租最多3500，淡滨尼，至少两个卧室。',
           start=0, end=31)
LISTING_CONSTRAINTS = [
    dict(constraint_id='constraint-price', field_path='price.amount', operator='lte',
         value=3500, strength='hard', priority='high', source=SRC),
    dict(constraint_id='constraint-scope', field_path='attributes.listing_scope', operator='eq',
         value='whole_unit', strength='hard', priority='high', source=SRC),
    dict(constraint_id='constraint-bedrooms', field_path='bedrooms', operator='gte',
         value=2, strength='hard', priority='high', source=SRC),
]
DERIVED_REQUIREMENTS = [dict(requirement_id='requirement-location', category='accessibility',
    target='淡滨尼', metric='residential_area', operator='eq', value=True, unit=None,
    strength='hard', priority='high', source=SRC)]
P1 = dict(profile_id='profile-001', user_id='user-001', conversation_id='thread-001',
          version=1, confirmed_version=1, status='confirmed', intent='rent', user_context=[],
          listing_constraints=LISTING_CONSTRAINTS,
          derived_data_requirements=DERIVED_REQUIREMENTS, open_data_requirements=[],
          unresolved=[], field_sources={
              'intent': 'msg-001', 'listing_constraints': 'msg-001',
              'derived_data_requirements': 'msg-001'}, created_at=NOW, updated_at=NOW,
          last_user_message_at=NOW, confirmed_at=NOW)
P0 = D(P1)
P0.update(version=0, confirmed_version=None, status='draft', intent=None,
          user_context=[], listing_constraints=[], derived_data_requirements=[],
          open_data_requirements=[],
          field_sources={}, unresolved=['intent', 'listing_constraints.price.amount',
          'listing_constraints.attributes.listing_scope', 'derived_data_requirements.location'],
          confirmed_at=None)
QUERY = dict(profile_version=1, entities=[dict(type='location', raw_text='淡滨尼',
    canonical_id='TAMPINES', aliases=['Tampines', '淡滨尼'])],
    semantic_query='Tampines 整套出租 月租不超过 SGD 3500 至少两个卧室', unresolved=[])


def listing(key, amount, bedrooms=2, price_status='known'):
    url = f'https://example.com/demo/{key}'
    vals = {'transaction_type': 'rent', 'price.currency': 'SGD', 'price.period': 'month',
            'attributes.listing_scope': 'whole_unit', 'location_id': 'TAMPINES'}
    if bedrooms is not None:
        vals['bedrooms'] = bedrooms
    if amount is not None:
        vals['price.amount'] = amount
    evidence = [dict(evidence_id=f'{key}:{field}', field=field, value=value,
                     source_url=url, observed_at=NOW, excerpt=f'模拟页面：{field}={value}')
                for field, value in vals.items()]
    price_ids = [f'{key}:price.amount'] if amount is not None else []
    if price_status == 'conflict':
        for label, price in [('display', 3200), ('body', 3800)]:
            eid = f'{key}:price.{label}'
            evidence.append(dict(evidence_id=eid, field='price.amount', value=price,
                source_url=url, observed_at=NOW, excerpt=f'模拟同口径月租：{price} SGD'))
            price_ids.append(eid)
    return dict(listing_key=key, source='demo_a', source_listing_id=key,
        source_url=url, source_mode='mock', title=f'模拟房源 {key}',
        transaction_type='rent', price=dict(amount=amount, currency='SGD', period='month',
            status=price_status, evidence_ids=price_ids),
        attributes=dict(property_type='hdb', unit_layout='3br', listing_scope='whole_unit',
            area_sqft=1000, bathrooms=2, room_type='unknown', ensuite_bathroom=None,
            owner_stays=False, cooking_policy='full', utilities_included=False,
            wifi_included=False, visitors_allowed=True, pets_allowed=None,
            furnishing='partially', tenure_type='leasehold', lease_years=99),
        bedrooms=bedrooms, location_id='TAMPINES', listing_status='active',
        listed_date='2026-09-08', fetched_at=NOW, source_updated_at=None,
        last_verified_at=NOW, raw_description=f'模拟房源 {key}，用于接口联调。',
        raw_details=['整套出租', '至少两个卧室', '月租按 SGD 计价'],
        evidence=evidence, field_issues=['price.amount:conflict'] if price_status == 'conflict' else [])


L1, L2, L3 = listing('L1', 3400), listing('L2', 3500), listing('L3', 3501)
L4, L5, L6 = listing('L4', None, price_status='conflict'), listing('L5', 3300, None), listing('L6', 3300)
L6['source_url'] = None
for item in L6['evidence']:
    item['source_url'] = None
GOOD = [L1, L2, L6]
ALL = [L1, L2, L3, L4, L5, L6]


def screened(item, verdict='pass', field=None):
    fields = ['transaction_type', 'price.currency', 'price.period', 'attributes.listing_scope',
              'price.amount', 'location_id', 'bedrooms']
    checks = []
    for f in fields:
        status = verdict if f == field else 'pass'
        ids = [e['evidence_id'] for e in item['evidence'] if e['field'] == f]
        checks.append(dict(field=f, status=status,
            reason={'pass': '本轮证据满足对应硬条件', 'fail': '月租超过 3500 SGD',
                    'unknown': '字段冲突或缺少可确认信息'}[status], evidence_ids=ids))
    return dict(listing_key=item['listing_key'], checks=checks)


SCREEN = dict(profile_version=1, eligible=[screened(x) for x in GOOD],
              rejected=[screened(L3, 'fail', 'price.amount')],
              needs_verification=[screened(L4, 'unknown', 'price.amount'), screened(L5, 'unknown', 'bedrooms')])
SNAPSHOT = dict(snapshot_id='snapshot-001', profile_version=1, items=ALL)
PLAN = dict(plan_id='plan-001', profile_version=1, attempt_id='attempt-001', intent='rent',
    required_filters=D(HC), queries=[dict(query_id='q-001', source='demo_a',
        text='Tampines whole unit rent SGD 3500 2 bedrooms', cursor=None)],
    page_limit=3, candidate_limit=60, source_mode='mock', reason='首次按已确认硬条件搜索')
COVERAGE = dict(queried_sources=['demo_a'], failed_sources=[], queries_completed=True,
    has_more=False, next_pages=[], truncated=False,
    applied_filters=['transaction_type', 'price.currency', 'price.period', 'attributes.listing_scope',
                     'price.amount', 'location_id', 'bedrooms'], unsupported_filters=[])


def retrieval(items):
    return dict(profile_version=1, candidates=[dict(listing_key=x['listing_key'],
        exact_matches=['TAMPINES'], vector_score=round(0.9-i*0.02, 2),
        keyword_score=4.0, retrieval_rank=i+1, retrieval_score=round(0.03-i*0.001, 3))
        for i, x in enumerate(items)], input_count=len(items), returned_count=len(items),
        truncated=False, method_version='example-rrf-v0')


RETRIEVAL = retrieval(GOOD)


def evaluation(items):
    return dict(profile_version=1, snapshot_id='snapshot-001', recommendation=dict(
        ordered_items=[dict(listing_key=x['listing_key'], rank=i+1, reasons=[dict(
            kind='fact', text=f'来源显示月租 SGD {x["price"]["amount"]}，不超过预算。',
            evidence_ids=[f'{x["listing_key"]}:price.amount'])], tradeoffs=[],
            unknowns=['实际当前可租状态尚未向经纪人核实']) for i, x in enumerate(items)],
        summary=f'本次展示 {len(items)} 套候选，顺序仅为接口示例。',
        limitations=['模拟数据；仅覆盖本次查询；来源状态不等于独立核实']),
        assessment=dict(constraint_findings=[], search_directive=None, relaxation_proposals=[],
                        next_action='publish', next_reason_code='enough_matches'))


EVALUATION = evaluation(GOOD)
PASS = dict(passed=True, issues=[])
DIRECTIVE = dict(reason_code='insufficient_candidates', strategy_changes=[dict(
    kind='next_page', query_id='q-001', cursor='page-2')], base_profile_version=1,
    evidence_listing_keys=['L1', 'L2'])
PROPOSAL = dict(proposal_id='proposal-001', field='listing_constraints.price.amount',
    old_value=3500, proposed_value=3600,
    reason='本次被排除的 L3 月租 3501；提高到 3600 可能扩大本轮匹配，仍需重新查询。',
    evidence_listing_keys=['L3'], requires_user_confirmation=True)
QUESTION = dict(question_id='run-001:state-7:q-1', text='是否将月租上限调整为 SGD 3600？',
    reason_code='insufficient_candidates', proposals=[PROPOSAL],
    allowed_actions=['accept_proposal', 'decline', 'answer', 'cancel'],
    base_profile_version=1, state_version=7)
STATE = dict(run_id='run-001', state_version=7, profile_version=1, current_profile_version=1,
    cancelled=False, user_declined=False, deadline_exhausted=False, search_status='success',
    search_attempts_used=1, repairs_used=0, eligible_count=3, review=PASS, failure_code=None,
    search_directive=None, pending_question=None,
    evaluation_next_action=None, evaluation_next_reason_code=None)
CASES = []


def ok(data, status='success', issues=None):
    return dict(status=status, data=data, issues=issues or [],
                meta=dict(trace_id='trace-001', call_id='call-001', duration_ms=10))


def issue(code, field=None, source=None, retryable=False, retry_after=None):
    return dict(code=code, message=f'示例：{code}', field_path=field, source=source,
                retryable=retryable, retry_after_seconds=retry_after)


def error(code, field=None, source=None, retryable=False):
    return ok(None, 'error', [issue(code, field, source, retryable)])


def add(id, fn, category, inputs, output=None, raises=None, dependency=None, checks=None):
    CASES.append(D(dict(id=id, function=fn, category=category, input=inputs,
        dependency_fixture=dependency or {}, expected=({'raises': raises} if raises else {'return': output}),
        assertions=checks or [])))


patch = [
    dict(operation='set', field='intent', value='rent', source_message_id='msg-001'),
    dict(operation='set', field='listing_constraints', value=LISTING_CONSTRAINTS,
         source_message_id='msg-001'),
    dict(operation='set', field='derived_data_requirements', value=DERIVED_REQUIREMENTS,
         source_message_id='msg-001'),
]
DRAFT = D(P1)
DRAFT.update(confirmed_version=None, status='pending_confirmation', confirmed_at=None)
CONFIRMATION = dict(confirmation_id='confirm-profile-001-v1', profile_id='profile-001',
                    profile_version=1,
                    summary='整套租房；月租最多 SGD 3500；淡滨尼；至少两个卧室。',
                    status='pending')
OB = dict(base_profile_version=0, profile_patch=patch, draft_profile=DRAFT,
          missing_required_fields=[], questions=[], confirmation=CONFIRMATION,
          next_action='ask_confirmation')
ob_inputs = dict(request=dict(message_id='msg-001', text='整套租房，SGD 月租最多3500，淡滨尼，至少两个卧室。'),
                 profile=P0, messages=[], ctx=CTX)
add('onboard.normal', 'onboard', 'normal', ob_inputs, ok(OB), checks=['不修改输入 profile；由 ProfileService 提交 patch 后 version 从 0 变为 1。'])
inp, out = D(ob_inputs), D(OB)
inp['request']['text'] = '整套租房，按 SGD 月租找淡滨尼，至少两个卧室，预算还没确定。'
out['profile_patch'][1]['value'] = [x for x in LISTING_CONSTRAINTS if x['field_path'] != 'price.amount']
out['draft_profile']['listing_constraints'] = D(out['profile_patch'][1]['value'])
out['draft_profile']['status'] = 'draft'
out['draft_profile']['unresolved'] = ['listing_constraints.price.amount']
out.update(missing_required_fields=['listing_constraints.price.amount'],
    questions=[dict(field='listing_constraints.price.amount', text='你的月租预算范围是多少 SGD？')],
    confirmation=None, next_action='ask_clarification')
add('onboard.boundary', 'onboard', 'boundary', inp, ok(out), checks=['缺失预算是正常澄清结果，不是 error。'])
inp = D(ob_inputs); inp['request']['text'] = '   '
add('onboard.error', 'onboard', 'error', inp, error('INVALID_INPUT', 'request.text'))

confirm_reply = dict(confirmation_id='confirm-profile-001-v1', message_id='msg-002',
                     action='confirm', text=None)
confirm_out = dict(profile=P1, ready_for_handoff=True, confirmation=None, questions=[])
add('confirm_requirements.normal', 'confirm_requirements', 'normal',
    dict(reply=confirm_reply, profile=DRAFT, ctx=CTX), ok(confirm_out),
    checks=['只有与当前 draft version 匹配的确认才能产生 ready_for_handoff=true。'])
correction_reply = dict(confirmation_id='confirm-profile-001-v1', message_id='msg-002',
                        action='correct', text='预算改成最多 SGD 3000。')
corrected = D(DRAFT)
corrected.update(version=2, status='pending_confirmation', updated_at=NOW,
                 last_user_message_at=NOW)
corrected['listing_constraints'][0]['value'] = 3000
confirmation2 = dict(confirmation_id='confirm-profile-001-v2', profile_id='profile-001',
                     profile_version=2,
                     summary='整套租房；月租最多 SGD 3000；淡滨尼；至少两个卧室。',
                     status='pending')
correction_out = dict(profile=corrected, ready_for_handoff=False,
                      confirmation=confirmation2, questions=[])
add('confirm_requirements.boundary', 'confirm_requirements', 'boundary',
    dict(reply=correction_reply, profile=DRAFT, ctx=CTX), ok(correction_out),
    checks=['用户修订产生新 draft version，必须再次确认，不能直接交给 B。'])
stale_reply = D(confirm_reply); stale_reply['confirmation_id'] = 'confirm-profile-001-v0'
add('confirm_requirements.error', 'confirm_requirements', 'error',
    dict(reply=stale_reply, profile=DRAFT, ctx=CTX),
    error('STATE_CONFLICT', 'reply.confirmation_id'))

add('prepare_query.normal', 'prepare_query', 'normal', dict(profile=P1, ctx=CTX), ok(QUERY))
amb = D(P1)
amb['derived_data_requirements'][0]['target'] = '裕廊'
amb['unresolved'] = ['derived_data_requirements.location:裕廊的具体范围']
qamb = D(QUERY); qamb.update(entities=[dict(type='location', raw_text='裕廊', canonical_id=None, aliases=[])],
    semantic_query='裕廊 整套出租 月租不超过 SGD 3500 至少两个卧室',
    unresolved=[dict(field='derived_data_requirements.location', text='裕廊指裕廊东、裕廊西，还是两者都可以？')])
add('prepare_query.boundary', 'prepare_query', 'boundary', dict(profile=amb, ctx=CTX), ok(qamb),
    checks=['本例词表返回两个候选；不能虚构唯一地点 ID，不进入计划生成。'])
badp = D(P1); badp['version'] = -1
add('prepare_query.error', 'prepare_query', 'error', dict(profile=badp, ctx=CTX), error('INVALID_INPUT', 'profile.version'))

plan_inputs = dict(profile=P1, query=QUERY, previous_attempts=[], directive=None, ctx=CTX)
add('build_search_plan.normal', 'build_search_plan', 'normal', plan_inputs, ok(PLAN))
ctx2 = D(CTX); ctx2['attempt_id'] = 'attempt-002'
inp = D(plan_inputs); inp.update(ctx=ctx2, directive=DIRECTIVE, previous_attempts=[dict(
    attempt_id='attempt-001', query_fingerprints=['demo_a|q-001|cursor:null|profile:1'], status='success', eligible_count=2)])
plan2 = D(PLAN); plan2.update(plan_id='plan-002', attempt_id='attempt-002', reason='在相同硬条件下查询下一页')
plan2['queries'][0]['cursor'] = 'page-2'
add('build_search_plan.boundary', 'build_search_plan', 'boundary', inp, ok(plan2),
    checks=['预算与区域保持不变；创建新的 attempt，不回滚或清零旧记录。'])
inp = D(plan_inputs); inp['query']['profile_version'] = 0
add('build_search_plan.error', 'build_search_plan', 'error', inp, error('STATE_CONFLICT', 'query.profile_version'))

search_out = dict(plan_id='plan-001', profile_version=1, items=ALL, coverage=COVERAGE)
REQ_REQUEST = dict(request_id='requirement-request-001', schema_version='0.3-draft',
    conversation_id='thread-001', profile_id='profile-001', profile_version=1,
    intent='rent', user_context=[], listing_constraints=LISTING_CONSTRAINTS,
    derived_data_requirements=DERIVED_REQUIREMENTS, open_data_requirements=[],
    unresolved_fields=[], confirmed_at=NOW)
REQ_COVERAGE = dict(fulfilled_requirement_ids=['requirement-location'],
                    unsupported_requirement_ids=[], unverified_requirement_ids=[],
                    skipped_best_effort_requirement_ids=[])
fulfillment = dict(request_id='requirement-request-001', profile_version=1,
                   status='completed', search_result=search_out,
                   coverage=REQ_COVERAGE, clarification_questions=[])
add('fulfill_requirements.normal', 'fulfill_requirements', 'normal',
    dict(request=REQ_REQUEST, ctx=CTX), ok(fulfillment),
    checks=['A 只提交已确认的数据需求；B 在内部决定 provider 与 SearchPlan。'])
needs_detail = D(fulfillment)
needs_detail.update(status='needs_clarification', search_result=None,
                    clarification_questions=[dict(field='derived_data_requirements[0].value',
                        text='可接受的最长通勤时间是多少分钟？')])
needs_detail['coverage'].update(fulfilled_requirement_ids=[],
                                unverified_requirement_ids=['requirement-location'])
add('fulfill_requirements.boundary', 'fulfill_requirements', 'boundary',
    dict(request=REQ_REQUEST, ctx=CTX), ok(needs_detail),
    checks=['B 只返回结构化澄清请求，由 A 负责与用户对话。'])
best_effort_request = D(REQ_REQUEST)
best_effort_request['open_data_requirements'] = [dict(
    requirement_id='requirement-open-tennis', description='附近有网球场',
    handling='best_effort', strength='soft', priority='medium', source=SRC)]
best_effort_fulfillment = D(fulfillment)
best_effort_fulfillment['coverage']['skipped_best_effort_requirement_ids'] = [
    'requirement-open-tennis']
add('fulfill_requirements.best_effort_skipped', 'fulfill_requirements', 'boundary',
    dict(request=best_effort_request, ctx=CTX), ok(best_effort_fulfillment),
    checks=['跳过无法处理的开放需求不改变 completed 状态，不阻止返回已匹配房源。'])
unconfirmed_request = D(REQ_REQUEST); unconfirmed_request['profile_version'] = 0
add('fulfill_requirements.error', 'fulfill_requirements', 'error',
    dict(request=unconfirmed_request, ctx=CTX),
    error('INVALID_STATE', 'request.profile_version'))

add('search.normal', 'search', 'normal', dict(plan=PLAN, ctx=CTX), ok(search_out),
    dependency=dict(provider_responses={'demo_a': dict(status='success', items=ALL+[D(L1)], has_more=False)}),
    checks=['相同来源、相同 listing_key 的重复 L1 只返回一次。'])
empty = D(search_out); empty['items'] = []
add('search.boundary', 'search', 'boundary', dict(plan=PLAN, ctx=CTX), ok(empty),
    dependency=dict(provider_responses={'demo_a': dict(status='success', items=[], has_more=False)}))
add('search.error', 'search', 'error', dict(plan=PLAN, ctx=CTX), error('TIMEOUT', source='demo_a', retryable=True),
    dependency=dict(provider_responses={'demo_a': dict(status='timeout', attempts=2)}),
    checks=['本调用内部重试已用完；retryable 描述错误性质，不授权调用方再叠加重试。'])
multi = D(PLAN); multi['queries'].append(dict(query_id='q-002', source='demo_b', text=PLAN['queries'][0]['text'], cursor=None))
partial = D(search_out); partial['coverage'].update(queried_sources=['demo_a', 'demo_b'],
    failed_sources=['demo_b'], queries_completed=False)
add('search.partial', 'search', 'boundary', dict(plan=multi, ctx=CTX),
    ok(partial, 'partial', [issue('RATE_LIMITED', source='demo_b', retryable=True, retry_after=60)]),
    dependency=dict(provider_responses={'demo_a': dict(status='success', items=ALL, has_more=False),
        'demo_b': dict(status='rate_limited', retry_after_seconds=60)}, remaining_seconds=20))

add('screen.normal', 'screen', 'normal', dict(listings=[L1,L3], profile=P1),
    dict(profile_version=1, eligible=[screened(L1)], rejected=[screened(L3,'fail','price.amount')], needs_verification=[]))
add('screen.boundary', 'screen', 'boundary', dict(listings=[L2,L4,L5], profile=P1),
    dict(profile_version=1, eligible=[screened(L2)], rejected=[], needs_verification=[
        screened(L4,'unknown','price.amount'), screened(L5,'unknown','bedrooms')]),
    checks=['3500 等于上限，允许通过；冲突金额与未知卧室数不能通过。'])
badl = listing('BAD', -1)
add('screen.error', 'screen', 'error', dict(listings=[badl], profile=P1),
    raises=dict(type='ContractViolation', code='INVALID_INPUT', field_path='listings[0].price.amount'))
add('screen.empty', 'screen', 'boundary', dict(listings=[], profile=P1),
    dict(profile_version=1, eligible=[], rejected=[], needs_verification=[]))

ret_inputs = dict(query=QUERY, eligible_listings=GOOD, top_k=30, ctx=CTX)
add('retrieve.normal', 'retrieve', 'normal', ret_inputs, ok(RETRIEVAL),
    checks=['结果引用仅来自输入候选；分数是示例，不规定真实检索算法的固定值。'])
inp = D(ret_inputs); inp['eligible_listings'] = []
add('retrieve.boundary', 'retrieve', 'boundary', inp, ok(retrieval([])))
inp = D(ret_inputs); inp['top_k'] = 0
add('retrieve.error', 'retrieve', 'error', inp, error('INVALID_INPUT','top_k'))
inp = D(ret_inputs); inp['top_k'] = 2
ret2 = retrieval(GOOD[:2]); ret2.update(input_count=3,truncated=True)
add('retrieve.truncated', 'retrieve', 'boundary', inp, ok(ret2), checks=['调用方仍保留 eligible_count=3，不能误报只找到 2 套。'])

ev_inputs = dict(profile=P1, retrieval=RETRIEVAL, screen_result=SCREEN, listing_snapshot=SNAPSHOT,
                 coverage=COVERAGE, repair_context=None, policy=POLICY, ctx=CTX)
add('evaluate.normal', 'evaluate', 'normal', ev_inputs, ok(EVALUATION),
    checks=['推荐必须是通过筛选且存在于 retrieval 的候选子集；文字和软偏好顺序允许变化。'])
inp = D(ev_inputs); inp.update(retrieval=retrieval([]), screen_result=dict(profile_version=1, eligible=[],
    rejected=[screened(L3,'fail','price.amount')], needs_verification=[]),
    listing_snapshot=dict(snapshot_id='snapshot-001',profile_version=1,items=[L3]))
ev0 = evaluation([]); ev0['assessment'].update(constraint_findings=['本轮 L3 因月租 3501 超出预算被排除。'], relaxation_proposals=[PROPOSAL])
add('evaluate.boundary', 'evaluate', 'boundary', inp, ok(ev0), checks=['不编造推荐、不自行更改预算；仅输出有依据的调整提案。'])
inp = D(ev_inputs); inp['retrieval']['candidates'][0]['listing_key'] = 'L999'
add('evaluate.error', 'evaluate', 'error', inp, error('INVALID_INPUT','retrieval.candidates[0].listing_key'))

review_inputs = dict(profile=P1,evaluation=EVALUATION,listing_snapshot=SNAPSHOT,policy=POLICY,ctx=CTX)
add('review.normal', 'review', 'normal', review_inputs, ok(PASS))
inp = D(review_inputs); inp['evaluation'] = evaluation([])
add('review.boundary', 'review', 'boundary', inp, ok(PASS),
    checks=['空推荐本身不构成格式/证据错误；数量不足由路由器处理。'])
add('review.error', 'review', 'error', review_inputs, error('MODEL_UNAVAILABLE',source='review_model',retryable=True),
    dependency=dict(review_model=dict(status='unavailable')),
    checks=['审查没有完成，不能伪造 passed=true。'])
bad_ev = D(EVALUATION); bad_ev['recommendation']['ordered_items'][0]['reasons'].append(
    dict(kind='fact',text='步行到地铁站只需 5 分钟。',evidence_ids=[]))
blocked = dict(passed=False,issues=[dict(code='UNSUPPORTED_CLAIM',listing_key='L1',
    field_path='recommendation.ordered_items[0].reasons[1]',message='没有支持步行 5 分钟的来源证据。',
    severity='blocking',suggested_fix='删除该事实，或补充真实路线证据后重新审查。')])
inp = D(review_inputs); inp['evaluation'] = bad_ev
add('review.detected_defect', 'review', 'boundary', inp, ok(blocked),
    checks=['成功发现坏草稿仍是 status=success；与审查服务失败区分。'])


def route(action, reason, directive=None, question=None):
    return dict(action=action,reason_code=reason,search_directive=directive,pending_question=question)


add('decide_next.normal', 'decide_next', 'normal', dict(state=STATE,policy=POLICY), route('publish','enough_matches'))
st = D(STATE); st.update(eligible_count=2,search_attempts_used=3,pending_question=QUESTION)
add('decide_next.boundary', 'decide_next', 'boundary', dict(state=st,policy=POLICY),
    route('ask_user','insufficient_candidates',question=QUESTION), checks=['已用完三次搜索额度，只询问用户，不创建第四次搜索。'])
st = D(STATE); st['eligible_count'] = -1
add('decide_next.error', 'decide_next', 'error', dict(state=st,policy=POLICY),
    raises=dict(type='ContractViolation',code='INVALID_STATE',field_path='state.eligible_count'))
st = D(STATE); st.update(eligible_count=2,search_directive=DIRECTIVE)
add('decide_next.research', 'decide_next', 'boundary', dict(state=st,policy=POLICY),
    route('research','insufficient_candidates',directive=DIRECTIVE))
st = D(STATE); st.update(review=blocked)
add('decide_next.repair', 'decide_next', 'boundary', dict(state=st,policy=POLICY),route('repair','review_blocked'))
st = D(STATE); st.update(review=blocked,repairs_used=1)
add('decide_next.repair_exhausted', 'decide_next', 'boundary', dict(state=st,policy=POLICY),route('stop','repair_exhausted'))
st = D(STATE); st.update(search_status='error',eligible_count=0,review=None,failure_code='TIMEOUT')
add('decide_next.source_failure', 'decide_next', 'boundary', dict(state=st,policy=POLICY),route('stop','source_failure'))
st = D(STATE); st.update(current_profile_version=2)
add('decide_next.stale', 'decide_next', 'boundary', dict(state=st,policy=POLICY),route('stop','profile_superseded'))
st = D(STATE); st.update(user_declined=True,eligible_count=2)
add('decide_next.declined', 'decide_next', 'boundary', dict(state=st,policy=POLICY),route('finish','user_declined'))


def check_type(tp, value, path='value', bindings=None):
    """检查本契约所用类型；不替代金额范围、证据语义等业务校验。"""
    bindings = bindings or {}
    if isinstance(tp, str):
        tp = eval(tp, vars(contracts))
    if isinstance(tp, typing.TypeVar):
        return check_type(bindings[tp], value, path, bindings)
    if isinstance(tp, typing.ForwardRef):
        return check_type(eval(tp.__forward_arg__, vars(contracts)), value, path, bindings)
    origin, args = typing.get_origin(tp), typing.get_args(tp)
    if origin in (typing.Union, types.UnionType):
        for a in args:
            try:
                check_type(a, value, path, bindings)
                return
            except (AssertionError, TypeError):
                pass
        raise AssertionError(f'{path}: 不符合 {tp}')
    if origin is typing.Literal:
        assert any(type(value) is type(a) and value == a for a in args), (path, value, args)
        return
    if origin is list:
        assert isinstance(value, list), path
        for i,v in enumerate(value): check_type(args[0],v,f'{path}[{i}]',bindings)
        return
    if origin is dict:
        assert isinstance(value,dict),path
        for k,v in value.items():
            check_type(args[0],k,path+'.key',bindings); check_type(args[1],v,path+'.'+str(k),bindings)
        return
    cls = origin or tp
    if typing.is_typeddict(cls):
        assert isinstance(value,dict),path
        local = dict(bindings)
        if origin: local.update(zip(cls.__parameters__,args))
        hints = typing.get_type_hints(cls,vars(contracts),vars(contracts))
        assert set(value) == set(hints), (path,'字段集合不符',set(value)^set(hints))
        for k,t in hints.items(): check_type(t,value[k],path+'.'+k,local)
        return
    if tp is float:
        assert type(value) in (float,int),path
    else:
        assert type(value) is tp,(path,tp,type(value))


def validate():
    assert len({c['id'] for c in CASES}) == len(CASES)
    for case in CASES:
        fn = getattr(contracts,case['function'])
        hints = typing.get_type_hints(fn)
        inspect.signature(fn).bind(**case['input'])
        for k,v in case['input'].items(): check_type(hints[k],v,case['id']+'.input.'+k)
        if 'return' in case['expected']:
            val = case['expected']['return']
            check_type(hints['return'],val,case['id']+'.return')
            if 'status' in val:
                assert (val['data'] is None) == (val['status']=='error'),case['id']
                assert val['status']=='success' or val['issues'],case['id']
            if case['function']=='review' and val['status']=='success':
                assert val['data']['passed'] == (not any(i['severity']=='blocking' for i in val['data']['issues']))
    for fn in {c['function'] for c in CASES}:
        assert {'normal','boundary','error'} <= {c['category'] for c in CASES if c['function']==fn},fn


if __name__ == '__main__':
    validate()
    dest = ROOT / 'examples' / 'function-contract-cases.json'
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(dict(schema_version='0.3-draft',
        note='全部为虚构示例，函数未实现；每例独立，输入已完全展开。模型文字/分数不是固定验收答案。',
        cases=CASES),ensure_ascii=False,indent=2)+'\n')
    fixture_dest = ROOT / 'examples' / 'shared-fixtures.json'
    fixture_dest.write_text(json.dumps(dict(profile_empty=P0,profile_ready=P1,query=QUERY,
        listings=ALL,screen_result=SCREEN,retrieval_result=RETRIEVAL,snapshot=SNAPSHOT,
        evaluation=EVALUATION,policy=POLICY),ensure_ascii=False,indent=2)+'\n')
    function_count = len({case['function'] for case in CASES})
    print(f'已验证 {len(CASES)} 个示例的签名绑定、类型结构、返回封装和 {function_count} 个函数的三类覆盖。')
    print('业务函数未执行；金额范围、模型质量、真实来源与恢复行为尚未测试。')
    print(dest)
