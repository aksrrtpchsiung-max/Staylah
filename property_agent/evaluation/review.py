"""Review responsibilities extracted without changing behavior."""
from __future__ import annotations
import re
from datetime import datetime
from time import perf_counter
from property_agent.contracts import Claim, ConversationProfile, ContractViolation, EvaluationResult, ListingSnapshot, Result, ReviewIssue, ReviewResult, RoutingPolicy, RunContext
from property_agent.evaluation.configuration import _resolve_evaluation_review_model
from property_agent.evaluation.evidence import _is_stale, _parse_timestamp, _review_issue
from property_agent.evaluation.results import _error, _partial, _success
from property_agent.evaluation.types import EvaluationReviewModelError, FRESHNESS_DAYS, MAX_RECOMMENDATIONS
from property_agent.evaluation.validation import _validate_policy, _validate_profile, _violation


async def review(
    profile: ConversationProfile,
    evaluation: EvaluationResult,
    listing_snapshot: ListingSnapshot,
    *,
    policy: RoutingPolicy,
    ctx: RunContext,
) -> Result[ReviewResult]:
    """执行本地 contract 审查；模型可用时再叠加独立语义复核。"""
    started = perf_counter()
    try:
        _validate_profile(profile)
        _validate_policy(policy)
        if evaluation["profile_version"] != profile["version"]:
            raise _violation("evaluation.profile_version", "does not match profile.version", "INVALID_STATE")
        if listing_snapshot["profile_version"] != profile["version"]:
            raise _violation("listing_snapshot.profile_version", "does not match profile.version", "INVALID_STATE")
        if evaluation["snapshot_id"] != listing_snapshot["snapshot_id"]:
            raise _violation("evaluation.snapshot_id", "does not match listing_snapshot.snapshot_id", "INVALID_STATE")

        listings = {listing["listing_key"]: listing for listing in listing_snapshot["items"]}
        recommendation = evaluation["recommendation"]
        items = recommendation["ordered_items"]
        issues: list[ReviewIssue] = []
        recommendation_limit = min(policy["display_limit"], MAX_RECOMMENDATIONS)
        if len(items) > recommendation_limit:
            del items[recommendation_limit:]
            issues.append(
                _review_issue(
                    "TOO_MANY_ITEMS",
                    None,
                    "recommendation.ordered_items",
                    "The recommendation exceeded 10 listings or the display limit and was truncated during review.",
                    "warning",
                    f"Only the first {recommendation_limit} listings were kept.",
                )
            )

        reference_time = _parse_timestamp(ctx.get("deadline_at")) or datetime.now().astimezone()
        # review 直接修正可确定的问题，不再把草稿退回 evaluate。
        existing_items = list(items)
        items[:] = [item for item in existing_items if item["listing_key"] in listings]
        for missing_index, item in enumerate(existing_items):
            if item["listing_key"] not in listings:
                issues.append(
                    _review_issue(
                        "UNKNOWN_LISTING",
                        item["listing_key"],
                        f"recommendation.ordered_items[{missing_index}].listing_key",
                        "The recommendation referenced a listing absent from the snapshot and was removed during review.",
                        "warning",
                        "The invalid recommendation was removed.",
                    )
                )

        has_unknown = False
        for index, item in enumerate(items):
            key = item["listing_key"]
            path = f"recommendation.ordered_items[{index}]"
            if item["rank"] != index + 1:
                item["rank"] = index + 1
                issues.append(
                    _review_issue(
                        "INVALID_RANK",
                        key,
                        f"{path}.rank",
                        "Recommendation ranks were not consecutive and were renumbered during review.",
                        "warning",
                        "Ranks were reassigned using the current order.",
                    )
                )
            listing = listings.get(key)
            assert listing is not None
            evidence_ids = {evidence["evidence_id"] for evidence in listing.get("evidence", [])}
            for claim_group in ("reasons", "tradeoffs"):
                original_claims = list(item[claim_group])
                valid_claims: list[Claim] = []
                for claim_index, claim in enumerate(original_claims):
                    missing_evidence = claim["kind"] == "fact" and not claim["evidence_ids"]
                    invalid_evidence = any(
                        evidence_id not in evidence_ids for evidence_id in claim["evidence_ids"]
                    )
                    if missing_evidence or invalid_evidence:
                        issues.append(
                            _review_issue(
                                "UNSUPPORTED_CLAIM",
                                key,
                                f"{path}.{claim_group}[{claim_index}]",
                                "A recommendation claim lacked valid evidence and was removed during review.",
                                "warning",
                                "The unsupported claim was removed.",
                            )
                        )
                        continue
                    valid_claims.append(claim)
                item[claim_group][:] = valid_claims
            verified_at = _parse_timestamp(listing.get("last_verified_at"))
            if verified_at and _is_stale(verified_at, reference_time):
                issues.append(
                    _review_issue(
                        "STALE_EVIDENCE",
                        key,
                        "last_verified_at",
                        f"The listing's independent verification is older than {FRESHNESS_DAYS} days.",
                        "warning",
                        "Verify the listing again or state the freshness limitation in the response.",
                    )
                )
            if item["unknowns"]:
                has_unknown = True

        if has_unknown and not recommendation["limitations"]:
            recommendation["limitations"].append(
                "Some listing details remain unknown or unverified; confirm them with the publisher before signing."
            )
            issues.append(
                _review_issue(
                    "MISSING_LIMITATION",
                    None,
                    "recommendation.limitations",
                    "The recommendation contains unknown or unverified details, so review added a limitation.",
                    "warning",
                    "A general verification limitation was added.",
                )
            )

        # The model may review language semantics only. Build an allowlist of exact
        # semantic targets so even an injected/custom model cannot override the
        # deterministic decisions above about counts, ranks, IDs, freshness, or
        # contract structure.
        semantic_claim_owners: dict[str, str | None] = {
            "recommendation.summary": None,
        }
        for item_index, item in enumerate(items):
            for claim_group in ("reasons", "tradeoffs"):
                for claim_index, _claim in enumerate(item[claim_group]):
                    semantic_claim_owners[
                        f"recommendation.ordered_items[{item_index}].{claim_group}[{claim_index}]"
                    ] = item["listing_key"]

        model, unavailable_reason = _resolve_evaluation_review_model()
        model_failed = False
        if model is not None:
            try:
                model_issues = await model.review(profile, evaluation, list(listings.values()), policy)
                known_issue_keys = {
                    (issue["code"], issue["listing_key"], issue["field_path"], issue["message"])
                    for issue in issues
                }
                claim_removals: set[tuple[int, str, int]] = set()
                for issue in model_issues:
                    if issue["code"] == "UNSUPPORTED_CLAIM":
                        expected_owner = semantic_claim_owners.get(issue["field_path"], object())
                        if expected_owner != issue["listing_key"]:
                            continue
                        if issue["field_path"] == "recommendation.summary":
                            recommendation["summary"] = (
                                f"This search produced {len(items)} candidates based on relevance scores. "
                                "Confirm listing facts and unresolved details using the cited evidence and publisher."
                            )
                        else:
                            match = re.fullmatch(
                                r"recommendation\.ordered_items\[(\d+)\]\."
                                r"(reasons|tradeoffs)\[(\d+)\]",
                                issue["field_path"],
                            )
                            if match is None:
                                continue
                            claim_removals.add(
                                (int(match.group(1)), match.group(2), int(match.group(3)))
                            )
                        normalized_issue = _review_issue(
                            "UNSUPPORTED_CLAIM",
                            issue["listing_key"],
                            issue["field_path"],
                            issue["message"] + " Review corrected it automatically.",
                            "warning",
                            "Unsupported claims were removed or an exaggerated summary was replaced.",
                        )
                    elif (
                        issue["code"] == "MISSING_LIMITATION"
                        and issue["listing_key"] is None
                        and issue["field_path"] == "recommendation.limitations"
                    ):
                        limitation = (
                            "Some listing details still require confirmation from the publisher; "
                            "the recommendation does not independently verify every requirement."
                        )
                        if limitation not in recommendation["limitations"]:
                            recommendation["limitations"].append(limitation)
                        normalized_issue = _review_issue(
                            "MISSING_LIMITATION",
                            None,
                            "recommendation.limitations",
                            issue["message"] + " Review added the required limitation automatically.",
                            "warning",
                            "A limitation was added.",
                        )
                    else:
                        # Structure and reference codes are owned exclusively by the
                        # deterministic checks above. Never merge an LLM opinion about
                        # TOO_MANY_ITEMS, INVALID_RANK, UNKNOWN_LISTING,
                        # STALE_EVIDENCE, or evidence-ID existence.
                        continue
                    issue_key = (
                        normalized_issue["code"],
                        normalized_issue["listing_key"],
                        normalized_issue["field_path"],
                        normalized_issue["message"],
                    )
                    if issue_key not in known_issue_keys:
                        issues.append(normalized_issue)
                        known_issue_keys.add(issue_key)
                for item_index, claim_group, claim_index in sorted(
                    claim_removals, reverse=True
                ):
                    if item_index >= len(items):
                        continue
                    claims = items[item_index][claim_group]
                    if claim_index < len(claims):
                        del claims[claim_index]
            except EvaluationReviewModelError as exc:
                model_failed = True
                unavailable_reason = (
                    f"{exc.failure_kind}: {exc}; used deterministic review"
                )
            except Exception:
                model_failed = True
                unavailable_reason = (
                    "INVALID_REVIEW_SCHEMA: unexpected review adapter output; "
                    "used deterministic review"
                )
        result: ReviewResult = {
            "passed": True,
            "issues": issues,
        }
        if model is None or model_failed:
            return _partial(
                result,
                ctx,
                started,
                unavailable_reason
                or "DeepSeek review model is unavailable; used deterministic review",
                retryable=model is not None,
                code="MODEL_UNAVAILABLE",
                source="review_model",
            )
        return _success(result, ctx, started)
    except ContractViolation as exc:
        return _error(ctx, started, exc.code, str(exc), exc.field_path)
    except (KeyError, TypeError) as exc:
        return _error(ctx, started, "INVALID_INPUT", str(exc))
