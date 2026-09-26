"""2：先搜索、后补充的搜索管理 Agent；模型只能在当前阶段内选择任务。

模型使用 config.py 的 ainvoke 消息接口，不依赖网关尚未验证的 bind_tools。
运行本文件使用真实网关和浏览器，检查 2→3a 的搜索、筛选和按需详情。
"""
import asyncio
from copy import deepcopy
import json
import logging
from pathlib import Path
import sys
from time import monotonic


from property_agent.search.execution.history import fingerprint, task
from property_agent.search.capabilities.location import request_from_listing
from property_agent.search.capabilities.amenities import supports_requirement
from property_agent.search.execution.dispatcher import investigation_requirements
from property_agent.search.aggregation.results import merge_listing
from property_agent.search.aggregation.requirements import evaluate_listing_constraints
from property_agent.search.providers.base import ProviderError, issue


class SearchSupervisor:
    def __init__(self, dispatcher, *, model=None, max_retries=1, model_timeout_seconds=8,
                 max_model_calls=3):
        if type(max_retries) is not int or not 0 <= max_retries <= 3:
            raise ValueError('max_retries 必须是 0–3')
        if not 0 < model_timeout_seconds <= 60:
            raise ValueError('模型决策超时必须在 0–60 秒内')
        if type(max_model_calls) is not int or max_model_calls <= 0:
            raise ValueError('max_model_calls 必须是正整数')
        self.dispatcher, self.model = dispatcher, model
        self.max_retries = max_retries
        self.model_timeout_seconds = model_timeout_seconds
        self.max_model_calls, self.model_calls = max_model_calls, 0
        self._model_disabled = False

    def hard_rejections(self, state, listing):
        """只跳过有证据的硬失败；缺失、冲突和软偏好仍允许补查。

        不删除候选，最终逐项检查仍交给汇总/C；这里只控制是否继续花费检索额度。
        """
        request = state.get('requirement_request')
        if request is not None:
            failures = [c for c in evaluate_listing_constraints(listing,
                [c for c in request['listing_constraints'] if c['strength'] == 'hard'],
                filters=state['plan']['required_filters']) if c['status'] == 'fail']
        else:
            # 独立 search(plan) 没有 SourceReference，不伪造用户条件/证据。
            filters = state['plan']['required_filters']
            def known(field):
                value = listing
                for part in field.split('.'):
                    value = value[part]
                facts = [e for e in listing['evidence'] if e['field'] == field]
                if (value is None or value in ('unknown', 'conflict') or not facts
                        or field + ':conflict' in listing['field_issues']
                        or any(e['value'] != value for e in facts)
                        or field.startswith('price.') and listing['price']['status'] == 'conflict'):
                    return None
                return value
            limits = [('transaction_type', 'eq', 'sale' if state['plan']['intent'] == 'buy' else 'rent'),
                ('attributes.listing_scope', 'eq', filters['rental_scope']),
                ('bedrooms', 'gte', filters['min_bedrooms'])]
            if (known('price.currency') == filters['currency']
                    and known('price.period') == filters['price_period']):
                limits.append(('price.amount', 'lte', filters['max_price']))
            failures = []
            for field, operator, expected in limits:
                actual = known(field)
                if actual is not None and expected is not None and (
                        actual != expected if operator == 'eq' else
                        actual < expected if operator == 'gte' else actual > expected):
                    failures.append(dict(field=field, status='fail', reason='不满足搜索计划硬条件',
                        evidence_ids=[e['evidence_id'] for e in listing['evidence'] if e['field'] == field]))
        # 契约明确禁止推荐已下架房源；原候选继续保留给 C 核对。
        if listing['listing_status'] == 'inactive' and any(
                e['field'] == 'listing_status' and e['value'] == 'inactive' for e in listing['evidence']):
            failures.append(dict(field='listing_status', status='fail', reason='房源已下架',
                evidence_ids=[e['evidence_id'] for e in listing['evidence'] if e['field'] == 'listing_status']))
        return failures

    def available_tasks(self, state):
        """只开放一个阶段；执行前的二次核对也使用同一阶段限制。"""
        tasks = []
        budget = self.dispatcher.budget
        has_budget = budget.pages_used < state['plan']['page_limit'] and budget.candidates_used < state['plan']['candidate_limit']
        for qid, progress in state['queries'].items():
            if progress['done'] or progress['blocked']:
                continue
            cached = self.dispatcher.history.get_page(state['plan'], qid, progress['cursor'])
            if has_budget or cached is not None:
                tasks.append(task('search_page', query_id=qid, cursor=progress['cursor']))
        # 包括仍能复用的真实页面：有搜索任务时，绝不开放详情、定位等补充能力。
        if tasks:
            return tasks

        # 搜索完成后按实际缺口派工；注册 Provider 不等于用户要求调用它。
        completed = set(state['completed_tasks'])
        requirements = [r for r in investigation_requirements(state.get('requirement_request') or {})
                        if supports_requirement(r)]
        for key, listing in state['listings'].items():
            # 搜索卡片先筛一次；详情补齐字段后再次筛，避免继续定位/配套调查。
            if self.hard_rejections(state, listing):
                continue
            detail = task('read_detail', listing_key=key)
            gaps = self.detail_gaps(state, listing)
            pending = [task('travel' if r['category'] == 'commute' else 'amenities',
                            listing_key=key, requirement_id=r['requirement_id'])
                       for r in sorted(requirements, key=lambda r: {'high': 0, 'medium': 1, 'low': 2}[r['priority']])]
            pending = [t for t in pending if t['task_id'] not in completed]
            # 地图调查需要地址时才从详情补地址，不把详情设为所有能力的固定前置。
            address_needed = bool(pending) and not request_from_listing(listing)['query']
            if (gaps or address_needed) and detail['task_id'] not in completed:
                tasks.append(detail)
                continue
            if not pending:
                continue
            location = task('locate', listing_key=key)
            location['task_id'] += ':' + fingerprint(request_from_listing(listing))
            if location['task_id'] not in completed:
                tasks.append(location)
            else:
                tasks.extend(pending)
        return tasks

    def detail_gaps(self, state, listing):
        """明确列出 guru 详情有能力补充的未知需求字段；已知/冲突不反复读取。"""
        capability = self.dispatcher.listings[listing['source']]
        fields = getattr(capability.provider, 'detail_fields', frozenset())
        request = state.get('requirement_request')
        if request is not None:
            checks = evaluate_listing_constraints(listing, request['listing_constraints'],
                                                   filters=state['plan']['required_filters'])
            return sorted({c['field'] for c in checks if c['status'] == 'unknown' and c['field'] in fields
                           and c['field'] + ':conflict' not in listing['field_issues']
                           and not (c['field'].startswith('price.') and listing['price']['status'] == 'conflict')})
        # 独立 search(plan) 只检查其真实给出的条件；不虚构 RequirementRequest。
        filters = state['plan']['required_filters']
        required = [('price.amount', filters['max_price']), ('bedrooms', filters['min_bedrooms']),
                    ('attributes.listing_scope', filters['rental_scope'])]
        gaps = []
        for field, expected in required:
            value = listing
            for part in field.split('.'):
                value = value[part]
            if (expected is not None and field in fields and field + ':conflict' not in listing['field_issues']
                    and (value in (None, 'unknown') or not any(e['field'] == field for e in listing['evidence']))):
                gaps.append(field)
        return gaps

    def task_context(self, state, selected):
        """向 Agent 和计时日志提供每次补查的具体原因，避免只给不透明任务 ID。"""
        key = selected.get('listing_key')
        if key is None:
            return dict(query_id=selected['query_id'], cursor=selected['cursor'])
        listing = state['listings'][key]
        completed = set(state['completed_tasks'])
        needs_investigation = any(supports_requirement(r) and task(
            'travel' if r['category'] == 'commute' else 'amenities', listing_key=key,
            requirement_id=r['requirement_id'])['task_id'] not in completed
            for r in investigation_requirements(state.get('requirement_request') or {}))
        return dict(listing_key=key, title=listing['title'], price=listing['price']['amount'],
            bedrooms=listing['bedrooms'], property_type=listing['attributes']['property_type'],
            missing_fields=self.detail_gaps(state, listing), requirement_id=selected.get('requirement_id'),
            needs_address=selected['kind'] == 'read_detail' and needs_investigation
                          and not request_from_listing(listing)['query'])

    async def decide(self, state):
        screened = {key: failures for key, listing in state['listings'].items()
                    if (failures := self.hard_rejections(state, listing))}
        for key, failures in screened.items():
            if state.get('screened_out', {}).get(key) != failures:
                logging.getLogger('search.audit').info('跳过硬条件失败候选的后续补查',
                    extra={'audit': dict(event='screened_out', listing_key=key, failures=failures)})
        try:
            self.dispatcher.budget.work_seconds(state['ctx'])
        except ProviderError:
            return dict(selected_task=None, stop_reason='deadline', screened_out=screened, issues=state['issues'] + [
                issue('TIMEOUT', '搜索已到执行时限，预留汇总时间并保留已取得的房源', source=None)])
        tasks = self.available_tasks(state)
        phase = 'search' if tasks and tasks[0]['kind'] == 'search_page' else 'enrich'
        pending = any(not q['done'] and not q['blocked'] for q in state['queries'].values())
        problems = state['issues'][:]
        # 在进入补充阶段时就记录额度中断，之后即使超时也不会丢失搜索未完成的原因。
        if phase == 'enrich' and pending and not any(p['code'] == 'BUDGET_EXHAUSTED' for p in problems):
            problems.append(issue('BUDGET_EXHAUSTED', '本次页数或候选额度用完，保留后续游标', source=None))
        if not tasks:
            return dict(selected_task=None, stop_reason='budget' if pending else 'complete', issues=problems, screened_out=screened)
        selected = tasks[0]
        reason = '搜索阶段：先执行硬条件搜索' if phase == 'search' else '补充阶段：按依赖补充候选信息'
        # 只有一个可执行任务时无需再花一次模型往返来选择同一个任务。
        if len(tasks) > 1 and self.model is not None and not self._model_disabled and self.model_calls < self.max_model_calls:
            # 限定一次决策的菜单长度；不向模型发送网页原文或任何密钥。
            menu = tasks[:64]
            payload = dict(phase=phase, available_tasks=menu,
                           task_context={t['task_id']: self.task_context(state, t) for t in menu},
                           pages_used=state['pages_used'], candidates_used=state['candidates_used'],
                           page_limit=state['plan']['page_limit'], candidate_limit=state['plan']['candidate_limit'],
                           queries_completed=all(q['done'] for q in state['queries'].values()),
                           candidate_count=len(state['listings']),
                           hard_constraints=state['plan']['required_filters'],
                           unresolved_locations=sum(x['status'] != 'resolved' for x in state['locations'].values()))
            model_started = monotonic()
            try:
                async with asyncio.timeout(min(self.model_timeout_seconds, self.dispatcher.budget.work_seconds(state['ctx']))):
                    self.model_calls += 1
                    reply = await self.model.ainvoke([
                        {'role': 'system', 'content': '你是搜索管理 Agent，只负责当前这一次任务选择，程序随后才会调用真实工具。调度分两阶段：phase=search 时只能选择 3a 的 search_page，按既定硬条件先搜索并收集候选，不能提前补充信息；phase=enrich 时搜索阶段已结束，只能对已取得的候选选择菜单中的补充任务：read_detail（3a 详情）、locate（3b 定位）、amenities（3c 配套）或 travel（3d 出行），依赖关系由程序保证。进入补充阶段不代表搜索完整完成，以 queries_completed 为准；额度耗尽或查询受阻时仍可补充已有候选，候选不代表已满足全部硬条件。阶段切换和停止由程序决定，不得改写或放宽硬条件。只能从 available_tasks 复制一个 task_id，禁止执行或模拟任务，禁止编造房源、坐标、工具结果和后续对话。只输出一行 JSON，恰好包含 task_id 和 reason 两个非空字符串，reason 不超过30字。不用Markdown。JSON结束后输出 <END_DECISION> 并立即结束。'},
                        {'role': 'user', 'content': json.dumps(payload, ensure_ascii=False)}],
                        stream=False, stop=['<END_DECISION>'])
                content = reply.content
                if not isinstance(content, str):
                    raise ValueError
                content = content.strip().removesuffix('<END_DECISION>').strip()
                if content.startswith('```') and content.endswith('```'):
                    content = '\n'.join(content.splitlines()[1:-1]).strip()
                decision = json.loads(content)
                if (not isinstance(decision, dict) or set(decision) != {'task_id', 'reason'}
                        or not all(isinstance(v, str) and v.strip() for v in decision.values())):
                    raise ValueError
                selected = next(t for t in menu if t['task_id'] == decision['task_id'])
                reason = decision['reason'][:500]
            except (ValueError, StopIteration, AttributeError):
                problems.append(issue('INVALID_OUTPUT', '管理模型返回了无效任务，已改用固定调度', source='model'))
                self._model_disabled = True
            except Exception as exc:
                code = 'TIMEOUT' if isinstance(exc, TimeoutError) else 'MODEL_UNAVAILABLE'
                problems.append(issue(code, f'管理模型调用失败（{type(exc).__name__}），已改用固定调度', source='model', retryable=True))
                self._model_disabled = True
            finally:
                logging.getLogger('search.audit').info('管理模型决策完成', extra={'audit': dict(
                    event='supervisor_model', duration_ms=round((monotonic() - model_started) * 1000),
                    phase=phase, fallback=self._model_disabled, available_count=len(tasks))})
        return dict(selected_task=selected, decision_reason=reason, issues=problems, screened_out=screened)

    async def execute(self, state):
        # 二次核对，不能让模型输出直接成为外部调用参数。
        selected = state['selected_task']
        if selected not in self.available_tasks(state):
            return dict(selected_task=None, stop_reason='invalid_state', issues=state['issues'] + [
                issue('INVALID_STATE', '选择的任务已经不可执行', source=None)])
        started = monotonic()
        try:
            async with asyncio.timeout(self.dispatcher.budget.work_seconds(state['ctx'])):
                result = await self.dispatcher.execute(selected, state)
        except (TimeoutError, ProviderError) as exc:
            problem = exc.issue if isinstance(exc, ProviderError) else issue(
                'TIMEOUT', '任务达到执行时限，预留时间汇总已有结果', source=None)
            result = dict(status='error', data=None, issues=[problem])
        updated = deepcopy(state)
        tid, kind = selected['task_id'], selected['kind']
        attempts = updated['attempts'].get(tid, 0) + 1
        updated['attempts'][tid] = attempts
        updated['task_issues'][tid] = result['issues']
        if kind == 'search_page':
            source = next(q['source'] for q in state['plan']['queries'] if q['query_id'] == selected['query_id'])
        else:
            source = ('routing' if kind == 'travel' else 'neighborhood' if kind == 'amenities' else
                self.dispatcher.location.provider.source if kind == 'locate' else state['listings'][selected['listing_key']]['source'])
        record = dict(task_id=tid, kind=kind, source=source, status=result['status'],
            attempt=attempts, reason=state['decision_reason'], duration_ms=round((monotonic() - started) * 1000),
            listing_key=selected.get('listing_key'), context=self.task_context(state, selected))
        updated['history'].append(record)
        logging.getLogger('search.audit').info('检索任务完成', extra={'audit': dict(event='task', **record)})
        retryable = any(p['retryable'] for p in result['issues'])
        # partial 搜索页已有事实和游标，不重取；详情/定位失败可有界重试。
        retry = retryable and attempts <= self.max_retries and (kind != 'search_page' or result['data'] is None)
        if retry:
            delay = max((p['retry_after_seconds'] or 0 for p in result['issues']), default=0)
            try:
                if delay >= self.dispatcher.budget.work_seconds(state['ctx']):
                    retry = False
                elif delay:
                    await asyncio.sleep(delay)
            except ProviderError:
                retry = False
        data = result['data']
        if kind == 'search_page':
            progress = updated['queries'][selected['query_id']]
            if data is not None:
                updated['pages'].append(deepcopy(data))
                for item in data['items']:
                    key = item['listing_key']
                    updated['listings'][key] = merge_listing(updated['listings'][key], item) if key in updated['listings'] else deepcopy(item)
                progress['seen_cursors'].append(selected['cursor'])
                next_cursor = data['next_cursor']
                progress['cursor'] = next_cursor
                progress['done'] = data['pagination_known'] and next_cursor is None and not data['truncated']
                progress['blocked'] = not progress['done'] and next_cursor is None
                if next_cursor is not None and next_cursor in progress['seen_cursors']:
                    progress['blocked'] = True
                    progress['cursor'] = None  # 已知循环游标不能继续交给 A 当作可用续页。
                    updated['task_issues'][tid].append(issue('RETRIEVAL_DEGRADED', '来源重复返回已经执行过的游标，已停止该查询', source=source))
                if progress['blocked'] and not updated['task_issues'][tid]:
                    updated['task_issues'][tid].append(issue('RETRIEVAL_DEGRADED', '来源未提供可继续的可靠游标', source=source))
            elif not retry:
                progress['blocked'] = True
        elif kind == 'read_detail':
            if data is not None:
                if result['status'] == 'success':
                    data['field_issues'] = [f for f in data['field_issues'] if not f.startswith('detail:')]
                updated['listings'][selected['listing_key']] = data
        elif kind == 'locate' and data is not None:
            key = selected['listing_key']
            updated['locations'][key] = data
            listing = updated['listings'][key]
            listing['evidence'] = [e for e in listing['evidence'] if e['field'] != 'location'] + deepcopy(data['evidence'])
            listing['field_issues'] = [f for f in listing['field_issues'] if not f.startswith('location:')] + data['gaps']
        elif kind in ('amenities', 'travel'):
            updated['investigations'][tid] = deepcopy(result)
            listing = updated['listings'][selected['listing_key']]
            prefix = 'investigation:' + tid + ':'
            listing['field_issues'] = [f for f in listing['field_issues'] if not f.startswith(prefix)]
            if data is not None:
                evidence = {e['evidence_id']: e for e in listing['evidence']}
                evidence.update({e['evidence_id']: deepcopy(e) for e in data['evidence']})
                listing['evidence'] = list(evidence.values())
                listing['field_issues'].extend(prefix + gap for gap in data['gaps'])
            else:
                listing['field_issues'].append(prefix + 'unverified')
        if not retry:
            updated['completed_tasks'].append(tid)
        updated['pages_used'] = self.dispatcher.budget.pages_used
        updated['candidates_used'] = self.dispatcher.budget.candidates_used
        updated['selected_task'] = None
        return updated


if __name__ == '__main__':
    import argparse
    from datetime import datetime, timedelta, timezone
    from uuid import uuid4
    from property_agent.search.api import create_live_search_service
    from property_agent.runtime.model_client import ModelConfigurationError
    from property_agent.search.aggregation.results import aggregate
    from time import monotonic

    class AuditedModel:
        """检查每次真实模型调用的任务菜单；不替换模型回复或工具结果。"""
        def __init__(self, model):
            self.model = model
            self.decisions = []

        async def ainvoke(self, messages, **kwargs):
            payload = json.loads(messages[-1]['content'])
            phase = payload['phase']
            kinds = {t['kind'] for t in payload['available_tasks']}
            assert (phase == 'search' and kinds == {'search_page'}) or (
                phase == 'enrich' and kinds and kinds <= {'read_detail', 'locate', 'amenities', 'travel'}), '任务菜单跨越阶段'
            assert not (phase == 'search' and any(d['phase'] == 'enrich' for d in self.decisions)), '补充后又返回搜索'
            self.decisions.append(dict(phase=phase, kinds=sorted(kinds),
                                       candidate_count=payload['candidate_count']))
            print(f'真实决策：{phase}，候选 {payload["candidate_count"]}，任务 {sorted(kinds)}', file=sys.stderr, flush=True)
            return await self.model.ainvoke(messages, **kwargs)

    parser = argparse.ArgumentParser(description='真实 2→3a 联调；缺少真实依赖时失败，不使用虚拟响应')
    parser.add_argument('--live', action='store_true', help='兼容旧命令；现在默认就是 live')
    parser.add_argument('--input', type=Path, help='至少三个 {plan, ctx} 的真实搜索输入，省略时使用三组实际区域查询')
    parser.add_argument('--output', type=Path, help='可选：保存本次真实调用的输入、执行历史及输出 JSON')
    args = parser.parse_args()

    async def main():
        if args.input:
            loaded = json.loads(args.input.read_text())
            cases = loaded if isinstance(loaded, list) else [loaded]
            if len(cases) < 3 or any(c['plan']['source_mode'] != 'live' or c['ctx']['source_mode'] != 'live' for c in cases):
                parser.error('需要至少三组 live 输入，不使用虚拟响应')
        else:
            cases = []
            for name, maximum in [('Tampines', 4000), ('Clementi', 4500), ('Punggol', 4000)]:
                identity = str(uuid4())
                plan = dict(plan_id=identity, profile_version=1, attempt_id=identity, intent='rent',
                    required_filters=dict(currency='SGD', max_price=maximum, price_period='month',
                        rental_scope='whole_unit', locations=[name.upper()], min_bedrooms=2),
                    # 相同查询用不同 ID 进入计划，第二项复用真实页面。
                    # 即使已有候选且额度用完，也必须先完成此搜索任务再读详情。
                    queries=[dict(query_id='q-' + name.lower() + suffix, source='propertyguru', text=name, cursor=None)
                             for suffix in ('', '-reuse')],
                    page_limit=1, candidate_limit=1, source_mode='live', reason='真实联调：先搜索及复用页面，再补充候选信息')
                ctx = dict(user_id='live-check', run_id=identity, conversation_id=identity, attempt_id=identity,
                    trace_id=identity, call_id=identity, source_mode='live',
                    deadline_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat())
                cases.append(dict(plan=plan, ctx=ctx))
        records, passed = [], 0
        for case in cases:
            if not args.input:
                case['ctx']['deadline_at'] = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
            started = monotonic()
            try:
                service = create_live_search_service()
                print(f'开始真实阶段测试：{case["plan"]["queries"][0]["text"]}', file=sys.stderr, flush=True)
                audit = AuditedModel(service.model)
                service.model = audit
                state = await service.run(case['plan'], ctx=case['ctx'])
                result = aggregate(state, started)
                # 所有返回均来自真实服务；检查模型菜单和实际调用顺序两层约束。
                order = [h['kind'] for h in state['history']]
                first_enrich = next((i for i, kind in enumerate(order) if kind != 'search_page'), len(order))
                phase_order_verified = bool(order) and order[0] == 'search_page' and 'search_page' not in order[first_enrich:]
                queries_complete = all(q['done'] for q in state['queries'].values())
                incomplete_reported = queries_complete or (result['status'] != 'success'
                    and result['data'] is not None and not result['data']['coverage']['queries_completed'])
                # 默认三组必须实际经过“已有房源，但仍要继续处理搜索任务”的边界。
                search_with_candidates = sum(h['kind'] == 'search_page' and h['status'] in ('success', 'partial')
                                             for h in state['history']) >= 2
                kinds = {h['kind'] for h in state['history'] if h['status'] == 'success'}
                # 这些原始计划没有配套/通勤需求，不应仅因注册 Provider 就查询地图。
                accepted = ({'search_page', 'read_detail'} <= kinds
                    and not ({'locate', 'amenities', 'travel'} & set(order))
                    and not any(p['source'] == 'model' for p in result['issues'])
                    and phase_order_verified and incomplete_reported
                    and (bool(args.input) or search_with_candidates))
                record = dict(input=case, history=state['history'], decisions=audit.decisions,
                    phase_order_verified=phase_order_verified, incomplete_reported=incomplete_reported,
                    search_with_candidates=search_with_candidates, output=result, live_chain_verified=accepted)
            except (ProviderError, ModelConfigurationError) as exc:
                problem = exc.issue if isinstance(exc, ProviderError) else issue('MODEL_UNAVAILABLE', str(exc), source='model')
                record = dict(input=case, output=dict(status='error', data=None, issues=[problem]), live_chain_verified=False)
            passed += record['live_chain_verified']
            records.append(record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
        if args.output:
            args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2))
        print(f'真实 2→3a 链路通过 {passed}/{len(cases)}；搜索和详情实际成功且没有无需求地图调用才通过。')
        return 0 if passed == len(cases) else 1

    raise SystemExit(asyncio.run(main()))
