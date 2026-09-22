import unittest

import part_c
from langgraph.checkpoint.memory import InMemorySaver

from property_agent.decision import (
    PartCEvaluationModule,
    build_decision_graph,
    build_stub_deps,
    initial_state,
)
from property_agent.decision.stubs import ScriptedSearchRunner
from property_agent.persistence.wiring import build_postgres_deps
from tests.support import build_ctx, build_outcome, load_profile


class AcceptingPartCModel:
    async def evaluate(
        self, profile, retrieval, screen_result, listings, coverage, policy
    ):
        keys = [item["listing_key"] for item in retrieval["candidates"]]
        enough = len(screen_result["eligible"]) >= policy["min_matches"]
        has_more = bool(coverage.get("next_pages") or coverage.get("has_more"))
        if enough:
            action, reason = "publish", "enough_matches"
        elif has_more:
            action, reason = "research", "insufficient_candidates"
        else:
            action, reason = "ask_user", "relaxation_available"
        return part_c.EvaluationDecision(
            selected_listing_keys=keys[: policy["display_limit"]],
            enough_candidates=enough,
            next_action=action,
            next_reason_code=reason,
            summary="Part C integration test",
            limitations=[],
        )

    async def review(self, profile, evaluation, listings, policy):
        return []


class PartCIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        part_c.configure_evaluation_review_model(AcceptingPartCModel())

    def tearDown(self):
        part_c.configure_evaluation_review_model(None)

    def test_fixture_profile_is_confirmed_conversation_profile(self):
        profile = load_profile()
        self.assertEqual(profile["status"], "confirmed")
        self.assertEqual(profile["confirmed_version"], profile["version"])
        self.assertTrue(profile["listing_constraints"])
        self.assertTrue(
            all(item["source"]["text"] == "" for item in profile["listing_constraints"])
        )

    def test_postgres_wiring_uses_part_c_by_default(self):
        deps = build_postgres_deps(
            search_runner=ScriptedSearchRunner(),
            sessions=lambda: None,
            use_deepseek=False,
        )
        self.assertIsInstance(deps.module_c, PartCEvaluationModule)

    async def test_graph_publishes_through_real_part_c(self):
        profile = load_profile()
        deps = build_stub_deps(profile)
        deps.module_c = PartCEvaluationModule()
        graph = build_decision_graph(deps).compile(checkpointer=InMemorySaver())
        result = await graph.ainvoke(
            initial_state(
                ctx=build_ctx("run-part-c-publish"),
                profile=profile,
                outcome=build_outcome(eligible=3),
            ),
            {"configurable": {"thread_id": "run-part-c-publish"}},
        )
        self.assertEqual(result["completion_reason"], "published")
        self.assertEqual(result["published_recommendation"]["summary"], "Part C integration test")

    async def test_part_c_relaxation_uses_listing_constraint_path(self):
        profile = load_profile()
        deps = build_stub_deps(profile)
        deps.module_c = PartCEvaluationModule()
        graph = build_decision_graph(deps).compile(checkpointer=InMemorySaver())
        result = await graph.ainvoke(
            initial_state(
                ctx=build_ctx("run-part-c-relax"),
                profile=profile,
                outcome=build_outcome(eligible=2, include_over_budget=True),
            ),
            {"configurable": {"thread_id": "run-part-c-relax"}},
        )
        question = result["__interrupt__"][0].value["pending_question"]
        self.assertEqual(question["proposals"][0]["field"], "listing_constraints.price.amount")
        self.assertIn("本轮已有 2 套符合硬条件的房源，只是数量还偏少。", question["text"])


if __name__ == "__main__":
    unittest.main()
