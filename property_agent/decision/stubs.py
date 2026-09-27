"""Development-time stand-in: module C is not finished, and upstream B and the profile service are also under parallel development.

The stand-ins are used only for integration and testing. They implement the same Protocol as the real implementations, so when taking over you can simply swap the injected object,
without changing node code. The default behavior is deliberately conservative: structurally valid, factual claims must have evidence citations, but it does not claim to have
real recommendation quality.
"""
from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass, field
from typing import Any

from property_agent.contracts import (
    AttemptSummary,
    Coverage,
    EvaluationResult,
    ListingSnapshot,
    Recommendation,
    RelaxationProposal,
    Result,
    RetrievalResult,
    ReviewResult,
    ReviewIssue,
    RoutingPolicy,
    RunContext,
    ScreenResult,
    SearchDirective,
    ConversationProfile,
)
from property_agent.decision.boundaries import ProfileVersionConflict
from property_agent.profiles import apply_relaxation, read_relaxable_value
from property_agent.results import CallTimer, make_issue


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def _stamp(result: Result, timer: CallTimer) -> Result:
    """Result written in tests usually omits meta; it is filled in here to keep the contract shape complete."""
    if not result.get("meta"):
        result = {**result, "meta": timer.meta()}
    result.setdefault("issues", [])
    return result


@dataclass
class ScriptedModuleC:
    """Stand-in for module C.

    `evaluations` / `reviews` are popped in order; once exhausted, it falls back to the default implementation, so tests can script only
    the call they care about. `*_calls` records the input arguments, so assertions can verify that the repair context was indeed passed down.
    """

    evaluations: list[Result] = field(default_factory=list)
    reviews: list[Result] = field(default_factory=list)
    evaluate_calls: list[dict] = field(default_factory=list)
    review_calls: list[dict] = field(default_factory=list)

    async def evaluate(
        self,
        profile: ConversationProfile,
        retrieval: RetrievalResult,
        screen_result: ScreenResult,
        listing_snapshot: ListingSnapshot,
        coverage: Coverage,
        repair_context: ReviewResult | None,
        *,
        policy: RoutingPolicy,
        ctx: RunContext,
    ) -> Result:
        timer = CallTimer(ctx)
        self.evaluate_calls.append(
            {
                "profile_version": profile["version"],
                "repair_context": repair_context,
                "eligible": [item["listing_key"] for item in screen_result["eligible"]],
            }
        )
        if self.evaluations:
            return _stamp(self.evaluations.pop(0), timer)
        return timer.ok(
            _default_evaluation(profile, screen_result, listing_snapshot, coverage, policy)
        )

    async def review(
        self,
        profile: ConversationProfile,
        evaluation: EvaluationResult,
        listing_snapshot: ListingSnapshot,
        *,
        policy: RoutingPolicy,
        ctx: RunContext,
    ) -> Result:
        timer = CallTimer(ctx)
        self.review_calls.append({"snapshot_id": evaluation["snapshot_id"]})
        if self.reviews:
            return _stamp(self.reviews.pop(0), timer)
        return timer.ok(_default_review(evaluation, listing_snapshot, policy))


def _price_of(listing: dict) -> int | None:
    price = listing.get("price") or {}
    return price.get("amount") if price.get("status") == "known" else None


def _default_evaluation(
    profile: ConversationProfile,
    screen_result: ScreenResult,
    listing_snapshot: ListingSnapshot,
    coverage: Coverage,
    policy: RoutingPolicy,
) -> EvaluationResult:
    """Listed in the original eligible order, each entry gives only one price fact with evidence."""
    by_key = {item["listing_key"]: item for item in listing_snapshot["items"]}
    ordered = []
    for rank, screened in enumerate(screen_result["eligible"], start=1):
        key = screened["listing_key"]
        listing = by_key.get(key, {})
        price = listing.get("price") or {}
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

    recommendation: Recommendation = {
        "ordered_items": ordered,
        "summary": f"A total of {len(ordered)} qualified candidates this time, ordered by the evaluation module.",
        "limitations": ["Generated by a development stand-in; covers only this query; source status does not equal independent verification"],
    }

    findings = [
        f"{item['listing_key']}: {check['field']} {check['status']}"
        for item in screen_result.get("rejected", [])
        for check in item.get("checks", [])
        if check.get("status") == "fail"
    ]

    short = len(ordered) < policy["min_matches"]
    directive = _suggest_directive(profile, coverage) if short else None
    proposals = (
        _suggest_price_relaxation(profile, screen_result, listing_snapshot) if short else []
    )
    if not short:
        next_action, next_reason = "publish", "enough_matches"
    elif directive is not None:
        next_action, next_reason = "research", directive["reason_code"]
    elif proposals:
        next_action, next_reason = "ask_user", "relaxation_available"
    else:
        next_action, next_reason = "finish", "insufficient_candidates"
    return {
        "profile_version": profile["version"],
        "snapshot_id": listing_snapshot["snapshot_id"],
        "recommendation": recommendation,
        "assessment": {
            "constraint_findings": findings,
            "search_directive": directive,
            "relaxation_proposals": proposals,
            "next_action": next_action,
            "next_reason_code": next_reason,
        },
    }


def _suggest_directive(profile: ConversationProfile, coverage: Coverage) -> SearchDirective | None:
    """Only suggest changing the search method: if there is a next page, turn the page; hard constraints remain unchanged."""
    next_pages = coverage.get("next_pages") or []
    if not next_pages:
        return None
    return {
        "reason_code": "insufficient_candidates",
        "strategy_changes": [dict(page) for page in next_pages[:1]],
        "base_profile_version": profile["version"],
        "evidence_listing_keys": [],
    }


def _suggest_price_relaxation(
    profile: ConversationProfile, screen_result: ScreenResult, listing_snapshot: ListingSnapshot
) -> list[RelaxationProposal]:
    """Propose a proposal based on the records filtered out by price this round; it still requires explicit user confirmation to take effect."""
    try:
        current = read_relaxable_value(profile, "listing_constraints.price.amount")
    except KeyError:
        return []
    if not isinstance(current, int):
        return []
    by_key = {item["listing_key"]: item for item in listing_snapshot["items"]}
    over_budget = []
    for screened in screen_result.get("rejected", []):
        if not any(
            check.get("field") == "price.amount" and check.get("status") == "fail"
            for check in screened.get("checks", [])
        ):
            continue
        amount = _price_of(by_key.get(screened["listing_key"], {}))
        if isinstance(amount, int) and amount > current:
            over_budget.append((amount, screened["listing_key"]))
    if not over_budget:
        return []
    amount, key = min(over_budget)
    return [
        {
            "proposal_id": f"relax-price-{amount}",
            "field": "listing_constraints.price.amount",
            "old_value": current,
            "proposed_value": amount,
            "reason": f"The {key} rent excluded this round is {amount}; raising it to that value may broaden matches, but a new query is still required.",
            "evidence_listing_keys": [key],
            "requires_user_confirmation": True,
        }
    ]


def _default_review(
    evaluation: EvaluationResult, listing_snapshot: ListingSnapshot, policy: RoutingPolicy
) -> ReviewResult:
    """The stand-in performs only deterministic structural and citation validation, not semantic Reflection."""
    known_keys = {item["listing_key"] for item in listing_snapshot["items"]}
    known_evidence = {
        evidence["evidence_id"]
        for item in listing_snapshot["items"]
        for evidence in item.get("evidence", [])
    }
    items = evaluation["recommendation"]["ordered_items"]
    issues: list[ReviewIssue] = []

    for index, item in enumerate(items):
        base = f"recommendation.ordered_items[{index}]"
        if item["listing_key"] not in known_keys:
            issues.append(
                {
                    "code": "UNKNOWN_LISTING",
                    "listing_key": item["listing_key"],
                    "field_path": f"{base}.listing_key",
                    "message": "The recommendation cites a listing outside this round's snapshot.",
                    "severity": "blocking",
                    "suggested_fix": "Cite only candidate evidence from this run.",
                }
            )
        for claim_field in ("reasons", "tradeoffs"):
            for claim_index, claim in enumerate(item.get(claim_field, [])):
                if claim.get("kind") != "fact":
                    continue
                ids = claim.get("evidence_ids") or []
                if not ids or any(evidence_id not in known_evidence for evidence_id in ids):
                    issues.append(
                        {
                            "code": "UNSUPPORTED_CLAIM",
                            "listing_key": item["listing_key"],
                            "field_path": f"{base}.{claim_field}[{claim_index}]",
                            "message": "A factual statement lacks a parseable evidence citation.",
                            "severity": "blocking",
                            "suggested_fix": "Delete the fact or add real evidence and review again.",
                        }
                    )

    if [item.get("rank") for item in items] != list(range(1, len(items) + 1)):
        issues.append(
            {
                "code": "INVALID_RANK",
                "listing_key": None,
                "field_path": "recommendation.ordered_items",
                "message": "The ranks are not consecutive integers starting from 1.",
                "severity": "blocking",
                "suggested_fix": "Re-rank while preserving the relative order given by the model.",
            }
        )

    if len(items) > policy["display_limit"]:
        issues.append(
            {
                "code": "TOO_MANY_ITEMS",
                "listing_key": None,
                "field_path": "recommendation.ordered_items",
                "message": "The number of recommendations exceeds the display limit.",
                "severity": "warning",
                "suggested_fix": "Truncate on the publishing side according to the display limit.",
            }
        )

    blocking = [issue for issue in issues if issue["severity"] == "blocking"]
    return {"passed": not blocking, "issues": issues}


@dataclass
class ScriptedSearchRunner:
    """Stand-in on the module B side. Unscripted calls return an explicit error instead of silently fabricating data."""

    outcomes: list[Result] = field(default_factory=list)
    calls: list[SearchDirective] = field(default_factory=list)
    histories: list[list[AttemptSummary]] = field(default_factory=list)

    async def run_attempt(
        self,
        directive: SearchDirective,
        profile: ConversationProfile,
        *,
        previous_attempts: list[AttemptSummary],
        ctx: RunContext,
    ) -> Result:
        timer = CallTimer(ctx)
        self.calls.append(directive)
        self.histories.append(copy.deepcopy(previous_attempts))
        if not self.outcomes:
            return timer.error(
                make_issue(
                    "SOURCE_UNAVAILABLE",
                    "The search stand-in has no results prepared for this supplementary search; it does not fall back to simulated data.",
                    source="stub",
                )
            )
        return _stamp(self.outcomes.pop(0), timer)


@dataclass
class InMemoryProfileWriter:
    """Stand-in for the profile service: optimistic locking + idempotency by op_key."""

    profiles: dict[str, ConversationProfile] = field(default_factory=dict)
    _applied: dict[str, ConversationProfile] = field(default_factory=dict)

    def put(self, profile: ConversationProfile, *, user_id: str | None = None) -> None:
        if user_id is not None and profile["user_id"] != user_id:
            raise PermissionError("profile.user_id does not match the current user")
        self.profiles[profile["profile_id"]] = copy.deepcopy(profile)

    def get(self, profile_id: str) -> ConversationProfile | None:
        value = self.profiles.get(profile_id)
        return copy.deepcopy(value) if value is not None else None

    def current_version(self, profile_id: str) -> int:
        return self.profiles[profile_id]["version"]

    def apply_relaxation(
        self,
        profile_id: str,
        *,
        base_version: int,
        proposal: RelaxationProposal,
        source_message_id: str,
        op_key: str,
    ) -> ConversationProfile:
        if op_key in self._applied:
            return self._applied[op_key]
        current = self.profiles[profile_id]
        if current["version"] != base_version:
            raise ProfileVersionConflict(f"The profile is already at v{current['version']}, but the proposal is based on v{base_version}")

        updated = apply_relaxation(
            current, proposal=proposal, source_message_id=source_message_id
        )
        self.profiles[profile_id] = updated
        self._applied[op_key] = updated
        return updated


@dataclass
class InMemoryRecommendationRepository:
    """Stand-in for recommendation delivery. Repeated submission with the same op_key returns the original result without inserting again."""

    saved: dict[str, Recommendation] = field(default_factory=dict)
    _by_op: dict[str, str] = field(default_factory=dict)

    def save(
        self, run_id: str, recommendation: Recommendation, *, op_key: str
    ) -> tuple[str, bool]:
        if op_key in self._by_op:
            return self._by_op[op_key], True
        final_result_id = f"rec-{run_id}-{_short_hash(op_key)}"
        self.saved[final_result_id] = recommendation
        self._by_op[op_key] = final_result_id
        return final_result_id, False


@dataclass
class InMemoryRunRepository:
    runs: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_conversation: dict[str, list[str]] = field(default_factory=dict)

    def prepare_run(self, *, ctx: RunContext, profile: ConversationProfile) -> None:
        run_id = ctx["run_id"]
        self.runs[run_id] = {
            "run_id": run_id,
            "status": "running",
            "conversation_id": ctx["conversation_id"],
            "user_id": ctx["user_id"],
            "profile_id": profile["profile_id"],
            "profile_version": profile["version"],
            "profile_snapshot": copy.deepcopy(profile),
            "ctx": copy.deepcopy(ctx),
        }
        history = self.by_conversation.setdefault(ctx["conversation_id"], [])
        if run_id not in history:
            history.append(run_id)

    def update(
        self,
        run_id: str,
        *,
        status: str,
        state_version: int | None = None,
        completion_reason: str | None = None,
        final_result_id: str | None = None,
    ) -> None:
        current = self.runs.setdefault(run_id, {"run_id": run_id})
        current.update(
            {
                "status": status,
                "completion_reason": completion_reason,
            }
        )
        if state_version is not None:
            current["state_version"] = state_version
        if final_result_id is not None:
            current["final_result_id"] = final_result_id

    def find_waiting_run(
        self, conversation_id: str, *, user_id: str
    ) -> dict[str, Any] | None:
        for run_id in reversed(self.by_conversation.get(conversation_id, [])):
            run = self.runs.get(run_id) or {}
            if run.get("status") == "waiting_user" and run.get("user_id") == user_id:
                return copy.deepcopy(run)
        return None


@dataclass
class InMemoryQuestionRepository:
    """Stand-in for question and answer consumption. Idempotent by question_id; an old answer can only be consumed repeatedly beyond one rejection."""

    questions: dict[str, dict] = field(default_factory=dict)
    answered: set[str] = field(default_factory=set)
    answers: dict[str, dict[str, str | None]] = field(default_factory=dict)

    def save_question(self, run_id: str, question: dict) -> dict:
        key = f"{run_id}:{question['question_id']}"
        return self.questions.setdefault(key, question)

    def mark_answered(
        self,
        run_id: str,
        question_id: str,
        *,
        client_message_id: str | None = None,
        answer_text: str | None = None,
    ) -> bool:
        key = f"{run_id}:{question_id}"
        if key not in self.questions:
            return False
        if key in self.answered:
            return bool(
                client_message_id
                and self.answers.get(key, {}).get("client_message_id")
                == client_message_id
                and self.answers.get(key, {}).get("answer_text") == answer_text
            )
        self.answered.add(key)
        self.answers[key] = {
            "client_message_id": client_message_id,
            "answer_text": answer_text,
        }
        return True
