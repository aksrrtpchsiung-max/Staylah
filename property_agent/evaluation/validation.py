"""Validation responsibilities extracted without changing behavior."""
from __future__ import annotations
from typing import Any
from property_agent.contracts import ConversationProfile, ContractViolation, Listing, ListingConstraint, RoutingPolicy



def _violation(field_path: str, message: str, code: str = "INVALID_INPUT") -> ContractViolation:
    return ContractViolation(code, field_path, message)


def _validate_policy(policy: RoutingPolicy) -> None:
    for field in ("min_matches", "display_limit", "max_search_attempts", "max_repairs"):
        value = policy.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise _violation(f"policy.{field}", "must be a non-negative integer")
    if policy["display_limit"] == 0:
        raise _violation("policy.display_limit", "must be greater than zero")


def _validate_profile(profile: ConversationProfile) -> None:
    version = profile.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 0:
        raise _violation("profile.version", "must be a non-negative integer")
    if profile.get("status") != "confirmed" or profile.get("confirmed_version") != version:
        raise _violation(
            "profile.confirmed_version",
            "C only accepts a confirmed profile whose confirmed_version matches version",
            "INVALID_STATE",
        )
    constraints = profile.get("listing_constraints")
    if not isinstance(constraints, list):
        raise _violation("profile.listing_constraints", "must be a list")
    for index, constraint in enumerate(constraints):
        path = f"profile.listing_constraints[{index}]"
        if not isinstance(constraint, dict):
            raise _violation(path, "must be an object")
        if not isinstance(constraint.get("constraint_id"), str) or not constraint["constraint_id"].strip():
            raise _violation(f"{path}.constraint_id", "must be a non-empty string")
        if not isinstance(constraint.get("field_path"), str) or not constraint["field_path"].strip():
            raise _violation(f"{path}.field_path", "must be a non-empty string")
        if constraint.get("operator") not in {"eq", "neq", "lt", "lte", "gt", "gte", "between", "in", "contains"}:
            raise _violation(f"{path}.operator", "is not supported")
        if constraint.get("strength") not in {"hard", "soft"}:
            raise _violation(f"{path}.strength", "must be hard or soft")
        if constraint.get("priority") not in {"high", "medium", "low"}:
            raise _violation(f"{path}.priority", "must be high, medium, or low")
        if constraint["operator"] == "between" and (
            not isinstance(constraint.get("value"), list) or len(constraint["value"]) != 2
        ):
            raise _violation(f"{path}.value", "between requires a two-item list")
        if constraint["operator"] == "in" and not isinstance(constraint.get("value"), list):
            raise _violation(f"{path}.value", "in requires a list")


def _validate_listing(listing: Listing, path: str) -> None:
    if not isinstance(listing, dict):
        raise _violation(path, "must be an object")
    if not isinstance(listing.get("listing_key"), str) or not listing["listing_key"].strip():
        raise _violation(f"{path}.listing_key", "must be a non-empty string")
    price = listing.get("price")
    if not isinstance(price, dict):
        raise _violation(f"{path}.price", "must be an object")
    amount = price.get("amount")
    if amount is not None and (
        not isinstance(amount, int) or isinstance(amount, bool) or amount < 0
    ):
        raise _violation(f"{path}.price.amount", "must be a non-negative integer or null")
    if price.get("status") not in {"known", "unknown", "conflict"}:
        raise _violation(f"{path}.price.status", "must be known, unknown, or conflict")
    if listing.get("listing_status") not in {"active", "inactive", "unknown"}:
        raise _violation(f"{path}.listing_status", "must be active, inactive, or unknown")
    if not isinstance(listing.get("attributes"), dict):
        raise _violation(f"{path}.attributes", "must be an object")


def _evidence_ids(listing: Listing, field: str) -> list[str]:
    ids = [
        evidence["evidence_id"]
        for evidence in listing.get("evidence", [])
        if evidence.get("field") == field and isinstance(evidence.get("evidence_id"), str)
    ]
    if field == "price.amount" and not ids:
        ids = [item for item in listing["price"].get("evidence_ids", []) if isinstance(item, str)]
    return ids


def _listing_field_value(listing: Listing, field: str) -> Any:
    """读取 Listing 的点分路径；来源明确表示未知或冲突时返回 None。"""
    if field == "price.amount" and listing["price"].get("status") != "known":
        return None
    current: Any = listing
    for segment in field.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(segment)
    if current is None or current in ("unknown", "conflict"):
        return None
    return current


def _constraint_matches(actual: Any, constraint: ListingConstraint) -> bool | None:
    """返回 True/False；数据缺失或值无法比较时返回 None。"""
    if actual is None:
        return None
    expected = constraint["value"]
    operator = constraint["operator"]
    try:
        if operator == "eq":
            return actual == expected
        if operator == "neq":
            return actual != expected
        if operator == "lt":
            return actual < expected
        if operator == "lte":
            return actual <= expected
        if operator == "gt":
            return actual > expected
        if operator == "gte":
            return actual >= expected
        if operator == "between":
            lower, upper = expected
            return lower <= actual <= upper
        if operator == "in":
            return actual in expected
        if operator == "contains":
            if isinstance(actual, str) and isinstance(expected, str):
                return expected.casefold() in actual.casefold()
            return expected in actual
    except (TypeError, ValueError):
        return None
    return None
