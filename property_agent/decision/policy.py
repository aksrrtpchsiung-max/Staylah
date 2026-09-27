"""Default values and validation for RoutingPolicy.

The numeric values come from the recommended values in the interface review draft and are marked as pending team confirmation; they are not measured throughput or quality conclusions.
"""
from __future__ import annotations

from property_agent.contracts import ContractViolation, RoutingPolicy

# Meeting-recommended values: publishing is allowed once 3 qualified candidates are reached, with a display limit of 10; the first search plus two supplementary searches; a total of 1 model content repair.
DEFAULT_POLICY: RoutingPolicy = {
    "min_matches": 3,
    "display_limit": 10,
    "max_search_attempts": 3,
    "max_repairs": 1,
}


def _require_count(policy: RoutingPolicy, key: str, minimum: int) -> int:
    value = policy.get(key)  # type: ignore[arg-type]
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ContractViolation(
            "INVALID_INPUT", f"policy.{key}", f"policy.{key} must be an integer not less than {minimum}"
        )
    return value


def validate_policy(policy: RoutingPolicy) -> None:
    """Reject invalid policies first; do not enter routing evaluation."""
    if not isinstance(policy, dict):
        raise ContractViolation("INVALID_INPUT", "policy", "policy must be an object")
    min_matches = _require_count(policy, "min_matches", 1)
    display_limit = _require_count(policy, "display_limit", 1)
    _require_count(policy, "max_search_attempts", 1)
    _require_count(policy, "max_repairs", 0)
    if display_limit < min_matches:
        raise ContractViolation(
            "INVALID_INPUT", "policy.display_limit", "display_limit cannot be less than min_matches"
        )
