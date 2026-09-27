"""Attempts responsibilities extracted without changing behavior."""
from __future__ import annotations
import copy
from collections.abc import Sequence
from typing import Any
from property_agent.evaluation import service as part_c
from property_agent.search.execution.history import query_fingerprint
from property_agent.contracts import AttemptSummary, ContractViolation, ConversationProfile, Issue, QueryFeatures, RequirementCoverage, RequirementFulfillment, RequirementRequest, Result, RunContext, ScreenResult, SearchPlan, SearchResult
from property_agent.decision.boundaries import AttemptOutcome
from property_agent.evaluation_trace import record_event, stage_span
from property_agent.results import CallTimer, is_usable, make_issue
from property_agent.integration.boundaries import BCTransition, RetrieveFunction


def _copy_error(result: Result, timer: CallTimer) -> Result:
    issues = copy.deepcopy(result.get("issues") or [])
    if not issues:
        issues = [make_issue("INTERNAL_ERROR", "upstream returned an error but did not explain the reason")]
    return timer.error(*issues)


def _failed_attempt(
    timer: CallTimer,
    ctx: RunContext,
    issues: Sequence[Issue],
    *,
    plan: SearchPlan | None = None,
) -> Result:
    """Save a completed supplementary search failure as a checkpointable attempt outcome."""

    problems = copy.deepcopy(list(issues))
    if not problems:
        problems = [make_issue("INTERNAL_ERROR", "supplementary search failed but did not explain the reason")]
    attempt_id = ctx["attempt_id"]
    if attempt_id is None:
        return timer.error(*problems)
    summary: AttemptSummary = {
        "attempt_id": attempt_id,
        "query_fingerprints": (
            [query_fingerprint(plan, item) for item in plan["queries"]]
            if plan is not None
            else []
        ),
        "status": "error",
        "eligible_count": 0,
    }
    outcome: AttemptOutcome = {
        "attempt_id": attempt_id,
        "search_status": "error",
        "failure_code": problems[0]["code"],
        "listing_snapshot": None,
        "screen_result": None,
        "retrieval_result": None,
        "coverage": None,
        "requirement_coverage": None,
        "attempt_summary": summary,
    }
    return timer.partial(outcome, problems)


def _requirement_request(profile: ConversationProfile) -> RequirementRequest:
    """Rebuild the minimal request needed for investigation B from the confirmed profile fixed for this run."""

    if (
        profile["status"] != "confirmed"
        or profile["confirmed_version"] != profile["version"]
        or profile["confirmed_at"] is None
        or profile["intent"] is None
    ):
        raise ContractViolation(
            "INVALID_STATE", "profile", "supplementary search can only use the currently confirmed profile"
        )
    return {
        "request_id": (
            f"requirement-{profile['conversation_id']}-v{profile['version']}"
        ),
        "schema_version": "0.3-draft",
        "conversation_id": profile["conversation_id"],
        "profile_id": profile["profile_id"],
        "profile_version": profile["version"],
        "intent": profile["intent"],
        "user_context": copy.deepcopy(profile["user_context"]),
        "listing_constraints": copy.deepcopy(profile["listing_constraints"]),
        "derived_data_requirements": copy.deepcopy(
            profile["derived_data_requirements"]
        ),
        "open_data_requirements": copy.deepcopy(profile["open_data_requirements"]),
        "unresolved_fields": list(profile["unresolved"]),
        "confirmed_at": profile["confirmed_at"],
    }


class BCAttemptAdapter:
    """Hand the candidates returned by B directly to C.retrieve, and construct a Decision attempt."""

    def __init__(
        self,
        *,
        retrieve: RetrieveFunction = part_c.retrieve,
        top_k: int = 12,
    ) -> None:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        self._retrieve = retrieve
        # B -> C hands over at most 12 sets; retrieve is only responsible for scoring and ranking this batch of candidates.
        self._top_k = min(top_k, part_c.MAX_EVALUATION_CANDIDATES)

    async def adapt(
        self,
        search_result: Result,
        *,
        profile: ConversationProfile,
        query: QueryFeatures,
        plan: SearchPlan,
        ctx: RunContext,
        requirement_coverage: RequirementCoverage | None = None,
        additional_issues: Sequence[Issue] = (),
    ) -> Result:
        """Wrap one B search and C's retrieval results as a single attempt."""

        timer = CallTimer(ctx)
        if not is_usable(search_result):
            return _copy_error(search_result, timer)
        try:
            data: SearchResult = search_result["data"]
            if (
                data["profile_version"] != profile["version"]
                or plan["profile_version"] != profile["version"]
                or query["profile_version"] != profile["version"]
            ):
                raise ContractViolation(
                    "STATE_CONFLICT",
                    "profile_version",
                    "B, C, and profile must use the same version",
                )
            if data["plan_id"] != plan["plan_id"]:
                raise ContractViolation(
                    "STATE_CONFLICT", "search_result.plan_id", "search result does not belong to the current plan"
                )

            searched_listings = copy.deepcopy(data["items"])
            record_event("b_search_result", {
                "plan_id": data["plan_id"],
                "coverage": data["coverage"],
                "count": len(searched_listings),
                "listings": searched_listings,
            }, attempt_id=plan["attempt_id"])
            # Under normal configuration B is already constrained by candidate_limit=12. Here we add another boundary protection,
            # to prevent test doubles or custom SearchService from sending more than 12 sets into retrieve's LLM.
            listings: list[dict[str, Any]] = []
            seen_listing_keys: set[str] = set()
            for listing in searched_listings:
                key = listing["listing_key"]
                if key in seen_listing_keys:
                    continue
                seen_listing_keys.add(key)
                listings.append(listing)
                if len(listings) == self._top_k:
                    break
            # The v0 contract still requires ScreenResult. Here we only preserve B's candidate identity, without running
            # C.screen, and we do not interpret empty checks as C having verified hard conditions.
            candidate_keys = [item["listing_key"] for item in listings]
            screened: ScreenResult = {
                "profile_version": profile["version"],
                "eligible": [{"listing_key": key, "checks": []} for key in candidate_keys],
                "rejected": [],
                "needs_verification": [],
            }
            record_event("b_candidate_handoff", {
                "candidate_count": len(candidate_keys),
                "candidate_listings": listings,
            }, attempt_id=plan["attempt_id"])
            with stage_span("C", "retrieve", attempt_id=plan["attempt_id"]):
                retrieval = await self._retrieve(
                    query, listings, top_k=self._top_k, ctx=ctx
                )
            record_event("c_retrieval_result", retrieval, attempt_id=plan["attempt_id"])
            if not is_usable(retrieval):
                return _copy_error(retrieval, timer)

            issues = [
                *copy.deepcopy(search_result.get("issues") or []),
                *copy.deepcopy(retrieval.get("issues") or []),
                *copy.deepcopy(list(additional_issues)),
            ]
            status = "partial" if issues else "success"
            summary: AttemptSummary = {
                "attempt_id": plan["attempt_id"],
                "query_fingerprints": [
                    query_fingerprint(plan, item) for item in plan["queries"]
                ],
                "status": status,
                "eligible_count": len(candidate_keys),
            }
            attempt_coverage = copy.deepcopy(data["coverage"])
            if len(seen_listing_keys) < len({item["listing_key"] for item in searched_listings}):
                attempt_coverage["truncated"] = True
            outcome: AttemptOutcome = {
                "attempt_id": plan["attempt_id"],
                "search_status": status,
                "failure_code": None,
                "listing_snapshot": {
                    "snapshot_id": f"snapshot-{plan['plan_id']}-{plan['attempt_id']}",
                    "profile_version": profile["version"],
                    "items": listings,
                },
                "screen_result": screened,
                "retrieval_result": retrieval["data"],
                "coverage": attempt_coverage,
                "requirement_coverage": copy.deepcopy(requirement_coverage),
                "attempt_summary": summary,
            }
            return timer.partial(outcome, issues) if issues else timer.ok(outcome)
        except ContractViolation as exc:
            return timer.error(
                make_issue(exc.code, str(exc), field_path=exc.field_path, source="bc_adapter")
            )
        except (KeyError, TypeError, ValueError) as exc:
            return timer.error(
                make_issue(
                    "INVALID_OUTPUT",
                    f"B->C artifact cannot be converted: {type(exc).__name__}",
                    source="bc_adapter",
                )
            )

    async def adapt_fulfillment(
        self,
        fulfillment: Result,
        *,
        profile: ConversationProfile,
        query: QueryFeatures | None,
        plan: SearchPlan | None,
        search_result: Result | None,
        ctx: RunContext,
    ) -> Result:
        """Route B's public fulfillment response to A for clarification or to C/Decision."""

        timer = CallTimer(ctx)
        if not is_usable(fulfillment):
            return _copy_error(fulfillment, timer)
        data: RequirementFulfillment = fulfillment["data"]
        coverage = copy.deepcopy(data["coverage"])
        if data["status"] == "needs_clarification":
            transition: BCTransition = {
                "route": "clarification",
                "outcome": None,
                "clarification_questions": copy.deepcopy(
                    data["clarification_questions"]
                ),
                "requirement_coverage": coverage,
            }
            issues = copy.deepcopy(fulfillment.get("issues") or [])
            return timer.partial(transition, issues) if issues else timer.ok(transition)
        if query is None or plan is None or search_result is None:
            return timer.error(
                make_issue(
                    "INVALID_STATE",
                    "B has completed fulfillment, but is missing query, plan, or the original search result",
                    field_path="fulfillment",
                    source="bc_adapter",
                )
            )
        search_for_c: Result = {
            "status": fulfillment["status"],
            "data": copy.deepcopy(data["search_result"]),
            "issues": copy.deepcopy(fulfillment.get("issues") or []),
            "meta": copy.deepcopy(fulfillment["meta"]),
        }
        attempted = await self.adapt(
            search_for_c,
            profile=profile,
            query=query,
            plan=plan,
            ctx=ctx,
            requirement_coverage=coverage,
        )
        if not is_usable(attempted):
            return attempted
        transition = {
            "route": "decision",
            "outcome": attempted["data"],
            "clarification_questions": [],
            "requirement_coverage": coverage,
        }
        return (
            timer.partial(transition, attempted["issues"])
            if attempted["issues"]
            else timer.ok(transition)
        )
