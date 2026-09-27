"""Evaluation responsibilities extracted without changing behavior."""
from __future__ import annotations
from time import perf_counter
from typing import Any
from property_agent.contracts import Assessment, ConversationProfile, ContractViolation, Coverage, EvaluationResult, Listing, ListingSnapshot, Result, RetrievalResult, ReviewResult, RoutingPolicy, RunContext, ScreenResult
from property_agent.evaluation.configuration import _resolve_evaluation_review_model
from property_agent.evaluation.recommendations import _make_directive, _recommendation_item, _soft_preference_score
from property_agent.evaluation.results import _error, _partial, _success
from property_agent.evaluation.types import DETERMINISTIC_METHOD_VERSION, EvaluationDecision, EvaluationReviewModelError, MAX_RECOMMENDATIONS
from property_agent.evaluation.validation import _validate_listing, _validate_policy, _validate_profile, _violation


async def evaluate(
    profile: ConversationProfile,
    retrieval: RetrievalResult,
    screen_result: ScreenResult,
    listing_snapshot: ListingSnapshot,
    coverage: Coverage,
    repair_context: ReviewResult | None,
    *,
    policy: RoutingPolicy,
    ctx: RunContext,
) -> Result[EvaluationResult]:
    """Select the top 10 from at most 12 scored candidates, and have the LLM summarize and suggest the next step.

    The ``screen_result.eligible`` of v0 carries the B candidate key in the main flow; it does not mean that C has rechecked the hard conditions.
    Candidate selection strictly follows the score order of ``retrieve``; the LLM does not participate in reordering, adding, or removing.
    """
    started = perf_counter()
    try:
        _validate_profile(profile)
        _validate_policy(policy)
        if retrieval["profile_version"] != profile["version"]:
            raise _violation("retrieval.profile_version", "does not match profile.version", "INVALID_STATE")
        if screen_result["profile_version"] != profile["version"]:
            raise _violation("screen_result.profile_version", "does not match profile.version", "INVALID_STATE")
        if listing_snapshot["profile_version"] != profile["version"]:
            raise _violation("listing_snapshot.profile_version", "does not match profile.version", "INVALID_STATE")

        listings_by_key: dict[str, Listing] = {}
        for index, listing in enumerate(listing_snapshot["items"]):
            _validate_listing(listing, f"listing_snapshot.items[{index}]")
            listings_by_key[listing["listing_key"]] = listing
        candidate_keys = {item["listing_key"] for item in screen_result["eligible"]}

        ranked: list[tuple[int, float, Listing]] = []
        seen_candidates: set[str] = set()
        for index, candidate in enumerate(retrieval["candidates"]):
            key = candidate["listing_key"]
            if key in seen_candidates:
                raise _violation(f"retrieval.candidates[{index}].listing_key", "must not be duplicated")
            seen_candidates.add(key)
            listing = listings_by_key.get(key)
            if listing is None:
                raise _violation(f"retrieval.candidates[{index}].listing_key", "is not in listing_snapshot")
            if key not in candidate_keys:
                raise _violation(f"retrieval.candidates[{index}].listing_key", "is not in B candidate handoff")
            preference_score, _ = _soft_preference_score(listing, profile["listing_constraints"])
            ranked.append((candidate["retrieval_rank"], preference_score, listing))

        # First prepare the verifiable facts required by the action; the model can only choose on this basis and cannot fabricate instructions on its own.
        enough_candidates = len(screen_result["eligible"]) >= policy["min_matches"]
        directive = _make_directive(
            profile,
            coverage,
            [item["listing_key"] for item in screen_result["eligible"]],
            enough_candidates,
        )

        model, unavailable_reason = _resolve_evaluation_review_model()
        used_fallback = model is None
        if model is not None:
            try:
                decision = await model.evaluate(
                    profile, retrieval, screen_result, list(listings_by_key.values()), coverage, policy
                )
                # The model's route suggestion must be supported by actual data and policy; otherwise, adopt a safe fallback route.
                if (
                    (decision.next_action == "publish" and not enough_candidates)
                    or (decision.next_action == "research" and directive is None)
                    or decision.next_action == "ask_user"
                ):
                    raise EvaluationReviewModelError("DeepSeek evaluation proposed an unsupported next action")
            except Exception:
                # The injection implementation may also throw SDK/network exceptions; retain an explainable fallback result instead of interrupting the flow.
                used_fallback = True
                unavailable_reason = "DeepSeek evaluation failed validation; used deterministic fallback"

        if used_fallback:
            ranked.sort(key=lambda row: (row[0], row[2]["listing_key"]))
            recommendation_limit = min(policy["display_limit"], MAX_RECOMMENDATIONS)
            selected_keys = [
                listing["listing_key"]
                for _, _, listing in ranked[:recommendation_limit]
            ]
            if enough_candidates:
                next_action, next_reason = "publish", "enough_matches"
            elif directive is not None:
                next_action, next_reason = "research", directive["reason_code"]
            else:
                next_action, next_reason = "finish", "insufficient_candidates"
            decision = EvaluationDecision(
                selected_listing_keys=selected_keys,
                enough_candidates=enough_candidates,
                next_action=next_action,
                next_reason_code=next_reason,
                summary=f"There are {len(selected_keys)} candidates supplied by the search service.",
                limitations=[],
            )

        recommendation_items: list[dict[str, Any]] = []
        for rank, listing_key in enumerate(decision.selected_listing_keys, start=1):
            listing = listings_by_key[listing_key]
            _, preference_claims = _soft_preference_score(listing, profile["listing_constraints"])
            recommendation_items.append(_recommendation_item(listing, rank, preference_claims))

        findings: list[str] = [
            "Module C does not rescreen listings returned by Module B; hard-constraint matching relies on Module B."
        ]
        if not coverage.get("queries_completed", True):
            findings.append("Search coverage is incomplete.")

        limitations = list(dict.fromkeys(item.strip() for item in decision.limitations if item.strip()))
        if ctx["source_mode"] == "mock":
            limitations.append("These results use mock data for demonstration only.")
        if retrieval.get("method_version") == DETERMINISTIC_METHOD_VERSION:
            limitations.append(
                "DeepSeek listing scoring was unavailable; local structured-requirement scoring was used to rank candidates."
            )
        limitations.insert(
            0,
            "Recommendations are based on the available PropertyGuru evidence. Any requirement without explicit "
            "evidence is identified separately below."
        )
        if repair_context and not repair_context["passed"]:
            limitations.append("An earlier review found issues; these recommendations require a new review.")
        if not enough_candidates:
            limitations.append("Module B supplied too few candidates.")
        if used_fallback:
            limitations.append("Model evaluation was unavailable; rules were used to rank candidates and determine the next step.")

        recommendation = {
            "ordered_items": recommendation_items,
            "summary": decision.summary or f"There are {len(recommendation_items)} candidates supplied by the search service.",
            "limitations": list(dict.fromkeys(limitations)),
        }
        assessment: Assessment = {
            "constraint_findings": findings,
            "search_directive": directive,
            "relaxation_proposals": [],
            "next_action": decision.next_action,  # type: ignore[typeddict-item]
            "next_reason_code": decision.next_reason_code,
        }
        output: EvaluationResult = {
            "profile_version": profile["version"],
            "snapshot_id": listing_snapshot["snapshot_id"],
            "recommendation": recommendation,
            "assessment": assessment,
        }
        if used_fallback:
            return _partial(
                output,
                ctx,
                started,
                unavailable_reason or "DeepSeek evaluation model is unavailable; used deterministic fallback",
                retryable=model is not None,
                code="MODEL_UNAVAILABLE",
                source="deepseek",
            )
        return _success(output, ctx, started)
    except ContractViolation as exc:
        return _error(ctx, started, exc.code, str(exc), exc.field_path)
    except (KeyError, TypeError) as exc:
        return _error(ctx, started, "INVALID_INPUT", str(exc))
