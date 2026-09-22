"""决策段的图级走查：从模块 C 的结果到交付、澄清、补搜与终止。

模块 C 与模块 B 都用替身，因此这些测试不需要网络或密钥。断言针对业务行为，
不逐字比对模型文案。
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
    """每个用例一套独立的替身与 thread。"""

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
        self.assertIn("__interrupt__", result, "本应停在等待用户回答")
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
        """数量以 ScreenResult.eligible 的去重数量为准，不看网页条数或检索 Top-K。"""
        result = await self.run_graph(eligible=2, include_unknown_price=True)
        self.assertEqual(result["eligible_count"], 2)
        self.assertNotEqual(result["decision"]["action"], "publish")

    async def test_display_limit_truncates_but_keeps_model_order(self):
        policy = {**DEFAULT_POLICY, "display_limit": 3}
        result = await self.run_graph(eligible=7, policy=policy)
        items = result["published_recommendation"]["ordered_items"]
        self.assertEqual([item["rank"] for item in items], [1, 2, 3])
        # 截断只砍尾部，不按价格或任何数字重排模型给出的顺序。
        self.assertEqual([item["listing_key"] for item in items], ELIGIBLE_KEYS[:3])
        self.assertTrue(
            any("合格候选共 7 条" in line for line in result["published_recommendation"]["limitations"])
        )

    async def test_publish_is_idempotent_across_lost_checkpoints(self):
        """业务事务成功但 checkpoint 丢失时，恢复不重复插入推荐。"""
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
        self.assertIn("本轮已有 2 套符合硬条件的房源，只是数量还偏少。", question["text"])
        self.assertNotIn("没有符合当前硬条件的房源", question["text"])
        # 提案还没被接受，档案必须一字未改。
        self.assertEqual(self.deps.profiles.current_version("mock-profile-001"), 1)
        self.assertEqual(self.deps.recommendations.saved, {})

    async def test_ask_user_says_none_when_zero_eligible(self):
        result = await self.run_graph(eligible=0, include_over_budget=True)
        question = self.interrupted_question(result)
        self.assertIn("本轮没有符合当前硬条件的房源。", question["text"])
        self.assertNotIn("数量还偏少", question["text"])
        self.assertEqual(self.deps.recommendations.saved, {})

    async def test_c_can_ask_even_when_enough_matches(self):
        """听 C：够数时若 evaluate 仍建议 ask_user，就先问，并说明已有若干套。"""
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
                            "text": f"来源显示租金 {price.get('currency')} {price.get('amount')}，未超预算。",
                            "evidence_ids": list(price.get("evidence_ids") or []),
                        }
                    ],
                    "tradeoffs": [],
                    "unknowns": ["当前可租状态尚未向经纪人核实"],
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
                        "summary": "本次共 3 条合格候选。",
                        "limitations": ["仅覆盖本次查询"],
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
                                "reason": "本轮被排除的房源租金为 3501。",
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
        self.assertIn("本轮已有 3 套符合硬条件的房源，只是数量还偏少。", question["text"])
        self.assertNotIn("没有符合当前硬条件的房源", question["text"])
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
        # 旧 run 不拿旧候选集发推荐，新一轮搜索由新 run 负责。
        self.assertEqual(self.deps.recommendations.saved, {})

    async def test_natural_language_answer_is_interpreted_before_routing(self):
        result = await self.run_graph(eligible=2, include_over_budget=True)
        question = self.interrupted_question(result)
        final = await self.resume(
            {
                "client_message_id": "msg-natural-accept",
                "text": "好的，我接受提高预算",
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
        self.assertEqual(saved["answer_text"], "好的，我接受提高预算")

    async def test_raw_string_resume_is_interpreted(self):
        await self.run_graph(eligible=2, include_over_budget=True)
        final = await self.resume("不接受调整，保持原样")
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
                "answer": "算了，我想看看 Jurong East。",
            }
        )
        self.assertEqual(final["completion_reason"], "handed_to_onboarding")
        self.assertEqual(final["next_run_request"]["reason_code"], "user_message")
        # 本段不猜测新硬条件，档案保持原样。
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

        # 有效回答仍然可以被消费。
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
        # 同一个问题再回答一次不应再改档案。
        self.assertEqual(self.deps.profiles.current_version("mock-profile-001"), 2)
        self.assertFalse(self.deps.questions.mark_answered("run-001", question["question_id"]))

    async def test_requirement_change_during_wait_supersedes_the_run(self):
        result = await self.run_graph(eligible=2, include_over_budget=True)
        question = self.interrupted_question(result)
        # 用户在别处改了需求：等待中的旧 run 不能再发布基于旧版本的结论。
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
                        "message": "评价模型不可用",
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
                        "message": "步行 5 分钟没有来源证据。",
                        "severity": "blocking",
                        "suggested_fix": "删除该事实或补充证据。",
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
        # 修复调用必须带上具体问题，否则模型无从改。
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
                                {"kind": "fact", "text": "月租 3000。", "evidence_ids": ["nope"]}
                            ],
                            "tradeoffs": [],
                            "unknowns": [],
                        }
                    ],
                    "summary": "编造的推荐",
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
        # 审查由程序侧结构检查给出，没有必要再调模块 C 的审查。
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
                    "summary": "本轮没有合格候选。",
                    "limitations": ["仅覆盖本次查询"],
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
                        "reason": "降低预算",
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
                        "reason": "改成买房",
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
                        "reason": "基于旧预算构造",
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
