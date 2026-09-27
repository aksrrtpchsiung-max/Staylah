"""Recommendations responsibilities extracted without changing behavior."""
from __future__ import annotations
from typing import Any, Iterable
from property_agent.contracts import Claim, ConversationProfile, Coverage, Listing, ListingConstraint, SearchDirective
from property_agent.evaluation.validation import _constraint_matches, _evidence_ids, _listing_field_value


def _soft_preference_score(
    listing: Listing, constraints: Iterable[ListingConstraint]
) -> tuple[float, list[Claim]]:
    weights = {"high": 3.0, "medium": 2.0, "low": 1.0}
    score = 0.0
    claims: list[Claim] = []
    for constraint in constraints:
        if constraint["strength"] != "soft":
            continue
        actual = _listing_field_value(listing, constraint["field_path"])
        if _constraint_matches(actual, constraint) is True:
            score += weights[constraint["priority"]]
            claims.append(
                {
                    "kind": "judgment",
                    "text": (
                        "Matches your preference: "
                        f"{constraint['field_path']} {constraint['operator']} {constraint['value']!r}."
                    ),
                    "evidence_ids": _evidence_ids(listing, constraint["field_path"]),
                }
            )
    return score, claims


def _fact_claim(listing: Listing, field: str, text: str) -> Claim | None:
    evidence_ids = _evidence_ids(listing, field)
    if not evidence_ids:
        return None
    return {"kind": "fact", "text": text, "evidence_ids": evidence_ids}


def _recommendation_item(listing: Listing, rank: int, preference_claims: list[Claim]) -> dict[str, Any]:
    reasons: list[Claim] = []
    price = listing["price"]
    if price["amount"] is not None and price["status"] == "known":
        claim = _fact_claim(
            listing,
            "price.amount",
            f"The source lists {price['currency']} {price['amount']} ({price.get('period') or 'period unspecified'}).",
        )
        if claim:
            reasons.append(claim)
    listing_scope = listing["attributes"].get("listing_scope")
    if listing_scope in {"room", "bedspace"}:
        scope_text = "a private room" if listing_scope == "room" else "a bedspace"
        claim = _fact_claim(
            listing,
            "attributes.listing_scope",
            f"The source lists this property as {scope_text}.",
        )
        if claim:
            reasons.append(claim)
    elif isinstance(listing.get("bedrooms"), int) and listing["bedrooms"] > 0:
        claim = _fact_claim(listing, "bedrooms", f"The source lists {listing['bedrooms']} bedrooms.")
        if claim:
            reasons.append(claim)
    if listing.get("location_id"):
        claim = _fact_claim(listing, "location_id", f"The listed location is {listing['location_id']}.")
        if claim:
            reasons.append(claim)
    reasons.extend(preference_claims)

    tradeoffs: list[Claim] = []
    unknowns: list[str] = []
    if listing["attributes"].get("wifi_included") is None:
        unknowns.append("Whether Wi-Fi is included")
    if listing["attributes"].get("utilities_included") is None:
        unknowns.append("Whether utilities are included")
    if listing["attributes"].get("owner_stays") is None:
        unknowns.append("Whether the owner lives in the property")
    if listing["last_verified_at"] is None:
        unknowns.append("Current availability has not been independently verified")
    if listing["field_issues"]:
        unknowns.append("Some source details need verification")

    return {
        "listing_key": listing["listing_key"],
        "rank": rank,
        "reasons": reasons,
        "tradeoffs": tradeoffs,
        "unknowns": unknowns,
    }


def _make_directive(
    profile: ConversationProfile,
    coverage: Coverage,
    candidate_keys: list[str],
    enough_candidates: bool,
) -> SearchDirective | None:
    if enough_candidates:
        return None
    next_pages = coverage.get("next_pages", [])
    incomplete = (
        not coverage.get("queries_completed", True)
        or bool(coverage.get("failed_sources"))
        or bool(coverage.get("truncated"))
    )
    if incomplete and next_pages:
        reason_code = "incomplete_coverage"
    elif next_pages or coverage.get("has_more", False):
        reason_code = "insufficient_candidates"
    else:
        return None
    return {
        "reason_code": reason_code,  # type: ignore[typeddict-item]
        "strategy_changes": list(next_pages),
        "base_profile_version": profile["version"],
        "evidence_listing_keys": candidate_keys,
    }
