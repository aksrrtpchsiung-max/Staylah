"""测试夹具：用 mock_property_data 的真实合成房源拼出上游一次搜索尝试的产物。

不自己编造 Listing，避免夹具和契约悄悄分叉。筛选分组按房源实际字段手工指定，
因为 screen 由模块 C 实现，这里只需要一个符合契约形状的输入。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from property_agent.profiles import from_legacy_user_profile

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "mock_property_data 2" / "output" / "fixtures"

# 档案为整租、CLEMENTI、月租 ≤ SGD 3500、至少两卧。以下分组据此手工核对。
ELIGIBLE_KEYS = [
    "propertyguru:mock-000901",  # 3500，恰好等于预算上限
    "propertyguru:mock-000910",
    "propertyguru:mock-000914",
    "propertyguru:mock-000915",
    "propertyguru:mock-000916",
    "propertyguru:mock-000917",
    "propertyguru:mock-000920",
]
OVER_BUDGET_KEY = "propertyguru:mock-000902"  # 3501，超预算 1 元
OVER_BUDGET_AMOUNT = 3501
UNKNOWN_PRICE_KEY = "propertyguru:mock-000903"  # 价格未知，待核实


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
    """拼出一个 AttemptOutcome。search_status='error' 时不带候选。"""
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
            {"listing_key": key, "checks": _checks("price.amount", "pass", "未超过月租上限")}
            for key in keys
        ],
        "rejected": (
            [
                {
                    "listing_key": OVER_BUDGET_KEY,
                    "checks": _checks("price.amount", "fail", f"{OVER_BUDGET_AMOUNT} 超过月租上限"),
                }
            ]
            if include_over_budget
            else []
        ),
        "needs_verification": (
            [
                {
                    "listing_key": UNKNOWN_PRICE_KEY,
                    "checks": _checks("price.amount", "unknown", "来源未给出金额"),
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
