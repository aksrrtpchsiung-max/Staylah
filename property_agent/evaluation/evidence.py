"""Evidence responsibilities extracted without changing behavior."""
from __future__ import annotations
from datetime import datetime, timedelta
from property_agent.contracts import ReviewIssue
from property_agent.evaluation.types import FRESHNESS_DAYS


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_stale(verified_at: datetime, reference_time: datetime) -> bool:
    """Compare ISO timestamps even when one source omitted its timezone."""
    if (verified_at.tzinfo is None) != (reference_time.tzinfo is None):
        verified_at = verified_at.replace(tzinfo=None)
        reference_time = reference_time.replace(tzinfo=None)
    return reference_time - verified_at > timedelta(days=FRESHNESS_DAYS)


def _review_issue(
    code: str,
    listing_key: str | None,
    field_path: str,
    message: str,
    severity: str,
    suggested_fix: str,
) -> ReviewIssue:
    return {
        "code": code,  # type: ignore[typeddict-item]
        "listing_key": listing_key,
        "field_path": field_path,
        "message": message,
        "severity": severity,  # type: ignore[typeddict-item]
        "suggested_fix": suggested_fix,
    }
