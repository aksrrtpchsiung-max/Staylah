"""把夹具里的 Result[SearchResult] 变成 decision 图消费的 AttemptOutcome。"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from property_agent.contracts import ConversationProfile, Result
from property_agent.decision.boundaries import AttemptOutcome
from tests.mock_search.screen import screen_listings

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "search"


def load_search_fixture(name: str) -> Result:
    path = FIXTURES / f"{name}.json"
    return json.loads(path.read_text())


def first_attempt_from_fixture(
    name: str,
    profile: ConversationProfile,
    *,
    attempt_id: str = "attempt-001",
    item_limit: int | None = None,
    has_more: bool | None = None,
) -> AttemptOutcome:
    result = load_search_fixture(name)
    if item_limit is not None and result.get("data"):
        data = copy.deepcopy(result["data"])
        data["items"] = data.get("items", [])[:item_limit]
        if has_more is not None:
            coverage = dict(data.get("coverage") or {})
            coverage["has_more"] = has_more
            if not has_more:
                coverage["next_pages"] = []
            data["coverage"] = coverage
        result = {**result, "data": data}
    return outcome_from_search_result(result, profile, attempt_id=attempt_id)


def outcome_from_search_result(
    result: Result,
    profile: ConversationProfile,
    *,
    attempt_id: str,
) -> AttemptOutcome:
    if result.get("status") == "error" or result.get("data") is None:
        issues = result.get("issues") or []
        return {
            "attempt_id": attempt_id,
            "search_status": "error",
            "failure_code": issues[0]["code"] if issues else "SOURCE_UNAVAILABLE",
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

    data: dict[str, Any] = copy.deepcopy(result["data"])
    listings = data.get("items") or []
    screen_result = screen_listings(listings, profile)
    eligible_keys = [item["listing_key"] for item in screen_result["eligible"]]
    search_status = result["status"] if result["status"] in {"success", "partial"} else "success"
    return {
        "attempt_id": attempt_id,
        "search_status": search_status,
        "failure_code": None,
        "listing_snapshot": {
            "snapshot_id": f"mock-snapshot-{attempt_id}",
            "profile_version": profile["version"],
            "items": listings,
        },
        "screen_result": screen_result,
        "retrieval_result": {
            "profile_version": profile["version"],
            "candidates": [
                {
                    "listing_key": key,
                    "exact_matches": [
                        item.get("target")
                        for item in profile.get("derived_data_requirements") or []
                        if item.get("target")
                    ],
                    "vector_score": None,
                    "keyword_score": None,
                    "retrieval_rank": rank,
                    "retrieval_score": None,
                }
                for rank, key in enumerate(eligible_keys, start=1)
            ],
            "input_count": len(listings),
            "returned_count": len(eligible_keys),
            "truncated": False,
            "method_version": "mock-screen-0",
        },
        "coverage": data.get("coverage")
        or {
            "queried_sources": ["propertyguru"],
            "failed_sources": [],
            "queries_completed": True,
            "has_more": False,
            "next_pages": [],
            "truncated": False,
            "applied_filters": [],
            "unsupported_filters": [],
        },
        "attempt_summary": {
            "attempt_id": attempt_id,
            "query_fingerprints": [],
            "status": search_status,
            "eligible_count": len(eligible_keys),
        },
    }
