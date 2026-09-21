"""确定性硬条件筛选。仅用于 mock 联调，不替代其他团队的 screen()。"""
from __future__ import annotations

from typing import Any

from property_agent.contracts import ScreenResult, UserProfile


def screen_listings(listings: list[dict[str, Any]], profile: UserProfile) -> ScreenResult:
    eligible: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    needs_verification: list[dict[str, Any]] = []
    for listing in listings:
        checks = _check_listing(listing, profile)
        statuses = {item["status"] for item in checks}
        bucket = {
            "listing_key": listing["listing_key"],
            "checks": checks,
        }
        if "fail" in statuses:
            rejected.append(bucket)
        elif "unknown" in statuses:
            needs_verification.append(bucket)
        else:
            eligible.append(bucket)
    return {
        "profile_version": profile["version"],
        "eligible": eligible,
        "rejected": rejected,
        "needs_verification": needs_verification,
    }


def _check_listing(listing: dict[str, Any], profile: UserProfile) -> list[dict[str, Any]]:
    constraints = profile["hard_constraints"]
    price = listing.get("price") or {}
    attributes = listing.get("attributes") or {}
    expected_type = "rent" if profile.get("intent") == "rent" else "sale"
    checks = [
        _check(
            "transaction_type",
            listing.get("transaction_type") == expected_type,
            "交易类型与当前找房意图一致" if listing.get("transaction_type") == expected_type else "交易类型与意图不一致",
            _evidence(listing, "transaction_type"),
        )
    ]

    currency = constraints.get("currency")
    if currency:
        actual = price.get("currency")
        if actual is None:
            checks.append(_unknown("price.currency", "来源未给出币种", _evidence(listing, "price.currency")))
        else:
            checks.append(
                _check(
                    "price.currency",
                    actual == currency,
                    "币种匹配" if actual == currency else f"{actual} 与要求的 {currency} 不一致",
                    _evidence(listing, "price.currency"),
                )
            )

    period = constraints.get("price_period")
    if period:
        actual = price.get("period")
        if actual is None:
            checks.append(_unknown("price.period", "来源未给出计价周期", _evidence(listing, "price.period")))
        else:
            checks.append(
                _check(
                    "price.period",
                    actual == period,
                    "计价周期匹配" if actual == period else f"{actual} 与要求的 {period} 不一致",
                    _evidence(listing, "price.period"),
                )
            )

    max_price = constraints.get("max_price")
    if max_price is not None:
        if price.get("status") != "known" or price.get("amount") is None:
            checks.append(_unknown("price.amount", "来源未给出金额", _evidence(listing, "price.amount")))
        else:
            amount = price["amount"]
            checks.append(
                _check(
                    "price.amount",
                    amount <= max_price,
                    "未超过预算上限" if amount <= max_price else f"{amount} 超过月租上限",
                    _evidence(listing, "price.amount"),
                )
            )

    rental_scope = constraints.get("rental_scope")
    if rental_scope:
        actual = attributes.get("listing_scope")
        if actual in (None, "unknown"):
            checks.append(
                _unknown("attributes.listing_scope", "来源未给出整租/房间口径", [])
            )
        else:
            checks.append(
                _check(
                    "attributes.listing_scope",
                    actual == rental_scope,
                    "出租口径匹配" if actual == rental_scope else f"{actual} 与要求的 {rental_scope} 不一致",
                    [],
                )
            )

    locations = constraints.get("locations") or []
    if locations:
        actual = listing.get("location_id")
        if actual is None:
            checks.append(_unknown("location_id", "来源未给出地点", _evidence(listing, "location_id")))
        else:
            checks.append(
                _check(
                    "location_id",
                    actual in locations,
                    "地点在已确认范围内" if actual in locations else f"{actual} 不在 {locations} 内",
                    _evidence(listing, "location_id"),
                )
            )

    min_bedrooms = constraints.get("min_bedrooms")
    if min_bedrooms is not None:
        actual = listing.get("bedrooms")
        if actual is None:
            checks.append(_unknown("bedrooms", "来源未给出卧室数", _evidence(listing, "bedrooms")))
        else:
            checks.append(
                _check(
                    "bedrooms",
                    actual >= min_bedrooms,
                    "卧室数满足下限" if actual >= min_bedrooms else f"{actual} 少于最少 {min_bedrooms} 间",
                    _evidence(listing, "bedrooms"),
                )
            )
    return checks


def _evidence(listing: dict[str, Any], field: str) -> list[str]:
    return [
        item["evidence_id"]
        for item in listing.get("evidence") or []
        if item.get("field") == field
    ]


def _check(field: str, passed: bool, reason: str, evidence_ids: list[str]) -> dict[str, Any]:
    return {
        "field": field,
        "status": "pass" if passed else "fail",
        "reason": reason,
        "evidence_ids": evidence_ids,
    }


def _unknown(field: str, reason: str, evidence_ids: list[str]) -> dict[str, Any]:
    return {
        "field": field,
        "status": "unknown",
        "reason": reason,
        "evidence_ids": evidence_ids,
    }
