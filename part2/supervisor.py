"""2：有限任务集合上的搜索管理 Agent；模型建议由代码校验后执行。

模型使用 config.py 的 ainvoke 消息接口，不依赖网关尚未验证的 bind_tools。
运行本文件使用真实网关、浏览器和 OneMap 执行 2→3a→3b 联调。
"""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys

if __name__ == '__main__' and __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from execution.budget import remaining_seconds
from execution.history import fingerprint, task
from part3.capabilities.location import request_from_listing
from part45.aggregation import merge_listing
from providers.base import ProviderError, issue


class SearchSupervisor:
    def __init__(self, dispatcher, *, model=None, max_retries=1, model_timeout_seconds=15):
        if type(max_retries) is not int or not 0 <= max_retries <= 3:
            raise ValueError('max_retries 必须是 0–3')
        if not 0 < model_timeout_seconds <= 60:
            raise ValueError('模型决策超时必须在 0–60 秒内')
        self.dispatcher, self.model = dispatcher, model
        self.max_retries = max_retries
        self.model_timeout_seconds = model_timeout_seconds
        self._model_disabled = False

    def available_tasks(self, state):
        completed = set(state['completed_tasks'])
        tasks = []
        # 先完善已取得的候选；分页耗尽不会阻止详情和定位。
        for key, listing in state['listings'].items():
            detail = task('read_detail', listing_key=key)
            if detail['task_id'] not in completed:
                tasks.append(detail)
            else:
                location = task('locate', listing_key=key)
                location['task_id'] += ':' + fingerprint(request_from_listing(listing))
                if location['task_id'] not in completed:
                    tasks.append(location)
        budget = self.dispatcher.budget
        has_budget = budget.pages_used < state['plan']['page_limit'] and budget.candidates_used < state['plan']['candidate_limit']
        for qid, progress in state['queries'].items():
            if progress['done'] or progress['blocked']:
                continue
            cached = self.dispatcher.history.get_page(state['plan'], qid, progress['cursor'])
            if has_budget or cached is not None:
                tasks.append(task('search_page', query_id=qid, cursor=progress['cursor']))
        return tasks

    async def decide(self, state):
        try:
            remaining_seconds(state['ctx'])
        except ProviderError:
            return dict(selected_task=None, stop_reason='deadline', issues=state['issues'] + [
                issue('TIMEOUT', '搜索已到截止时间，保留已取得的房源', source=None)])
        tasks = self.available_tasks(state)
        if not tasks:
            pending = any(not q['done'] and not q['blocked'] for q in state['queries'].values())
            issues = state['issues'][:]
            if pending:
                issues.append(issue('BUDGET_EXHAUSTED', '本次页数或候选额度用完，保留后续游标', source=None))
            return dict(selected_task=None, stop_reason='budget' if pending else 'complete', issues=issues)
        selected, reason, problems = tasks[0], '按依赖顺序执行已有候选，再执行搜索页', state['issues'][:]
        if self.model is not None and not self._model_disabled:
            # 限定一次决策的菜单长度；不向模型发送网页原文或任何密钥。
            menu = tasks[:64]
            payload = dict(available_tasks=menu, pages_used=state['pages_used'], candidates_used=state['candidates_used'],
                           hard_constraints=state['plan']['required_filters'],
                           unresolved_locations=sum(x['status'] != 'resolved' for x in state['locations'].values()))
            try:
                async with asyncio.timeout(min(self.model_timeout_seconds, remaining_seconds(state['ctx']))):
                    reply = await self.model.ainvoke([
                        {'role': 'system', 'content': '你只负责当前这一次任务选择，程序随后才会调用真实工具。只能从 available_tasks 复制一个 task_id，禁止执行或模拟任务，禁止编造房源、坐标、工具结果和后续对话。只输出一行 JSON，恰好包含 task_id 和 reason 两个非空字符串，reason 不超过30字。不用Markdown。JSON结束后输出 <END_DECISION> 并立即结束。'},
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
        return dict(selected_task=selected, decision_reason=reason, issues=problems)

    async def execute(self, state):
        # 二次核对，不能让模型输出直接成为外部调用参数。
        selected = state['selected_task']
        if selected not in self.available_tasks(state):
            return dict(selected_task=None, stop_reason='invalid_state', issues=state['issues'] + [
                issue('INVALID_STATE', '选择的任务已经不可执行', source=None)])
        result = await self.dispatcher.execute(selected, state)
        updated = deepcopy(state)
        tid, kind = selected['task_id'], selected['kind']
        attempts = updated['attempts'].get(tid, 0) + 1
        updated['attempts'][tid] = attempts
        updated['task_issues'][tid] = result['issues']
        if kind == 'search_page':
            source = next(q['source'] for q in state['plan']['queries'] if q['query_id'] == selected['query_id'])
        else:
            source = self.dispatcher.location.provider.source if kind == 'locate' else state['listings'][selected['listing_key']]['source']
        updated['history'].append(dict(task_id=tid, kind=kind, source=source, status=result['status'],
                                       attempt=attempts, reason=state['decision_reason']))
        retryable = any(p['retryable'] for p in result['issues'])
        # partial 搜索页已有事实和游标，不重取；详情/定位失败可有界重试。
        retry = retryable and attempts <= self.max_retries and (kind != 'search_page' or result['data'] is None)
        if retry:
            delay = max((p['retry_after_seconds'] or 0 for p in result['issues']), default=0)
            try:
                if delay >= remaining_seconds(state['ctx']):
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
    from api import create_live_search_service
    from config import ModelConfigurationError
    from part45.aggregation import aggregate
    from time import monotonic

    parser = argparse.ArgumentParser(description='真实 2→3a→3b 联调；缺少真实依赖时失败，不使用虚拟响应')
    parser.add_argument('--live', action='store_true', help='兼容旧命令；现在默认就是 live')
    parser.add_argument('--input', type=Path, help='一个或多个 {plan, ctx} 的真实搜索输入，省略时使用三组实际区域查询')
    parser.add_argument('--output', type=Path, help='可选：保存本次真实调用的输入、执行历史及输出 JSON')
    args = parser.parse_args()

    async def main():
        if args.input:
            loaded = json.loads(args.input.read_text())
            cases = loaded if isinstance(loaded, list) else [loaded]
        else:
            cases = []
            for name, maximum in [('Tampines', 4000), ('Clementi', 4500), ('Punggol', 4000)]:
                identity = str(uuid4())
                plan = dict(plan_id=identity, profile_version=1, attempt_id=identity, intent='rent',
                    required_filters=dict(currency='SGD', max_price=maximum, price_period='month',
                        rental_scope='whole_unit', locations=[name.upper()], min_bedrooms=2),
                    queries=[dict(query_id='q-' + name.lower(), source='propertyguru', text=name, cursor=None)],
                    page_limit=1, candidate_limit=1, source_mode='live', reason='真实联调：按给定整租需求执行一页搜索')
                ctx = dict(user_id='live-check', run_id=identity, conversation_id=identity, attempt_id=identity,
                    trace_id=identity, call_id=identity, source_mode='live',
                    deadline_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat())
                cases.append(dict(plan=plan, ctx=ctx))
        records, passed = [], 0
        for case in cases:
            started = monotonic()
            try:
                service = create_live_search_service()
                state = await service.run(case['plan'], ctx=case['ctx'])
                result = aggregate(state, started)
                # 一页/一个候选可能达到额度；但三项能力必须有真实成功输出，模型也必须真实决策。
                kinds = {h['kind'] for h in state['history'] if h['status'] == 'success'}
                accepted = ({'search_page', 'read_detail', 'locate'} <= kinds
                    and any(x['status'] == 'resolved' for x in state['locations'].values())
                    and not any(p['source'] == 'model' for p in result['issues']))
                record = dict(input=case, history=state['history'], output=result, live_chain_verified=accepted)
            except (ProviderError, ModelConfigurationError) as exc:
                problem = exc.issue if isinstance(exc, ProviderError) else issue('MODEL_UNAVAILABLE', str(exc), source='model')
                record = dict(input=case, output=dict(status='error', data=None, issues=[problem]), live_chain_verified=False)
            passed += record['live_chain_verified']
            records.append(record)
            print(json.dumps(record, ensure_ascii=False), flush=True)
        if args.output:
            args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2))
        print(f'真实 2→3a→3b 链路通过 {passed}/{len(cases)}；只有三项能力实际成功才通过。')
        return 0 if passed == len(cases) else 1

    raise SystemExit(asyncio.run(main()))
