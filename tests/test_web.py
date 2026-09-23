"""验证网页边界：确认版本、选择上下文、重试与 A/C 图集成。"""
import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from web.server import WebBridge

@dataclass
class Turn:
    assistant_response: str = 'Ready'
    phase: str = 'published'
    run_id: str = 'run-1'
    recommendation: dict | None = None

class FakeGraph:
    def __init__(self, values):
        self.values = values
    async def aget_state(self, config):
        return SimpleNamespace(values=self.values)

class FakeOrchestrator:
    def __init__(self):
        self.calls = []
        self.a_graph = FakeGraph({'status':'awaiting_confirmation','profile':{'version':2},'confirmation':{'confirmation_id':'c2','profile_version':2,'status':'pending'}})
        self.decision_graph = FakeGraph({'listing_snapshot':{'items':[{'listing_key':'home-1','title':'A real returned home','price':{'amount':3000,'currency':'SGD','period':'month'}}]}})
    async def handle_message(self, text, **kwargs):
        self.calls.append((text, kwargs))
        return Turn(recommendation={'ordered_items':[{'listing_key':'home-1','rank':1,'reasons':[]}]})

class WebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime=FakeOrchestrator()
        self.bridge=WebBridge(self.runtime)
        self.token=self.bridge.create_session()

    async def test_cards_selection_and_retry(self):
        first=await self.bridge.turn(self.token,{'text':'Search','message_id':'1'})
        self.assertEqual(first['cards'][0]['title'],'A real returned home')
        payload={'text':'Compare this home','message_id':'2','selected_listing_keys':['home-1']}
        await self.bridge.turn(self.token,payload)
        self.assertIn('A real returned home',self.runtime.calls[-1][0])
        self.assertIn('reference data, not new requirements',self.runtime.calls[-1][0])
        await self.bridge.turn(self.token,payload)
        self.assertEqual(len(self.runtime.calls),2)

    async def test_invalid_selection_never_calls_graph(self):
        for keys in [['invented'],[{}], 'home-1']:
            with self.assertRaises(ValueError):
                await self.bridge.turn(self.token,{'text':'Compare','message_id':'1','selected_listing_keys':keys})
        self.assertEqual(self.runtime.calls,[])

    async def test_stale_confirmation_rejected(self):
        with self.assertRaises(ValueError):
            await self.bridge.turn(self.token,{'text':'Confirm','message_id':'1','confirmation_id':'c1'})
        await self.bridge.turn(self.token,{'text':'Confirm','message_id':'2','confirmation_id':'c2'})
        self.assertEqual('confirm',self.runtime.calls[0][0])

    async def test_session_isolation(self):
        await self.bridge.turn(self.token,{'text':'Search','message_id':'1'})
        with self.assertRaises(ValueError):
            await self.bridge.turn(self.bridge.create_session(),{'text':'Compare','message_id':'1','selected_listing_keys':['home-1']})

    async def test_real_graph_confirmation_and_cards(self):
        from tests.test_orchestration import OrchestrationTests, FakeBSearchRunner, _decision_handoff, REQUIREMENT
        from tests.test_requirement_understanding import mock_deepseek_client, complete_sentence_output
        from requirement_understanding import DeepSeekRequirementInterpreter
        fake_b=FakeBSearchRunner([lambda request,profile,ctx:_decision_handoff(profile,ctx,eligible=3)])
        with mock_deepseek_client(complete_sentence_output()) as client:
            runtime=OrchestrationTests()._build_orchestrator(DeepSeekRequirementInterpreter(api_key='test-only',client=client),fake_b)
            bridge=WebBridge(runtime)
            token=bridge.create_session()
            first=await bridge.turn(token,{'text':REQUIREMENT,'message_id':'1'})
            self.assertEqual(first['status'],'awaiting_confirmation')
            self.assertIsNotNone(first['confirmation'])
            result=await bridge.turn(token,{'text':'Confirm my requirements','message_id':'2','confirmation_id':first['confirmation']['confirmation_id']})
            self.assertEqual(result['phase'],'published')
            self.assertEqual(len(result['cards']),3)
            self.assertEqual(len(fake_b.requests),1)

class CancellationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import asyncio
        self.entered = asyncio.Event()
        self.stopped = asyncio.Event()
        self.runtime = FakeOrchestrator()
        async def blocking(*args, **kwargs):
            from property_agent.evaluation_trace import stage_span

            with stage_span('B', 'search_for_request'):
                self.entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    self.stopped.set()
        self.runtime.handle_message = blocking
        self.bridge = WebBridge(self.runtime)
        self.token = self.bridge.create_session()

    async def test_cancel_reaches_backend_and_releases_session(self):
        import asyncio
        payload = {'text': 'Search', 'message_id': 'slow'}
        task = asyncio.create_task(self.bridge.turn(self.token, payload))
        await self.entered.wait()
        result = await self.bridge.cancel(self.token, payload)
        self.assertEqual(result['status'], 'cancelled')
        self.assertTrue(self.stopped.is_set())
        self.assertEqual((await task)['phase'], 'cancelled')
        self.assertEqual((await self.bridge.turn(self.token, payload))['phase'], 'cancelled')
        self.runtime.handle_message = FakeOrchestrator().handle_message
        self.assertEqual((await self.bridge.turn(self.token, {'text':'Next', 'message_id':'next'}))['phase'], 'published')

    async def test_progress_reports_the_actual_running_node(self):
        import asyncio

        payload = {'text': 'Search', 'message_id': 'progress'}
        task = asyncio.create_task(self.bridge.turn(self.token, payload))
        await self.entered.wait()
        progress = await self.bridge.progress(self.token, payload)
        self.assertEqual(progress, {
            'status': 'running',
            'stage': 'B',
            'operation': 'search_for_request',
        })
        await self.bridge.cancel(self.token, payload)
        await task

    async def test_cancel_before_turn_never_starts_search(self):
        payload = {'text':'Search','message_id':'early'}
        await self.bridge.cancel(self.token, payload)
        self.assertEqual((await self.bridge.turn(self.token,payload))['phase'],'cancelled')
        self.assertFalse(self.entered.is_set())

    async def test_cancellation_is_scoped_to_session_and_message(self):
        import asyncio
        payload = {'text':'Search','message_id':'slow'}
        task = asyncio.create_task(self.bridge.turn(self.token,payload))
        await self.entered.wait()
        await self.bridge.cancel(self.bridge.create_session(),payload)
        await self.bridge.cancel(self.token, {'message_id':'different'})
        self.assertFalse(task.done())
        await self.bridge.cancel(self.token,payload)
        await task

    async def test_timeout_cancels_backend(self):
        self.bridge.turn_timeout_seconds = .03
        result = await self.bridge.turn(self.token, {'text':'Search','message_id':'timeout'})
        self.assertEqual(result['phase'],'timed_out')
        self.assertTrue(self.stopped.is_set())
        self.assertIsNone(self.bridge.sessions[self.token]['active'])

    async def test_completed_turn_wins_late_cancel(self):
        self.runtime.handle_message = FakeOrchestrator().handle_message
        payload = {'text':'Search','message_id':'done'}
        result = await self.bridge.turn(self.token,payload)
        self.assertEqual((await self.bridge.cancel(self.token,payload))['status'],'completed')
        self.assertEqual(await self.bridge.turn(self.token,payload),result)

class ConfirmationCompletenessTests(unittest.TestCase):
    def test_payment_period_is_required_before_confirmation_and_clears(self):
        from requirement_understanding.workflow import assess_completeness, select_clarification
        from tests.support import load_profile
        profile = load_profile()
        profile['listing_constraints'] = [c for c in profile['listing_constraints'] if c['field_path'] != 'price.period']
        for c in profile['listing_constraints']:
            if c['field_path'] == 'price.amount':
                c['source']['text'] = 'up to SGD 2500'
        assessed = assess_completeness({'profile':profile})
        self.assertEqual(assessed['workflow_route'],'select_clarification')
        self.assertIn('listing_constraints.price.period',assessed['profile']['unresolved'])
        questions = select_clarification(assessed)['clarification_questions']
        self.assertTrue(any(q['text']=='Is your rental budget per month or per week?' for q in questions))
        profile = assessed['profile']
        import copy
        period = copy.deepcopy(next(c for c in profile['listing_constraints'] if c['field_path']=='price.currency'))
        period.update(constraint_id='period',field_path='price.period',operator='eq',value='month')
        profile['listing_constraints'].append(period)
        self.assertNotIn('listing_constraints.price.period',assess_completeness({'profile':profile})['profile']['unresolved'])
