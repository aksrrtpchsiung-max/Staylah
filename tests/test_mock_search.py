import copy
import unittest

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from property_agent.decision import build_decision_graph, build_stub_deps, initial_state
from property_agent.mock_search import (
    MockSearchRunner,
    first_attempt_from_fixture,
    load_search_fixture,
    screen_listings,
)
from tests.support import ELIGIBLE_KEYS, OVER_BUDGET_KEY, UNKNOWN_PRICE_KEY, build_ctx, load_profile


class MockScreenTests(unittest.TestCase):
    def test_search_success_keeps_known_eligible_and_rejects_over_budget(self):
        profile = load_profile()
        result = load_search_fixture("search-success")
        screened = screen_listings(result["data"]["items"], profile)
        eligible = {item["listing_key"] for item in screened["eligible"]}
        rejected = {item["listing_key"] for item in screened["rejected"]}
        verify = {item["listing_key"] for item in screened["needs_verification"]}
        self.assertTrue(set(ELIGIBLE_KEYS).issubset(eligible))
        self.assertIn(OVER_BUDGET_KEY, rejected)
        self.assertIn(UNKNOWN_PRICE_KEY, verify)
        self.assertGreaterEqual(len(eligible), 3)
        self.assertNotIn("propertyguru:mock-000912", eligible)


class MockPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_publish_uses_real_search_fixture(self):
        profile = load_profile()
        deps = build_stub_deps(profile)
        deps.search_runner = MockSearchRunner()
        graph = build_decision_graph(deps).compile(checkpointer=InMemorySaver())
        result = await graph.ainvoke(
            initial_state(
                ctx=build_ctx("run-mock-publish"),
                profile=profile,
                outcome=first_attempt_from_fixture("search-success", profile),
            ),
            {"configurable": {"thread_id": "run-mock-publish"}},
        )
        self.assertEqual(result["completion_reason"], "published")
        self.assertGreaterEqual(result["eligible_count"], 3)
        self.assertEqual(len(deps.recommendations.saved), 1)

    async def test_page_one_researches_page_two(self):
        profile = load_profile()
        deps = build_stub_deps(profile)
        deps.search_runner = MockSearchRunner()
        graph = build_decision_graph(deps).compile(checkpointer=InMemorySaver())
        result = await graph.ainvoke(
            initial_state(
                ctx=build_ctx("run-mock-research"),
                profile=profile,
                outcome=first_attempt_from_fixture("page-1", profile),
            ),
            {"configurable": {"thread_id": "run-mock-research"}},
        )
        self.assertEqual(len(deps.search_runner.calls), 1)
        self.assertEqual(
            deps.search_runner.calls[0]["strategy_changes"][0]["cursor"],
            "mock-page-2",
        )
        self.assertEqual(result["search_attempts_used"], 2)
        self.assertIn(result["status"], {"completed", "waiting_user", "superseded"})

    async def test_short_page_asks_and_accepts_natural_language(self):
        profile = copy.deepcopy(load_profile())
        deps = build_stub_deps(profile)
        deps.search_runner = MockSearchRunner()
        graph = build_decision_graph(deps).compile(checkpointer=InMemorySaver())
        interrupted = await graph.ainvoke(
            initial_state(
                ctx=build_ctx("run-mock-relax"),
                profile=profile,
                outcome=first_attempt_from_fixture(
                    "search-success", profile, item_limit=3, has_more=False
                ),
            ),
            {"configurable": {"thread_id": "run-mock-relax"}},
        )
        self.assertIn("__interrupt__", interrupted)
        final = await graph.ainvoke(
            Command(resume={"client_message_id": "msg-1", "text": "好的，我接受提高预算"}),
            {"configurable": {"thread_id": "run-mock-relax"}},
        )
        self.assertEqual(final["completion_reason"], "profile_updated")
        self.assertEqual(deps.profiles.current_version("mock-profile-001"), 2)

    async def test_timeout_is_source_failure(self):
        profile = load_profile()
        deps = build_stub_deps(profile)
        graph = build_decision_graph(deps).compile(checkpointer=InMemorySaver())
        result = await graph.ainvoke(
            initial_state(
                ctx=build_ctx("run-mock-timeout"),
                profile=profile,
                outcome=first_attempt_from_fixture("search-timeout", profile),
            ),
            {"configurable": {"thread_id": "run-mock-timeout"}},
        )
        self.assertEqual(result["completion_reason"], "source_failure")
        self.assertEqual(deps.recommendations.saved, {})


if __name__ == "__main__":
    unittest.main()
