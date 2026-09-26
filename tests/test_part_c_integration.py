import copy
import unittest

from property_agent.evaluation import service as part_c
from langgraph.checkpoint.memory import InMemorySaver

from property_agent.decision import (
    DEFAULT_POLICY,
    PartCEvaluationModule,
    build_decision_graph,
    build_stub_deps,
    initial_state,
)
from property_agent.decision.stubs import ScriptedSearchRunner
from property_agent.persistence.wiring import build_postgres_deps
from tests.support import build_ctx, build_outcome, load_profile
from tests.refactor_scenarios import UnavailableModel


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

    def test_room_listing_never_describes_placeholder_zero_bedrooms(self):
        listing = copy.deepcopy(build_outcome(eligible=1)["listing_snapshot"]["items"][0])
        listing["bedrooms"] = 0
        listing["attributes"]["listing_scope"] = "room"
        listing["evidence"].append(
            {
                "evidence_id": f"{listing['listing_key']}:scope-test",
                "field": "attributes.listing_scope",
                "value": "room",
                "source_url": listing["source_url"],
                "observed_at": listing["fetched_at"],
                "excerpt": "PropertyGuru detail: Common room",
            }
        )

        item = part_c._recommendation_item(listing, 1, [])
        texts = [claim["text"] for claim in item["reasons"]]

        self.assertIn("The source lists this property as a private room.", texts)
        self.assertFalse(any("0 bedrooms" in text for text in texts))

    async def test_evaluation_prompt_describes_card_evidence_as_sufficient_without_detail_lookup(self):
        class CapturingEvaluationModel(part_c.DeepSeekEvaluationReviewModel):
            def __init__(self):
                self.system_prompt = ""

            async def _converse(self, system, prompt):
                self.system_prompt = system
                return {
                    "next_action": prompt["allowed_next_actions"][0],
                    "next_reason_code": "prompt_check",
                    "summary": "Prompt check",
                    "limitations": [],
                }

        outcome = build_outcome(eligible=3)
        model = CapturingEvaluationModel()
        await model.evaluate(
            load_profile(),
            outcome["retrieval_result"],
            outcome["screen_result"],
            outcome["listing_snapshot"]["items"],
            outcome["coverage"],
            DEFAULT_POLICY,
        )

        self.assertIn(
            "no additional detail-page lookup was required",
            model.system_prompt,
        )
        self.assertIn(
            "Never phrase this as 'no detail page was fetched'",
            model.system_prompt,
        )

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
        self.assertEqual(
            result["published_recommendation"]["limitations"][0],
            "Recommendations are based on the available PropertyGuru evidence. "
            "Any requirement without explicit evidence is identified separately below.",
        )
        self.assertNotIn(
            "Module C did not independently verify every hard requirement; check Module B's evidence and unresolved fields.",
            result["published_recommendation"]["limitations"],
        )

    async def test_graph_publishes_with_deterministic_fallback_without_model(self):
        # None enables automatic model configuration; inject an explicit outage
        # so this regression never depends on credentials or another test.
        part_c.configure_evaluation_review_model(UnavailableModel())
        profile = load_profile()
        deps = build_stub_deps(profile)
        deps.module_c = PartCEvaluationModule()
        graph = build_decision_graph(deps).compile(checkpointer=InMemorySaver())
        result = await graph.ainvoke(
            initial_state(
                ctx=build_ctx("run-part-c-fallback"),
                profile=profile,
                outcome=build_outcome(eligible=3),
            ),
            {"configurable": {"thread_id": "run-part-c-fallback"}},
        )

        self.assertEqual(result["completion_reason"], "published")
        self.assertIn(
            "Model evaluation was unavailable; rules were used to rank candidates and determine the next step.",
            result["published_recommendation"]["limitations"],
        )
        self.assertEqual(result["last_issues"][0]["code"], "MODEL_UNAVAILABLE")

    async def test_part_c_unsupported_relaxation_finishes_without_mutating_profile(self):
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
        # Current C does not invent relaxation proposals. An unsupported model
        # ask_user response falls back to finish when no next page is available.
        self.assertNotIn("__interrupt__", result)
        self.assertEqual(result["decision"]["action"], "finish")
        self.assertEqual(result["evaluation"]["assessment"]["relaxation_proposals"], [])
        self.assertEqual(deps.profiles.current_version(profile["profile_id"]), profile["version"])
        self.assertIn("Model evaluation was unavailable; rules were used to rank candidates and determine the next step.",
                      result["evaluation"]["recommendation"]["limitations"])


if __name__ == "__main__":
    unittest.main()
