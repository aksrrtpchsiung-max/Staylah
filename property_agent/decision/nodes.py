"""Node adapters: take input from state -> call business functions -> validate results -> return state updates.

Responsibility boundaries:
- Module C decides the recommended content and order; this layer only performs deterministic checks on structure, references, counts, and budget.
- `decide_next` is the sole routing decision point; nodes do not each "incidentally" decide the next step.
- A concession proposal is not written to the profile until the user explicitly accepts it; after acceptance, the old run yields superseded, and the run control layer starts a new run.
"""
from __future__ import annotations

import hashlib
from typing import Any

from langgraph.graph import END
from langgraph.types import interrupt

from property_agent.contracts import (
    ContractViolation,
    EvaluationResult,
    Issue,
    PendingQuestion,
    ReviewIssue,
    ReviewResult,
)
from property_agent.decision.boundaries import ProfileVersionConflict
from property_agent.decision.decide import decide_next
from property_agent.decision.deps import DecisionDeps
from property_agent.decision.guards import (
    prepare_for_display,
    sanitize_directive,
    sanitize_proposals,
)
from property_agent.decision.state import (
    SCHEMA_VERSION,
    DState,
    build_decision_state,
    count_eligible,
    eligible_keys,
)
from property_agent.evaluation_trace import record_event, stage_span
from property_agent.results import first_issue_code, is_usable, make_issue

ANSWER_ACTIONS = ("answer", "accept_proposal", "decline", "cancel")

# Mapping from stop reason codes to run terminal states. Only system failures count as failed.
STOP_STATUS = {
    "cancelled": "cancelled",
    "profile_superseded": "superseded",
}


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def _program_review(issues: list[ReviewIssue]) -> ReviewResult:
    """Structural problems detected on the program side are handed to routing in the ReviewResult shape, sharing the same repair budget.

    This does not replace Module C's semantic Reflection; it only covers the deterministic part of "structure and references".
    """
    return {"passed": False, "issues": issues}


def _validate_evaluation(evaluation: Any, state: DState) -> list[ReviewIssue]:
    snapshot = state["listing_snapshot"] or {}
    issues: list[ReviewIssue] = []
    if evaluation.get("profile_version") != state["profile_version"]:
        issues.append(
            {
                "code": "HARD_CONSTRAINT_VIOLATION",
                "listing_key": None,
                "field_path": "profile_version",
                "message": "The evaluation result is attached to a different requirement version.",
                "severity": "blocking",
                "suggested_fix": "Re-evaluate using the requirement version pinned for this run.",
            }
        )
    if evaluation.get("snapshot_id") != snapshot.get("snapshot_id"):
        issues.append(
            {
                "code": "UNKNOWN_LISTING",
                "listing_key": None,
                "field_path": "snapshot_id",
                "message": "The evaluation result references a candidate snapshot outside this round.",
                "severity": "blocking",
                "suggested_fix": "Only evaluate the candidate evidence saved for this run.",
            }
        )
    return issues


class DecisionNodes:
    """Bind dependencies to a set of nodes. Graph assembly only looks at the signatures of these methods."""

    def __init__(self, deps: DecisionDeps) -> None:
        self.deps = deps

    # --- Module C's two invocations ---------------------------------------------------

    async def evaluate_candidates(self, state: DState) -> dict:
        with stage_span("C", "evaluate", attempt_id=state.get("attempt_id")):
            result = await self.deps.module_c.evaluate(
                state["profile_snapshot"],
                state["retrieval_result"],
                state["screen_result"],
                state["listing_snapshot"],
                state["coverage"],
                state.get("repair_context"),
                policy=state["policy"],
                ctx=state["ctx"],
            )
        record_event("c_evaluation", result, attempt_id=state.get("attempt_id"))
        if not is_usable(result):
            # The evaluation service could not complete: we must not pretend there are "no matching listings".
            return {
                "evaluation": None,
                "review": None,
                "failure_code": first_issue_code(result),
                "last_issues": result["issues"],
            }

        evaluation: EvaluationResult = result["data"]
        structural = _validate_evaluation(evaluation, state)
        if structural:
            # Content issues go through the repair budget rather than terminating directly.
            return {
                "evaluation": evaluation,
                "review": _program_review(structural),
                "repair_context": None,
                "failure_code": None,
                "last_issues": result["issues"],
            }
        return {
            "evaluation": evaluation,
            "review": None,
            # The repair context has been consumed by this invocation; if problems arise again next round, they will be provided by a new review result.
            "repair_context": None,
            "failure_code": None,
            "last_issues": result["issues"],
        }

    async def review_recommendation(self, state: DState) -> dict:
        with stage_span("C", "review", attempt_id=state.get("attempt_id")):
            result = await self.deps.module_c.review(
                state["profile_snapshot"],
                state["evaluation"],
                state["listing_snapshot"],
                policy=state["policy"],
                ctx=state["ctx"],
            )
        record_event("c_review", result, attempt_id=state.get("attempt_id"))
        if not is_usable(result):
            # Review could not complete != review passed.
            return {
                "review": None,
                "failure_code": first_issue_code(result),
                "last_issues": result["issues"],
            }
        return {
            # part_c.review will directly correct the draft when it finds excessive, exaggerated, or unsupported copy.
            # Explicitly write back evaluation to ensure the corrected content enters the checkpoint and is handed to A.
            "evaluation": state["evaluation"],
            "review": result["data"],
            "failure_code": None,
            "last_issues": result["issues"],
        }

    # --- Program preparation before decision ----------------------------------------------------

    async def prepare_decision(self, state: DState) -> dict:
        """Clean C's next-step suggestions, count by B candidates, and pre-allocate question IDs."""
        profile = state["profile_snapshot"]
        next_version = state.get("state_version", 0) + 1
        issues: list[Issue] = list(state.get("last_issues") or [])

        assessment = (state.get("evaluation") or {}).get("assessment") or {}
        directive = None
        proposals: list[Any] = []
        settled = state.get("cancelled") or state.get("user_declined")
        eligible_count = count_eligible(state.get("screen_result"))
        if not settled:
            directive, directive_issues = sanitize_directive(
                assessment.get("search_directive"),
                profile_version=state["profile_version"],
                allowed_sources=self.deps.allowed_sources,
            )
            proposals, proposal_issues = sanitize_proposals(
                assessment.get("relaxation_proposals"), profile
            )
            issues += directive_issues + proposal_issues

        next_action = assessment.get("next_action")
        next_reason = assessment.get("next_reason_code")

        question = (
            self._build_question(state, proposals, next_version, eligible_count)
            if proposals and not settled and next_action != "finish"
            else None
        )
        if question is not None:
            question = await self.deps.clarification.prepare_question(question)
        return {
            "schema_version": SCHEMA_VERSION,
            # Re-read the current profile version before publishing: the user may have changed requirements during the wait.
            "current_profile_version": self.deps.profiles.current_version(state["profile_id"]),
            "eligible_count": eligible_count,
            "search_directive": directive,
            "relaxation_proposals": proposals,
            "pending_question": question,
            "evaluation_next_action": next_action,
            "evaluation_next_reason_code": next_reason,
            "state_version": next_version,
            "last_issues": issues,
        }

    def _build_question(
        self,
        state: DState,
        proposals: list[Any],
        state_version: int,
        eligible_count: int,
    ) -> PendingQuestion:
        """Question IDs are pre-allocated by the orchestrator to ensure node reruns do not ask the same question twice."""
        fingerprint = _short_hash(",".join(p["proposal_id"] for p in proposals))
        lines = [
            f"- Change {p['field']} from {p['old_value']} to {p['proposed_value']}: {p['reason']}"
            for p in proposals
        ]
        if eligible_count <= 0:
            lead = "No listings in this search meet all current requirements. "
        else:
            lead = f"This search found {eligible_count} listings meeting your requirements, fewer than requested. "
        return {
            "question_id": f"{state['run_id']}:state-{state_version}:relax-{fingerprint}",
            "text": lead + "Would you accept any of the following changes? You can also end this search.\n" + "\n".join(lines),
            "reason_code": "insufficient_candidates",
            "proposals": proposals,
            "allowed_actions": list(ANSWER_ACTIONS),
            "base_profile_version": state["profile_version"],
            "state_version": state_version,
        }

    def route(self, state: DState) -> dict:
        try:
            with stage_span("C", "decide_next", attempt_id=state.get("attempt_id")):
                decision = decide_next(build_decision_state(state), state["policy"])
            record_event("c_route_decision", decision, attempt_id=state.get("attempt_id"))
        except ContractViolation as exc:
            # A contract error in a pure function is an orchestration-layer bug; convert it to a system error and log it, without disguising it as a business result.
            self.deps.runs.update(
                state["run_id"],
                status="failed",
                state_version=state.get("state_version"),
                completion_reason="internal_error",
            )
            return {
                "decision": None,
                "status": "failed",
                "completion_reason": "internal_error",
                "last_issues": [
                    *(state.get("last_issues") or []),
                    make_issue("INTERNAL_ERROR", str(exc), field_path=exc.field_path),
                ],
            }
        return {"decision": decision}

    # --- Six actions ------------------------------------------------------------

    def publish(self, state: DState) -> dict:
        return self._deliver(state, partial=False, completion_reason="published")

    def repair(self, state: DState) -> dict:
        """Hand the specific review issues back to the evaluation module; the repair quota is deducted only once."""
        return {
            "repairs_used": state.get("repairs_used", 0) + 1,
            "repair_context": state["review"],
            "evaluation": None,
            "review": None,
        }

    async def research(self, state: DState) -> dict:
        """Supplementary search is executed upstream; here we only hand over instructions that preserve hard constraints and accumulate the attempt count."""
        directive = state["decision"]["search_directive"]
        result = await self.deps.search_runner.run_attempt(
            directive,
            state["profile_snapshot"],
            previous_attempts=state.get("previous_attempts", []),
            ctx=state["ctx"],
        )
        # Count as one attempt regardless of success or failure, to prevent failed retries from bypassing the budget.
        used = state.get("search_attempts_used", 0) + 1
        cleared = {
            "search_attempts_used": used,
            "evaluation": None,
            "review": None,
            "repair_context": None,
            "search_directive": None,
            "last_issues": result["issues"],
        }
        if not is_usable(result):
            history = list(state.get("previous_attempts", []))
            failed_attempt_id = f"{state['run_id']}:attempt:{used:03d}"
            if not any(
                item["attempt_id"] == failed_attempt_id for item in history
            ):
                history.append(
                    {
                        "attempt_id": failed_attempt_id,
                        "query_fingerprints": [],
                        "status": "error",
                        "eligible_count": 0,
                    }
                )
            return {
                **cleared,
                "attempt_id": failed_attempt_id,
                "search_status": "error",
                "failure_code": first_issue_code(result),
                "previous_attempts": history,
            }
        outcome = result["data"]
        history = list(state.get("previous_attempts", []))
        summary = outcome.get("attempt_summary")
        if summary is not None:
            matching = [
                item for item in history if item["attempt_id"] == summary["attempt_id"]
            ]
            if matching and matching[0] != summary:
                return {
                    **cleared,
                    "search_status": "error",
                    "failure_code": "STATE_CONFLICT",
                    "last_issues": [
                        make_issue(
                            "STATE_CONFLICT",
                            "The same attempt_id corresponds to different search summaries.",
                            field_path="previous_attempts",
                        )
                    ],
                }
            if not matching:
                history.append(summary)
        return {
            **cleared,
            "attempt_id": outcome["attempt_id"],
            "search_status": outcome["search_status"],
            "failure_code": outcome.get("failure_code"),
            "listing_snapshot": outcome["listing_snapshot"],
            "screen_result": outcome["screen_result"],
            "retrieval_result": outcome["retrieval_result"],
            "coverage": outcome["coverage"],
            "requirement_coverage": outcome.get("requirement_coverage"),
            "previous_attempts": history,
        }

    def ask_user(self, state: DState) -> dict:
        """Enter the wait only after the question is saved; saving is idempotent by question_id."""
        question = state["decision"]["pending_question"]
        saved = self.deps.questions.save_question(state["run_id"], question)
        self.deps.runs.update(
            state["run_id"],
            status="waiting_user",
            state_version=state.get("state_version"),
        )
        return {"pending_question": saved, "status": "waiting_user"}

    def wait_for_user(self, state: DState) -> dict:
        """Before interrupt, do not write messages, modify the profile, or deduct quota -- this node will execute to this point again on rerun."""
        answer = interrupt({"pending_question": state["pending_question"]})
        return {"pending_answer": answer, "answer_rejected": False}

    async def apply_answer(self, state: DState) -> dict:
        question = state["pending_question"]
        raw_answer = state.get("pending_answer")
        if isinstance(raw_answer, str):
            answer = await self.deps.clarification.parse_answer(
                text=raw_answer,
                question=question,
                client_message_id=None,
            )
        elif isinstance(raw_answer, dict) and not raw_answer.get("action"):
            answer = await self.deps.clarification.parse_answer(
                text=str(raw_answer.get("text") or raw_answer.get("answer") or ""),
                question=question,
                client_message_id=raw_answer.get("client_message_id"),
            )
        else:
            answer = raw_answer or {}

        if answer.get("question_id") != question["question_id"]:
            return self._reject_answer("The question the answer corresponds to is not the current pending question.", "answer.question_id")
        if answer.get("expected_state_version") != question["state_version"]:
            return self._reject_answer(
                "The state version carried by the answer has expired.", "answer.expected_state_version"
            )
        action = answer.get("action")
        if action not in question["allowed_actions"]:
            return self._reject_answer(f"Action {action!r} is not within the allowed range.", "answer.action")
        answer_text = answer.get("answer") or answer.get("text") or str(action)
        client_message_id = (
            answer.get("client_message_id") or f"{question['question_id']}:answer"
        )
        if not self.deps.questions.mark_answered(
            state["run_id"],
            question["question_id"],
            client_message_id=client_message_id,
            answer_text=answer_text,
        ):
            return self._reject_answer("This question has already been answered.", "answer.question_id")

        consumed = {"pending_question": None, "pending_answer": None, "answer_rejected": False}
        if action == "cancel":
            return {**consumed, "cancelled": True}
        if action == "decline":
            return {**consumed, "user_declined": True}
        if action == "accept_proposal":
            return self._accept_proposal(state, question, answer)
        return self._hand_to_onboarding(state, answer)

    def _reject_answer(self, message: str, field_path: str) -> dict:
        """Old answers do not overwrite new state: record the conflict and continue waiting for a valid answer."""
        return {
            "pending_answer": None,
            "answer_rejected": True,
            "last_issues": [make_issue("STATE_CONFLICT", message, field_path=field_path)],
        }

    def _accept_proposal(self, state: DState, question: dict, answer: dict) -> dict:
        proposal_id = answer.get("proposal_id")
        # New values are always read from the question saved on the server; the frontend can only submit proposal_id.
        proposal = next(
            (p for p in question["proposals"] if p["proposal_id"] == proposal_id), None
        )
        if proposal is None:
            return self._reject_answer("The proposal does not belong to the current question.", "answer.proposal_id")

        message_id = answer.get("client_message_id") or f"{question['question_id']}:accept"
        try:
            profile = self.deps.profiles.apply_relaxation(
                state["profile_id"],
                base_version=state["profile_version"],
                proposal=proposal,
                source_message_id=message_id,
                op_key=f"{state['run_id']}:{question['question_id']}:{proposal_id}",
            )
        except ProfileVersionConflict as exc:
            self.deps.runs.update(
                state["run_id"],
                status="superseded",
                state_version=state.get("state_version"),
                completion_reason="profile_superseded",
            )
            return {
                "pending_question": None,
                "pending_answer": None,
                "answer_rejected": False,
                "status": "superseded",
                "completion_reason": "profile_superseded",
                "last_issues": [
                    make_issue("STATE_CONFLICT", str(exc), field_path="profile.version")
                ],
            }

        # The requirement has indeed changed: this run ends here, and the run control layer starts a new run based on the new version.
        self.deps.runs.update(
            state["run_id"],
            status="superseded",
            state_version=state.get("state_version"),
            completion_reason="profile_updated",
        )
        return {
            "profile_snapshot": profile,
            "current_profile_version": profile["version"],
            "pending_question": None,
            "pending_answer": None,
            "answer_rejected": False,
            "status": "superseded",
            "completion_reason": "profile_updated",
            "next_run_request": {
                "reason_code": "relaxation_accepted",
                "profile_id": state["profile_id"],
                "profile_version": profile["version"],
                "superseded_run_id": state["run_id"],
                "accepted_proposal_id": proposal_id,
                "source_message_id": message_id,
            },
        }

    def _hand_to_onboarding(self, state: DState, answer: dict) -> dict:
        """Free text may be a new requirement expression; hand it back to the requirement parsing stage, and do not guess hard constraints here."""
        self.deps.runs.update(
            state["run_id"],
            status="superseded",
            state_version=state.get("state_version"),
            completion_reason="handed_to_onboarding",
        )
        return {
            "pending_question": None,
            "pending_answer": None,
            "answer_rejected": False,
            "status": "superseded",
            "completion_reason": "handed_to_onboarding",
            "next_run_request": self.deps.onboarding_handoff.build_request(
                profile_id=state["profile_id"],
                profile_version=state["profile_version"],
                superseded_run_id=state["run_id"],
                source_message_id=answer.get("client_message_id"),
            ),
        }

    def finish_run(self, state: DState) -> dict:
        """finish does not equal "found enough listings": if there are valid results, deliver them as partial results."""
        reason = state["decision"]["reason_code"]
        evaluation = state.get("evaluation")
        review = state.get("review")
        has_results = bool(
            evaluation
            and review
            and review.get("passed")
            and evaluation["recommendation"]["ordered_items"]
        )
        if has_results:
            return self._deliver(state, partial=True, completion_reason=reason)
        self.deps.runs.update(
            state["run_id"],
            status="completed",
            state_version=state.get("state_version"),
            completion_reason=reason,
        )
        return {
            "status": "completed",
            "completion_reason": reason,
            "pending_question": None,
        }

    def stop_run(self, state: DState) -> dict:
        reason = state["decision"]["reason_code"]
        status = STOP_STATUS.get(reason, "failed")
        self.deps.runs.update(
            state["run_id"],
            status=status,
            state_version=state.get("state_version"),
            completion_reason=reason,
        )
        return {
            "status": status,
            "completion_reason": reason,
            "pending_question": None,
        }

    def _deliver(self, state: DState, *, partial: bool, completion_reason: str) -> dict:
        evaluation = state["evaluation"]
        recommendation, issues = prepare_for_display(
            evaluation["recommendation"],
            display_limit=state["policy"]["display_limit"],
            eligible_keys=eligible_keys(state.get("screen_result")),
        )
        if issues:
            # The final check before publishing did not pass: fail explicitly, and do not publish the question draft.
            self.deps.runs.update(
                state["run_id"],
                status="failed",
                state_version=state.get("state_version"),
                completion_reason="publish_validation_failed",
            )
            return {
                "status": "failed",
                "completion_reason": "publish_validation_failed",
                "last_issues": [*(state.get("last_issues") or []), *issues],
            }

        op_key = f"{state['run_id']}:publish:{evaluation['snapshot_id']}:{state['profile_version']}"
        final_result_id, _replayed = self.deps.recommendations.save(
            state["run_id"], recommendation, op_key=op_key
        )
        snapshot = state.get("listing_snapshot") or {}
        by_key = {item["listing_key"]: item for item in snapshot.get("items") or []}
        record_event("final_recommendation", {
            "recommendation": recommendation,
            "ordered_listings": [
                {"rank": item["rank"], "listing_key": item["listing_key"],
                 "listing": by_key.get(item["listing_key"])}
                for item in recommendation.get("ordered_items") or []
            ],
            "snapshot_id": snapshot.get("snapshot_id"),
            "is_partial": partial,
        }, attempt_id=state.get("attempt_id"))
        self.deps.runs.update(
            state["run_id"],
            status="completed",
            state_version=state.get("state_version"),
            completion_reason=completion_reason,
            final_result_id=final_result_id,
        )
        return {
            "final_result_id": final_result_id,
            "published_recommendation": recommendation,
            "delivery_is_partial": partial,
            "status": "completed",
            "completion_reason": completion_reason,
            "pending_question": None,
        }


# --- Conditional edges -----------------------------------------------------------------


def route_entry(state: DState) -> str:
    """If all sources have already failed, or upstream provided no candidates, there is no need to bother the evaluation module."""
    if state.get("search_status") == "error" or not state.get("listing_snapshot"):
        return "prepare_decision"
    return "evaluate_candidates"


def route_after_evaluate(state: DState) -> str:
    if state.get("failure_code") or state.get("review") is not None:
        return "prepare_decision"
    return "review_recommendation"


def route_after_research(state: DState) -> str:
    if state.get("search_status") == "error" or not state.get("listing_snapshot"):
        return "prepare_decision"
    return "evaluate_candidates"


def route_decision(state: DState) -> str:
    decision = state.get("decision")
    if decision is None:
        return END
    return {
        "publish": "publish",
        "repair": "repair",
        "research": "research",
        "ask_user": "ask_user",
        "finish": "finish_run",
        "stop": "stop_run",
    }[decision["action"]]


def route_after_answer(state: DState) -> str:
    if state.get("answer_rejected"):
        # An old answer or duplicate answer was rejected; continue waiting for a valid answer.
        return "wait_for_user"
    if state.get("status") == "superseded":
        return END
    return "prepare_decision"
