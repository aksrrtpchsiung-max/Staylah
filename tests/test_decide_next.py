"""Contract test regression for decide_next.

The first 9 cases read tests/fixtures/decision_routes.json directly, avoiding manual transcription of the review draft.
The remaining cases cover rules in the "additional constraints" section of the review draft that have no accompanying JSON samples.
"""
import copy
import json
import unittest
from pathlib import Path

from property_agent.contracts import ContractViolation
from property_agent.decision import DEFAULT_POLICY, decide_next

CASES_PATH = Path(__file__).resolve().parents[1] / "tests/fixtures/decision_routes.json"


def load_cases() -> dict[str, dict]:
    cases = json.loads(CASES_PATH.read_text())["cases"]
    return {case["id"]: case for case in cases if case["function"] == "decide_next"}


class ContractCaseTests(unittest.TestCase):
    """Execute the 9 cases from the review draft one by one; do not rewrite the expected values, and when disagreements arise, go back to the review draft rather than changing the implementation."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = load_cases()
        assert len(cls.cases) == 9, f"Number of cases changed: {sorted(cls.cases)}"

    def test_every_contract_case(self):
        for case_id, case in self.cases.items():
            with self.subTest(case=case_id):
                state = case["input"]["state"]
                policy = case["input"]["policy"]
                expected = case["expected"]
                if "raises" in expected:
                    with self.assertRaises(ContractViolation) as caught:
                        decide_next(state, policy)
                    self.assertEqual(caught.exception.code, expected["raises"]["code"])
                    self.assertEqual(
                        caught.exception.field_path, expected["raises"]["field_path"]
                    )
                else:
                    self.assertEqual(decide_next(state, policy), expected["return"])

    def test_inputs_are_not_mutated(self):
        """DecisionState is a read-only view; pure functions must not modify the caller's object in place."""
        for case_id, case in self.cases.items():
            if "raises" in case["expected"]:
                continue
            with self.subTest(case=case_id):
                state = case["input"]["state"]
                before = copy.deepcopy(state)
                decide_next(state, case["input"]["policy"])
                self.assertEqual(state, before)


def base_state(**overrides) -> dict:
    """A publishable health state; each test only modifies the fields it cares about."""
    state = {
        "run_id": "run-001",
        "state_version": 7,
        "profile_version": 1,
        "current_profile_version": 1,
        "cancelled": False,
        "user_declined": False,
        "deadline_exhausted": False,
        "search_status": "success",
        "search_attempts_used": 1,
        "repairs_used": 0,
        "eligible_count": 3,
        "review": {"passed": True, "issues": []},
        "failure_code": None,
        "search_directive": None,
        "pending_question": None,
    }
    state.update(overrides)
    return state


def directive(**overrides) -> dict:
    payload = {
        "reason_code": "insufficient_candidates",
        "strategy_changes": [{"kind": "next_page", "query_id": "q-001", "cursor": "page-2"}],
        "base_profile_version": 1,
        "evidence_listing_keys": [],
    }
    payload.update(overrides)
    return payload


def question(**overrides) -> dict:
    payload = {
        "question_id": "run-001:state-7:q-1",
        "text": "Should the monthly rent cap be adjusted to SGD 3600?",
        "reason_code": "insufficient_candidates",
        "proposals": [
            {
                "proposal_id": "proposal-001",
                "field": "listing_constraints.price.amount",
                "old_value": 3500,
                "proposed_value": 3600,
                "reason": "The candidate monthly rent of 3501 excluded this time.",
                "evidence_listing_keys": ["L3"],
                "requires_user_confirmation": True,
            }
        ],
        "allowed_actions": ["accept_proposal", "decline", "answer", "cancel"],
        "base_profile_version": 1,
        "state_version": 7,
    }
    payload.update(overrides)
    return payload


def blocking_review() -> dict:
    return {
        "passed": False,
        "issues": [
            {
                "code": "UNSUPPORTED_CLAIM",
                "listing_key": "L1",
                "field_path": "recommendation.ordered_items[0].reasons[1]",
                "message": "There is no source evidence supporting a 5-minute walk.",
                "severity": "blocking",
                "suggested_fix": "Delete this fact or add supporting evidence.",
            }
        ],
    }


class RoutingPriorityTests(unittest.TestCase):
    def test_cancel_beats_publishable_results(self):
        decision = decide_next(base_state(cancelled=True), DEFAULT_POLICY)
        self.assertEqual((decision["action"], decision["reason_code"]), ("stop", "cancelled"))

    def test_superseded_profile_beats_user_declined(self):
        state = base_state(current_profile_version=2, user_declined=True)
        decision = decide_next(state, DEFAULT_POLICY)
        self.assertEqual(decision["reason_code"], "profile_superseded")

    def test_source_failure_beats_insufficient_candidates(self):
        """When all sources fail, do not use "only 0 listings found" to suggest the user increase their budget."""
        state = base_state(
            search_status="error",
            eligible_count=0,
            review=None,
            failure_code="TIMEOUT",
            search_directive=directive(),
            pending_question=question(),
        )
        decision = decide_next(state, DEFAULT_POLICY)
        self.assertEqual((decision["action"], decision["reason_code"]), ("stop", "source_failure"))
        self.assertIsNone(decision["search_directive"])
        self.assertIsNone(decision["pending_question"])

    def test_review_service_failure_is_not_a_pass(self):
        state = base_state(review=None, failure_code="MODEL_UNAVAILABLE")
        decision = decide_next(state, DEFAULT_POLICY)
        self.assertEqual((decision["action"], decision["reason_code"]), ("stop", "review_missing"))

    def test_blocking_review_beats_enough_matches(self):
        state = base_state(eligible_count=10, review=blocking_review())
        self.assertEqual(decide_next(state, DEFAULT_POLICY)["action"], "repair")

    def test_research_preferred_over_asking_for_relaxation(self):
        """When there is no C preference, first supplement the search using a method that preserves the hard constraints, then consider asking the user to compromise."""
        state = base_state(
            eligible_count=1, search_directive=directive(), pending_question=question()
        )
        decision = decide_next(state, DEFAULT_POLICY)
        self.assertEqual(decision["action"], "research")
        self.assertIsNone(decision["pending_question"])

    def test_evaluation_can_prefer_asking_when_research_is_also_legal(self):
        state = base_state(
            eligible_count=1,
            search_directive=directive(),
            pending_question=question(),
            evaluation_next_action="ask_user",
            evaluation_next_reason_code="relaxation_available",
        )
        decision = decide_next(state, DEFAULT_POLICY)
        self.assertEqual(decision["action"], "ask_user")
        self.assertEqual(decision["reason_code"], "relaxation_available")
        self.assertIsNone(decision["search_directive"])

    def test_evaluation_can_skip_publish_to_ask_when_c_requests_it(self):
        """Listen to C: when there are enough results, if evaluate suggests ask_user and the question is ready, ask the user first."""
        state = base_state(
            evaluation_next_action="ask_user",
            evaluation_next_reason_code="relaxation_available",
            pending_question=question(),
        )
        decision = decide_next(state, DEFAULT_POLICY)
        self.assertEqual(decision["action"], "ask_user")
        self.assertEqual(decision["reason_code"], "relaxation_available")

    def test_evaluation_can_finish_even_when_research_remains(self):
        """Listen to C: when evaluate suggests finish, do not force additional searching."""
        state = base_state(
            eligible_count=1,
            search_directive=directive(),
            evaluation_next_action="finish",
            evaluation_next_reason_code="insufficient_candidates",
        )
        decision = decide_next(state, DEFAULT_POLICY)
        self.assertEqual((decision["action"], decision["reason_code"]), ("finish", "insufficient_candidates"))

    def test_evaluation_cannot_override_blocking_review(self):
        state = base_state(
            eligible_count=10,
            review=blocking_review(),
            evaluation_next_action="publish",
            evaluation_next_reason_code="enough_matches",
        )
        self.assertEqual(decide_next(state, DEFAULT_POLICY)["action"], "repair")

    def test_warning_issues_do_not_block_publish(self):
        review = blocking_review()
        review["issues"][0]["severity"] = "warning"
        review["passed"] = True
        self.assertEqual(decide_next(base_state(review=review), DEFAULT_POLICY)["action"], "publish")

    def test_finish_when_no_directive_and_no_question(self):
        decision = decide_next(base_state(eligible_count=0), DEFAULT_POLICY)
        self.assertEqual(
            (decision["action"], decision["reason_code"]), ("finish", "insufficient_candidates")
        )


class BudgetTests(unittest.TestCase):
    def test_exhausted_attempts_stop_research(self):
        state = base_state(
            eligible_count=1, search_attempts_used=3, search_directive=directive()
        )
        decision = decide_next(state, DEFAULT_POLICY)
        self.assertEqual((decision["action"], decision["reason_code"]), ("finish", "budget_exhausted"))

    def test_deadline_forbids_research_and_repair(self):
        researching = base_state(
            eligible_count=1, deadline_exhausted=True, search_directive=directive()
        )
        self.assertEqual(decide_next(researching, DEFAULT_POLICY)["reason_code"], "deadline_exhausted")

        repairing = base_state(deadline_exhausted=True, review=blocking_review())
        decision = decide_next(repairing, DEFAULT_POLICY)
        self.assertEqual((decision["action"], decision["reason_code"]), ("stop", "deadline_exhausted"))

    def test_deadline_stops_even_with_enough_matches(self):
        """C treats the deadline as a hard stop, even if there are already enough valid results."""
        decision = decide_next(base_state(deadline_exhausted=True), DEFAULT_POLICY)
        self.assertEqual((decision["action"], decision["reason_code"]), ("stop", "deadline_exhausted"))

    def test_min_matches_is_a_boundary_not_a_range(self):
        for count, action in ((2, "finish"), (3, "publish"), (4, "publish")):
            with self.subTest(eligible_count=count):
                state = base_state(eligible_count=count)
                self.assertEqual(decide_next(state, DEFAULT_POLICY)["action"], action)


class StaleDirectiveTests(unittest.TestCase):
    def test_directive_from_another_profile_version_is_still_used_by_c(self):
        """C's decide_next does not check the directive's profile version; the graph side will still discard expired directives in prepare_decision."""
        state = base_state(
            eligible_count=1,
            search_directive=directive(base_profile_version=0),
            pending_question=question(),
        )
        decision = decide_next(state, DEFAULT_POLICY)
        self.assertEqual(decision["action"], "research")

    def test_not_started_without_question_or_directive_is_invalid(self):
        state = base_state(search_status="not_started", eligible_count=0, review=None)
        with self.assertRaises(ContractViolation) as caught:
            decide_next(state, DEFAULT_POLICY)
        self.assertEqual(caught.exception.field_path, "state.search_status")

    def test_not_started_with_question_asks_user(self):
        state = base_state(
            search_status="not_started", eligible_count=0, review=None, pending_question=question()
        )
        self.assertEqual(decide_next(state, DEFAULT_POLICY)["action"], "ask_user")


class ValidationTests(unittest.TestCase):
    def test_missing_review_without_failure_stops(self):
        decision = decide_next(base_state(review=None), DEFAULT_POLICY)
        self.assertEqual((decision["action"], decision["reason_code"]), ("stop", "review_missing"))

    def test_passed_review_cannot_carry_blocking_issues(self):
        review = blocking_review()
        review["passed"] = True
        with self.assertRaises(ContractViolation) as caught:
            decide_next(base_state(review=review), DEFAULT_POLICY)
        self.assertEqual(caught.exception.field_path, "state.review.passed")

    def test_booleans_are_not_accepted_for_counts(self):
        with self.assertRaises(ContractViolation) as caught:
            decide_next(base_state(eligible_count=True), DEFAULT_POLICY)
        self.assertEqual(caught.exception.field_path, "state.eligible_count")

    def test_counts_are_not_accepted_for_flags(self):
        with self.assertRaises(ContractViolation) as caught:
            decide_next(base_state(cancelled=1), DEFAULT_POLICY)
        self.assertEqual(caught.exception.field_path, "state.cancelled")

    def test_question_version_must_match_decision_view(self):
        for field, path in (
            ("state_version", "state.pending_question.state_version"),
            ("base_profile_version", "state.pending_question.base_profile_version"),
        ):
            with self.subTest(field=field):
                state = base_state(eligible_count=1, pending_question=question(**{field: 99}))
                with self.assertRaises(ContractViolation) as caught:
                    decide_next(state, DEFAULT_POLICY)
                self.assertEqual(caught.exception.field_path, path)

    def test_proposal_must_require_confirmation(self):
        stale = question()
        stale["proposals"][0]["requires_user_confirmation"] = False
        with self.assertRaises(ContractViolation) as caught:
            decide_next(base_state(eligible_count=1, pending_question=stale), DEFAULT_POLICY)
        self.assertEqual(
            caught.exception.field_path,
            "state.pending_question.proposals[0].requires_user_confirmation",
        )

    def test_unknown_strategy_kind_is_rejected(self):
        bad = directive(strategy_changes=[{"kind": "raise_budget", "field": "max_price"}])
        with self.assertRaises(ContractViolation) as caught:
            decide_next(base_state(eligible_count=1, search_directive=bad), DEFAULT_POLICY)
        self.assertEqual(
            caught.exception.field_path, "state.search_directive.strategy_changes[0].kind"
        )

    def test_empty_strategy_changes_is_rejected(self):
        with self.assertRaises(ContractViolation) as caught:
            decide_next(
                base_state(eligible_count=1, search_directive=directive(strategy_changes=[])),
                DEFAULT_POLICY,
            )
        self.assertEqual(caught.exception.field_path, "state.search_directive.strategy_changes")

    def test_invalid_policy_is_input_error(self):
        for policy, path in (
            ({**DEFAULT_POLICY, "min_matches": 0}, "policy.min_matches"),
            ({**DEFAULT_POLICY, "max_search_attempts": 0}, "policy.max_search_attempts"),
            ({**DEFAULT_POLICY, "display_limit": 2}, "policy.display_limit"),
            ({**DEFAULT_POLICY, "max_repairs": -1}, "policy.max_repairs"),
        ):
            with self.subTest(path=path):
                with self.assertRaises(ContractViolation) as caught:
                    decide_next(base_state(), policy)
                self.assertEqual(caught.exception.code, "INVALID_INPUT")
                self.assertEqual(caught.exception.field_path, path)


if __name__ == "__main__":
    unittest.main()
