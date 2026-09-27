"""Graph-level walkthrough of the decision stage: from module C's results to delivery, clarification, supplementary search, and termination.

Both module C and module B use stand-ins, so these tests require no network or keys. Assertions target business behavior,
not verbatim comparison of model wording.
"""
import copy
import unittest

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from property_agent.decision import (
    DEFAULT_POLICY,
    build_decision_graph,
    build_stub_deps,
    initial_state,
)
from tests.support import (
    ELIGIBLE_KEYS,
    OVER_BUDGET_AMOUNT,
    OVER_BUDGET_KEY,
    build_ctx,
    build_outcome,
    load_profile,
    load_snapshot,
)
from property_agent.profiles import read_relaxable_value


class DecisionGraphCase(unittest.IsolatedAsyncioTestCase):
    """Each test case gets its own independent set of stand-ins and thread."""

    def setUp(self) -> None:
        self.profile = load_profile()
        self.deps = build_stub_deps(self.profile)
        self.graph = build_decision_graph(self.deps).compile(checkpointer=InMemorySaver())

    async def run_graph(self, *, run_id: str = "run-001", policy=None, **outcome_kwargs):
        ctx = build_ctx(run_id)
        state = initial_state(
            ctx=ctx,
            profile=self.profile,
            outcome=build_outcome(**outcome_kwargs),
            policy=policy,
        )
        self.config = {"configurable": {"thread_id": ctx["run_id"]}}
        return await self.graph.ainvoke(state, self.config)

    async def resume(self, answer: dict):
        return await self.graph.ainvoke(Command(resume=answer), self.config)

    def interrupted_question(self, result: dict) -> dict:
        self.assertIn("__interrupt__", result, "should have stopped waiting for the user's answer")
        return result["__interrupt__"][0].value["pending_question"]


class PublishTests(DecisionGraphCase):
    async def test_enough_matches_are_published(self):
        result = await self.run_graph(eligible=3)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["completion_reason"], "published")
        self.assertEqual(result["decision"]["action"], "publish")
        self.assertFalse(result["delivery_is_partial"])
        self.assertIsNotNone(result["final_result_id"])
        self.assertEqual(len(result["published_recommendation"]["ordered_items"]), 3)
        self.assertEqual(len(self.deps.recommendations.saved), 1)

    async def test_eligible_count_comes_from_screen_result(self):
        """The count is based on the deduplicated count of ScreenResult.eligible, not the number of web pages or the retrieval Top-K."""
        result = await self.run_graph(eligible=2, include_unknown_price=True)
        self.assertEqual(result["eligible_count"], 2)
        self.assertNotEqual(result["decision"]["action"], "publish")

    async def test_display_limit_truncates_but_keeps_model_order(self):
        policy = {**DEFAULT_POLICY, "display_limit": 3}
        result = await self.run_graph(eligible=7, policy=policy)
        items = result["published_recommendation"]["ordered_items"]
        self.assertEqual([item["rank"] for item in items], [1, 2, 3])
        # Truncation only cuts the tail; it does not reorder the model's given order by price or any number.
        self.assertEqual([item["listing_key"] for item in items], ELIGIBLE_KEYS[:3])
        self.assertTrue(
            any("B has 7 candidates in total" in line for line in result["published_recommendation"]["limitations"])
        )

    async def test_publish_is_idempotent_across_lost_checkpoints(self):
        """When the business transaction succeeds but the checkpoint is lost, recovery does not duplicate inserted recommendations."""
        first = await self.run_graph(eligible=3)
        self.config = {"configurable": {"thread_id": "thread-after-crash"}}
        second = await self.graph.ainvoke(
            initial_state(
                ctx=build_ctx("run-001"),
                profile=self.profile,
                outcome=build_outcome(eligible=3),
            ),
            self.config,
        )
        self.assertEqual(first["final_result_id"], second["final_result_id"])
        self.assertEqual(len(self.deps.recommendations.saved), 1)


class RelaxationTests(DecisionGraphCase):
    async def test_short_results_ask_before_changing_requirements(self):
        result = await self.run_graph(eligible=2, include_over_budget=True)
        question = self.interrupted_question(result)
        self.assertEqual(question["reason_code"], "insufficient_candidates")
        proposal = question["proposals"][0]
        self.assertEqual(proposal["field"], "listing_constraints.price.amount")
        self.assertEqual(proposal["old_value"], 3500)
        self.assertEqual(proposal["proposed_value"], OVER_BUDGET_AMOUNT)
        self.assertTrue(proposal["requires_user_confirmation"])
        self.assertEqual(proposal["evidence_listing_keys"], [OVER_BUDGET_KEY])
        self.assertIn("This search found 2 listings meeting your requirements, fewer than requested.", question["text"])
        self.assertNotIn("No listings in this search meet all current requirements", question["text"])
        # The proposal has not yet been accepted, so the profile must remain completely unchanged.
        self.assertEqual(self.deps.profiles.current_version("mock-profile-001"), 1)
        self.assertEqual(self.deps.recommendations.saved, {})

    async def test_ask_user_says_none_when_zero_eligible(self):
        result = await self.run_graph(eligible=0, include_over_budget=True)
        question = self.interrupted_question(result)
        self.assertIn("No listings in this search meet all current requirements.", question["text"])
        self.assertNotIn("fewer than requested", question["text"])
        self.assertEqual(self.deps.recommendations.saved, {})

    async def test_c_can_ask_even_when_enough_matches(self):
        """Listen to C: when there are enough, if evaluate still suggests ask_user, ask first and explain that several sets already exist."""
        snapshot = load_snapshot()
        ordered = []
        by_key = {item["listing_key"]: item for item in snapshot["items"]}
        for rank, key in enumerate(ELIGIBLE_KEYS[:3], start=1):
            price = by_key[key].get("price") or {}
            ordered.append(
                {
                    "listing_key": key,
                    "rank": rank,
                    "reasons": [
                        {
                            "kind": "fact",
                            "text": f"The source shows rent {price.get('currency')} {price.get('amount')}, within budget.",
                            "evidence_ids": list(price.get("evidence_ids") or []),
                        }
                    ],
                    "tradeoffs": [],
                    "unknowns": ["Current rental availability has not yet been verified with the agent"],
                }
            )
        self.deps.module_c.evaluations.append(
            {
                "status": "success",
                "data": {
                    "profile_version": 1,
                    "snapshot_id": snapshot["snapshot_id"],
                    "recommendation": {
                        "ordered_items": ordered,
                        "summary": "A total of 3 qualified candidates this time.",
                        "limitations": ["Only covers this query"],
                    },
                    "assessment": {
                        "constraint_findings": [],
                        "search_directive": None,
                        "relaxation_proposals": [
                            {
                                "proposal_id": "relax-price-3501",
                                "field": "listing_constraints.price.amount",
                                "old_value": 3500,
                                "proposed_value": OVER_BUDGET_AMOUNT,
                                "reason": "The rent of the listing excluded this round is 3501.",
                                "evidence_listing_keys": [OVER_BUDGET_KEY],
                                "requires_user_confirmation": True,
                            }
                        ],
                        "next_action": "ask_user",
                        "next_reason_code": "relaxation_available",
                    },
                },
                "issues": [],
            }
        )
        result = await self.run_graph(eligible=3, include_over_budget=True)
        question = self.interrupted_question(result)
        self.assertEqual(result["decision"]["action"], "ask_user")
        self.assertIn("This search found 3 listings meeting your requirements, fewer than requested.", question["text"])
        self.assertNotIn("No listings in this search meet all current requirements", question["text"])
        self.assertEqual(self.deps.recommendations.saved, {})

    async def test_accepting_proposal_updates_profile_and_supersedes_run(self):
        result = await self.run_graph(eligible=2, include_over_budget=True)
        question = self.interrupted_question(result)
        proposal_id = question["proposals"][0]["proposal_id"]
        final = await self.resume(
            {
                "client_message_id": "msg-accept-1",
                "question_id": question["question_id"],
                "expected_state_version": question["state_version"],
                "action": "accept_proposal",
                "proposal_id": proposal_id,
            }
        )
        self.assertEqual(final["status"], "superseded")
        self.assertEqual(final["completion_reason"], "profile_updated")
        self.assertEqual(final["next_run_request"]["accepted_proposal_id"], proposal_id)

        updated = self.deps.profiles.profiles["mock-profile-001"]
        self.assertEqual(updated["version"], 2)
        self.assertEqual(read_relaxable_value(updated, "listing_constraints.price.amount"), OVER_BUDGET_AMOUNT)
        self.assertEqual(updated["field_sources"]["listing_constraints.price.amount"], "msg-accept-1")
        self.assertEqual(updated["confirmed_version"], 2)
        # The old run does not publish recommendations using the old candidate set; the new round of search is handled by the new run.
        self.assertEqual(self.deps.recommendations.saved, {})

    async def test_natural_language_answer_is_interpreted_before_routing(self):
        result = await self.run_graph(eligible=2, include_over_budget=True)
        question = self.interrupted_question(result)
        final = await self.resume(
            {
                "client_message_id": "msg-natural-accept",
                "text": "Okay, I accept raising the budget",
            }
        )
        self.assertEqual(final["completion_reason"], "profile_updated")
        self.assertEqual(
            final["next_run_request"]["accepted_proposal_id"],
            question["proposals"][0]["proposal_id"],
        )
        saved = self.deps.questions.answers[
            f"run-001:{question['question_id']}"
        ]
        self.assertEqual(saved["answer_text"], "Okay, I accept raising the budget")

    async def test_raw_string_resume_is_interpreted(self):
        await self.run_graph(eligible=2, include_over_budget=True)
        final = await self.resume("I do not accept the adjustment, keep it as is")
        self.assertEqual(final["completion_reason"], "user_declined")
        self.assertTrue(final["delivery_is_partial"])

    async def test_declining_keeps_the_legal_partial_result(self):
        result = await self.run_graph(eligible=2, include_over_budget=True)
        question = self.interrupted_question(result)
        final = await self.resume(
            {
                "client_message_id": "msg-decline-1",
                "question_id": question["question_id"],
                "expected_state_version": question["state_version"],
                "action": "decline",
            }
        )
        self.assertEqual(final["status"], "completed")
        self.assertEqual(final["completion_reason"], "user_declined")
        self.assertTrue(final["delivery_is_partial"])
        self.assertEqual(len(final["published_recommendation"]["ordered_items"]), 2)
        self.assertEqual(self.deps.profiles.current_version("mock-profile-001"), 1)

    async def test_cancelling_stops_the_run(self):
        result = await self.run_graph(eligible=2, include_over_budget=True)
        question = self.interrupted_question(result)
        final = await self.resume(
            {
                "client_message_id": "msg-cancel-1",
                "question_id": question["question_id"],
                "expected_state_version": question["state_version"],
                "action": "cancel",
            }
        )
        self.assertEqual(final["status"], "cancelled")
        self.assertEqual(final["completion_reason"], "cancelled")
        self.assertEqual(self.deps.recommendations.saved, {})

    async def test_free_text_answer_goes_back_to_requirement_parsing(self):
        result = await self.run_graph(eligible=2, include_over_budget=True)
        question = self.interrupted_question(result)
        final = await self.resume(
            {
                "client_message_id": "msg-answer-1",
                "question_id": question["question_id"],
                "expected_state_version": question["state_version"],
                "action": "answer",
                "answer": "Never mind, I want to look at Jurong East.",
            }
        )
        self.assertEqual(final["completion_reason"], "handed_to_onboarding")
        self.assertEqual(final["next_run_request"]["reason_code"], "user_message")
        # This stage does not guess new hard constraints; the profile remains unchanged.
        self.assertEqual(self.deps.profiles.current_version("mock-profile-001"), 1)


class StaleAnswerTests(DecisionGraphCase):
    async def test_stale_state_version_is_rejected_and_run_keeps_waiting(self):
        result = await self.run_graph(eligible=2, include_over_budget=True)
        question = self.interrupted_question(result)
        rejected = await self.resume(
            {
                "client_message_id": "msg-stale",
                "question_id": question["question_id"],
                "expected_state_version": question["state_version"] + 99,
                "action": "decline",
            }
        )
        again = self.interrupted_question(rejected)
        self.assertEqual(again["question_id"], question["question_id"])
        self.assertEqual(rejected["last_issues"][-1]["code"], "STATE_CONFLICT")
        self.assertEqual(self.deps.profiles.current_version("mock-profile-001"), 1)

        # A valid answer can still be consumed.
        final = await self.resume(
            {
                "client_message_id": "msg-decline-2",
                "question_id": question["question_id"],
                "expected_state_version": question["state_version"],
                "action": "decline",
            }
        )
        self.assertEqual(final["completion_reason"], "user_declined")

    async def test_answer_cannot_be_consumed_twice(self):
        result = await self.run_graph(eligible=2, include_over_budget=True)
        question = self.interrupted_question(result)
        answer = {
            "client_message_id": "msg-accept-1",
            "question_id": question["question_id"],
            "expected_state_version": question["state_version"],
            "action": "accept_proposal",
            "proposal_id": question["proposals"][0]["proposal_id"],
        }
        await self.resume(answer)
        # Answering the same question again should not change the profile again.
        self.assertEqual(self.deps.profiles.current_version("mock-profile-001"), 2)
        self.assertFalse(self.deps.questions.mark_answered("run-001", question["question_id"]))

    async def test_requirement_change_during_wait_supersedes_the_run(self):
        result = await self.run_graph(eligible=2, include_over_budget=True)
        question = self.interrupted_question(result)
        # The user changed requirements elsewhere: a waiting old run must not publish conclusions based on the old version.
        bumped = copy.deepcopy(self.profile)
        bumped["version"] = 2
        self.deps.profiles.put(bumped)

        final = await self.resume(
            {
                "client_message_id": "msg-decline-3",
                "question_id": question["question_id"],
                "expected_state_version": question["state_version"],
                "action": "decline",
            }
        )
        self.assertEqual(final["status"], "superseded")
        self.assertEqual(final["completion_reason"], "profile_superseded")
        self.assertEqual(self.deps.recommendations.saved, {})


class ResearchTests(DecisionGraphCase):
    async def test_research_is_tried_before_asking_for_relaxation(self):
        self.deps.search_runner.outcomes.append(
            {
                "status": "success",
                "data": build_outcome(eligible=3, attempt_id="attempt-002"),
                "issues": [],
            }
        )
        result = await self.run_graph(eligible=2, include_over_budget=True, has_more=True)
        self.assertEqual(len(self.deps.search_runner.calls), 1)
        self.assertEqual(
            [item["attempt_id"] for item in self.deps.search_runner.histories[0]],
            ["attempt-001"],
        )
        self.assertEqual(
            self.deps.search_runner.calls[0]["strategy_changes"][0]["kind"], "next_page"
        )
        self.assertEqual(result["search_attempts_used"], 2)
        self.assertEqual(result["attempt_id"], "attempt-002")
        self.assertEqual(
            [item["attempt_id"] for item in result["previous_attempts"]],
            ["attempt-001", "attempt-002"],
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["completion_reason"], "published")

    async def test_failed_research_attempt_still_counts_and_stops(self):
        result = await self.run_graph(eligible=2, include_over_budget=True, has_more=True)
        self.assertEqual(result["search_attempts_used"], 2)
        self.assertEqual(len(result["previous_attempts"]), 2)
        self.assertEqual(result["previous_attempts"][-1]["status"], "error")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["completion_reason"], "source_failure")
        self.assertEqual(self.deps.recommendations.saved, {})


class FailureTests(DecisionGraphCase):
    async def test_all_sources_failed_is_not_reported_as_no_matches(self):
        result = await self.run_graph(search_status="error", failure_code="TIMEOUT")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["completion_reason"], "source_failure")
        self.assertIsNone(result["pending_question"])
        self.assertEqual(self.deps.module_c.evaluate_calls, [])

    async def test_evaluation_service_failure_is_not_a_pass(self):
        self.deps.module_c.evaluations.append(
            {
                "status": "error",
                "data": None,
                "issues": [
                    {
                        "code": "MODEL_UNAVAILABLE",
                        "message": "The evaluation model is unavailable",
                        "field_path": None,
                        "source": None,
                        "retryable": True,
                        "retry_after_seconds": None,
                    }
                ],
            }
        )
        result = await self.run_graph(eligible=3)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["completion_reason"], "review_missing")
        self.assertEqual(self.deps.recommendations.saved, {})

    async def test_no_matches_and_no_options_finishes_cleanly(self):
        result = await self.run_graph(eligible=0)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["completion_reason"], "insufficient_candidates")
        self.assertIsNone(result["final_result_id"])


class RepairTests(DecisionGraphCase):
    def blocking_review(self) -> dict:
        return {
            "status": "success",
            "data": {
                "passed": False,
                "issues": [
                    {
                        "code": "UNSUPPORTED_CLAIM",
                        "listing_key": "propertyguru:mock-000901",
                        "field_path": "recommendation.ordered_items[0].reasons[0]",
                        "message": "There is no source evidence for a 5-minute walk.",
                        "severity": "blocking",
                        "suggested_fix": "Delete this fact or add evidence.",
                    }
                ],
            },
            "issues": [],
        }

    async def test_blocking_issues_spend_one_repair_then_publish(self):
        self.deps.module_c.reviews.append(self.blocking_review())
        result = await self.run_graph(eligible=3)
        self.assertEqual(result["repairs_used"], 1)
        self.assertEqual(len(self.deps.module_c.evaluate_calls), 2)
        # The repair call must include the specific problem, otherwise the model has no way to fix it.
        self.assertIsNotNone(self.deps.module_c.evaluate_calls[1]["repair_context"])
        self.assertIsNone(self.deps.module_c.evaluate_calls[0]["repair_context"])
        self.assertEqual(result["completion_reason"], "published")

    async def test_repair_budget_exhausted_does_not_publish_the_draft(self):
        self.deps.module_c.reviews.extend([self.blocking_review(), self.blocking_review()])
        result = await self.run_graph(eligible=3)
        self.assertEqual(result["repairs_used"], 1)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["completion_reason"], "repair_exhausted")
        self.assertEqual(self.deps.recommendations.saved, {})

    async def test_fabricated_listing_is_caught_before_delivery(self):
        snapshot = load_snapshot()
        fabricated = {
            "status": "success",
            "data": {
                "profile_version": 1,
                "snapshot_id": snapshot["snapshot_id"],
                "recommendation": {
                    "ordered_items": [
                        {
                            "listing_key": "propertyguru:does-not-exist",
                            "rank": 1,
                            "reasons": [
                                {"kind": "fact", "text": "Monthly rent 3000.", "evidence_ids": ["nope"]}
                            ],
                            "tradeoffs": [],
                            "unknowns": [],
                        }
                    ],
                    "summary": "Fabricated recommendation",
                    "limitations": [],
                },
                "assessment": {
                    "constraint_findings": [],
                    "search_directive": None,
                    "relaxation_proposals": [],
                },
            },
            "issues": [],
        }
        self.deps.module_c.evaluations.extend([fabricated, copy.deepcopy(fabricated)])
        result = await self.run_graph(eligible=3)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["completion_reason"], "repair_exhausted")
        self.assertEqual(self.deps.recommendations.saved, {})

    async def test_evaluation_for_another_profile_version_is_rejected(self):
        snapshot = load_snapshot()
        stale = {
            "status": "success",
            "data": {
                "profile_version": 99,
                "snapshot_id": snapshot["snapshot_id"],
                "recommendation": {"ordered_items": [], "summary": "", "limitations": []},
                "assessment": {
                    "constraint_findings": [],
                    "search_directive": None,
                    "relaxation_proposals": [],
                },
            },
            "issues": [],
        }
        self.deps.module_c.evaluations.extend([stale, copy.deepcopy(stale)])
        result = await self.run_graph(eligible=3)
        self.assertEqual(result["completion_reason"], "repair_exhausted")
        # The review is provided by the program-side structural check; there is no need to call module C's review again.
        self.assertEqual(self.deps.module_c.review_calls, [])


class GuardTests(DecisionGraphCase):
    def evaluation_with_proposals(self, proposals: list[dict]) -> dict:
        snapshot = load_snapshot()
        return {
            "status": "success",
            "data": {
                "profile_version": 1,
                "snapshot_id": snapshot["snapshot_id"],
                "recommendation": {
                    "ordered_items": [],
                    "summary": "No qualified candidates this round.",
                    "limitations": ["Only covers this query"],
                },
                "assessment": {
                    "constraint_findings": [],
                    "search_directive": None,
                    "relaxation_proposals": proposals,
                },
            },
            "issues": [],
        }

    async def test_tightening_disguised_as_relaxation_is_rejected(self):
        self.deps.module_c.evaluations.append(
            self.evaluation_with_proposals(
                [
                    {
                        "proposal_id": "tighten",
                        "field": "listing_constraints.price.amount",
                        "old_value": 3500,
                        "proposed_value": 3000,
                        "reason": "Lower the budget",
                        "evidence_listing_keys": [],
                        "requires_user_confirmation": True,
                    }
                ]
            )
        )
        result = await self.run_graph(eligible=0)
        self.assertIsNone(result["pending_question"])
        self.assertEqual(result["completion_reason"], "insufficient_candidates")
        self.assertIn(
            "CONSTRAINT_CHANGE_NOT_ALLOWED", [issue["code"] for issue in result["last_issues"]]
        )

    async def test_proposal_outside_whitelist_is_rejected(self):
        self.deps.module_c.evaluations.append(
            self.evaluation_with_proposals(
                [
                    {
                        "proposal_id": "switch-intent",
                        "field": "intent",
                        "old_value": "rent",
                        "proposed_value": "buy",
                        "reason": "Change to buying a home",
                        "evidence_listing_keys": [],
                        "requires_user_confirmation": True,
                    }
                ]
            )
        )
        result = await self.run_graph(eligible=0)
        self.assertIsNone(result["pending_question"])
        codes = [issue["code"] for issue in result["last_issues"]]
        self.assertIn("CONSTRAINT_CHANGE_NOT_ALLOWED", codes)

    async def test_proposal_built_on_a_stale_value_is_rejected(self):
        self.deps.module_c.evaluations.append(
            self.evaluation_with_proposals(
                [
                    {
                        "proposal_id": "stale-base",
                        "field": "listing_constraints.price.amount",
                        "old_value": 3000,
                        "proposed_value": 4000,
                        "reason": "Constructed based on the old budget",
                        "evidence_listing_keys": [],
                        "requires_user_confirmation": True,
                    }
                ]
            )
        )
        result = await self.run_graph(eligible=0)
        self.assertIsNone(result["pending_question"])
        self.assertIn("STATE_CONFLICT", [issue["code"] for issue in result["last_issues"]])

    async def test_directive_pointing_at_a_forbidden_source_is_dropped(self):
        snapshot = load_snapshot()
        self.deps.module_c.evaluations.append(
            {
                "status": "success",
                "data": {
                    "profile_version": 1,
                    "snapshot_id": snapshot["snapshot_id"],
                    "recommendation": {"ordered_items": [], "summary": "", "limitations": []},
                    "assessment": {
                        "constraint_findings": [],
                        "search_directive": {
                            "reason_code": "insufficient_candidates",
                            "strategy_changes": [
                                {"kind": "alternate_source", "source": "some-random-site"}
                            ],
                            "base_profile_version": 1,
                            "evidence_listing_keys": [],
                        },
                        "relaxation_proposals": [],
                    },
                },
                "issues": [],
            }
        )
        result = await self.run_graph(eligible=0)
        self.assertEqual(self.deps.search_runner.calls, [])
        self.assertIn("SOURCE_UNAVAILABLE", [issue["code"] for issue in result["last_issues"]])
        self.assertEqual(result["completion_reason"], "insufficient_candidates")


if __name__ == "__main__":
    unittest.main()
