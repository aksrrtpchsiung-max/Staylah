"""Test fixture: assemble the artifact of one upstream search attempt using retained historical synthetic listings.

Do not fabricate Listings yourself, to avoid the fixture and the contract silently diverging. Screening groups are specified manually according to the actual listing fields,
because screen is implemented by module C, and here we only need an input that conforms to the contract shape.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from property_agent.profiles import from_legacy_user_profile

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "search"

# The profile is whole-unit rental, CLEMENTI, monthly rent <= SGD 3500, at least two bedrooms. The following groups are manually verified against this.
ELIGIBLE_KEYS = [
    "propertyguru:mock-000901",  # 3500, exactly equal to the budget cap
    "propertyguru:mock-000910",
    "propertyguru:mock-000914",
    "propertyguru:mock-000915",
    "propertyguru:mock-000916",
    "propertyguru:mock-000917",
    "propertyguru:mock-000920",
]
OVER_BUDGET_KEY = "propertyguru:mock-000902"  # 3501, over budget by 1
OVER_BUDGET_AMOUNT = 3501
UNKNOWN_PRICE_KEY = "propertyguru:mock-000903"  # price unknown, pending verification


def load_profile() -> dict:
    raw = json.loads((FIXTURES / "profile.json").read_text())
    return from_legacy_user_profile(raw)


def load_snapshot() -> dict:
    return json.loads((FIXTURES / "snapshot.json").read_text())


def build_ctx(run_id: str = "run-001", **overrides: Any) -> dict:
    ctx = {
        "user_id": "mock-user-001",
        "run_id": run_id,
        "conversation_id": f"conversation-{run_id}",
        "attempt_id": "attempt-001",
        "trace_id": f"trace-{run_id}",
        "call_id": f"call-{run_id}",
        "deadline_at": "2026-09-15T12:02:00+08:00",
        "source_mode": "mock",
    }
    ctx.update(overrides)
    return ctx


def _checks(field: str, status: str, reason: str) -> list[dict]:
    return [{"field": field, "status": status, "reason": reason, "evidence_ids": []}]


def build_outcome(
    *,
    eligible: int = 3,
    include_over_budget: bool = False,
    include_unknown_price: bool = False,
    has_more: bool = False,
    search_status: str = "success",
    failure_code: str | None = None,
    attempt_id: str = "attempt-001",
) -> dict:
    """Assemble an AttemptOutcome. When search_status='error', no candidates are included."""
    snapshot = load_snapshot()
    if search_status == "error":
        return {
            "attempt_id": attempt_id,
            "search_status": "error",
            "failure_code": failure_code or "TIMEOUT",
            "listing_snapshot": None,
            "screen_result": None,
            "retrieval_result": None,
            "coverage": None,
            "attempt_summary": {
                "attempt_id": attempt_id,
                "query_fingerprints": [],
                "status": "error",
                "eligible_count": 0,
            },
        }

    keys = ELIGIBLE_KEYS[:eligible]
    screen_result = {
        "profile_version": snapshot["profile_version"],
        "eligible": [
            {"listing_key": key, "checks": _checks("price.amount", "pass", "does not exceed the monthly rent cap")}
            for key in keys
        ],
        "rejected": (
            [
                {
                    "listing_key": OVER_BUDGET_KEY,
                    "checks": _checks("price.amount", "fail", f"{OVER_BUDGET_AMOUNT} exceeds the monthly rent cap"),
                }
            ]
            if include_over_budget
            else []
        ),
        "needs_verification": (
            [
                {
                    "listing_key": UNKNOWN_PRICE_KEY,
                    "checks": _checks("price.amount", "unknown", "the source did not provide an amount"),
                }
            ]
            if include_unknown_price
            else []
        ),
    }
    retrieval_result = {
        "profile_version": snapshot["profile_version"],
        "candidates": [
            {
                "listing_key": key,
                "exact_matches": ["CLEMENTI"],
                "vector_score": None,
                "keyword_score": None,
                "retrieval_rank": rank,
                "retrieval_score": None,
            }
            for rank, key in enumerate(keys, start=1)
        ],
        "input_count": len(keys),
        "returned_count": len(keys),
        "truncated": False,
        "method_version": "stub-0",
    }
    coverage = {
        "queried_sources": ["propertyguru"],
        "failed_sources": [],
        "queries_completed": True,
        "has_more": has_more,
        "next_pages": (
            [{"kind": "next_page", "query_id": "q-001", "cursor": "page-2"}] if has_more else []
        ),
        "truncated": False,
        "applied_filters": ["price.amount", "bedrooms", "location_id"],
        "unsupported_filters": [],
    }
    return {
        "attempt_id": attempt_id,
        "search_status": search_status,
        "failure_code": failure_code,
        "listing_snapshot": snapshot,
        "screen_result": screen_result,
        "retrieval_result": retrieval_result,
        "coverage": coverage,
        "attempt_summary": {
            "attempt_id": attempt_id,
            "query_fingerprints": [],
            "status": search_status,
            "eligible_count": len(keys),
        },
    }
