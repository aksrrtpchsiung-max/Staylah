"""Integration boundary test for B→C attempt conversion and persistence-friendly supplementary search."""
from __future__ import annotations

import copy
import unittest
from datetime import datetime, timedelta, timezone
from time import monotonic

from property_agent.domain.requirements import normalize_requirements
from property_agent.search.capabilities.listings import merge_detail
from property_agent.search.aggregation.requirements import build_fulfillment
from property_agent.integration import BCAttemptAdapter, BSearchRunner
from tests.mock_search.pipeline import load_search_fixture
from property_agent.persistence.wiring import build_postgres_deps
from property_agent.results import is_usable, make_issue
from property_agent.requirements.workflow import build_requirement_request
from tests.support import load_profile


def context(profile: dict, run_id: str = "run-search-adapter") -> dict:
    return {
        "user_id": profile["user_id"],
        "run_id": run_id,
        "conversation_id": profile["conversation_id"],
        "attempt_id": "attempt-001",
        "trace_id": f"trace-{run_id}",
        "call_id": f"call-{run_id}",
        "deadline_at": (
            datetime.now(timezone.utc) + timedelta(minutes=5)
        ).isoformat(),
        "source_mode": "mock",
    }


def query(profile: dict) -> dict:
    return {
        "profile_version": profile["version"],
        "entities": [],
        "semantic_query": "confirmed housing requirements",
        "unresolved": [],
    }


def plan(profile: dict, ctx: dict, *, plan_id: str) -> dict:
    requirements = normalize_requirements(profile)
    return {
        "plan_id": plan_id,
        "profile_version": profile["version"],
        "attempt_id": ctx["attempt_id"],
        "intent": profile["intent"],
        "required_filters": requirements["required_filters"],
        "queries": [
            {
                "query_id": "mock-query-001",
                "source": "propertyguru",
                "text": "Clementi",
                "cursor": None,
            }
        ],
        "page_limit": 2,
        "candidate_limit": 50,
        "source_mode": ctx["source_mode"],
        "reason": "test plan",
    }


async def deterministic_retrieve(query_value, listings, *, top_k, ctx):
    candidates = [
        {
            "listing_key": item["listing_key"],
            "exact_matches": [],
            "vector_score": None,
            "keyword_score": None,
            "retrieval_rank": rank,
            "retrieval_score": None,
        }
        for rank, item in enumerate(listings[:top_k], start=1)
    ]
    return {
        "status": "success",
        "data": {
            "profile_version": query_value["profile_version"],
            "candidates": candidates,
            "input_count": len(listings),
            "returned_count": len(candidates),
            "truncated": len(listings) > len(candidates),
            "method_version": "test-deterministic-v1",
        },
        "issues": [],
        "meta": {
            "trace_id": ctx["trace_id"],
            "call_id": ctx["call_id"],
            "duration_ms": 0,
        },
    }


class RecordingPlanner:
    def __init__(self) -> None:
        self.previous_attempts = None
        self.directive = None
        self.ctx = None

    async def build_search_plan(
        self, profile, query_value, previous_attempts, directive, *, ctx
    ):
        self.previous_attempts = copy.deepcopy(previous_attempts)
        self.directive = copy.deepcopy(directive)
        self.ctx = copy.deepcopy(ctx)
        result_plan = plan(profile, ctx, plan_id="continuation-plan")
        change = directive["strategy_changes"][0]
        result_plan["queries"][0]["cursor"] = change.get("cursor")
        return {
            "status": "success",
            "data": result_plan,
            "issues": [],
            "meta": {
                "trace_id": ctx["trace_id"],
                "call_id": ctx["call_id"],
                "duration_ms": 0,
            },
        }


class RecordingSearchService:
    def __init__(self) -> None:
        self.ctx = None
        self.request = None

    async def search_for_request(self, result_plan, request, *, ctx):
        self.ctx = copy.deepcopy(ctx)
        self.request = copy.deepcopy(request)
        result = load_search_fixture("page-2")
        result["data"]["plan_id"] = result_plan["plan_id"]
        result["data"]["profile_version"] = result_plan["profile_version"]
        result["meta"] = {
            "trace_id": ctx["trace_id"],
            "call_id": ctx["call_id"],
            "duration_ms": 0,
        }
        return result


class FakeFulfillmentService:
    def __init__(self, profile: dict) -> None:
        self.profile = profile

    async def run(self, request, *, ctx):
        result_plan = plan(self.profile, ctx, plan_id="initial-plan")
        searched = load_search_fixture("search-success")
        searched["data"]["plan_id"] = result_plan["plan_id"]
        searched["data"]["profile_version"] = result_plan["profile_version"]
        searched["meta"] = {
            "trace_id": ctx["trace_id"],
            "call_id": ctx["call_id"],
            "duration_ms": 0,
        }
        fulfilled = build_fulfillment(
            request,
            searched,
            ctx=ctx,
            started=monotonic(),
            filters=result_plan["required_filters"],
        )
        return {
            "request": request,
            "ctx": ctx,
            "query": query(self.profile),
            "plan": result_plan,
            "search_result": searched,
            "result": fulfilled,
        }


class SearchIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def test_successful_detail_marks_listing_active_and_verified(self):
        listing = copy.deepcopy(load_search_fixture("search-success")["data"]["items"][0])
        listing["listing_status"] = "unknown"
        listing["last_verified_at"] = None
        verified_at = "2026-09-22T12:00:00+00:00"
        detail = {
            "source_listing_id": listing["source_listing_id"],
            "source_url": (
                "https://www.propertyguru.com.sg/listing/"
                + listing["source_listing_id"]
            ),
            "fetched_at": verified_at,
            "raw_description": None,
            "raw_details": [],
            "facts": [
                {
                    "field": "listing_status",
                    "value": "active",
                    "excerpt": "PropertyGuru detail page returned current listing data",
                }
            ],
        }

        merged = merge_detail(listing, detail)

        self.assertEqual(merged["listing_status"], "active")
        self.assertEqual(merged["last_verified_at"], verified_at)
        self.assertTrue(
            any(
                fact["field"] == "listing_status" and fact["value"] == "active"
                for fact in merged["evidence"]
            )
        )

    async def test_initial_b_fulfillment_seeds_decision_attempt_history(self):
        profile = copy.deepcopy(load_profile())
        profile["listing_constraints"] = []
        profile["derived_data_requirements"] = []
        profile["open_data_requirements"] = []
        request = build_requirement_request({"profile": profile})[
            "requirement_request"
        ]
        runner = BSearchRunner(
            bc_adapter=BCAttemptAdapter(retrieve=deterministic_retrieve),
            fulfillment_factory=lambda: FakeFulfillmentService(profile),
        )

        result = await runner.run_initial(
            request,
            profile,
            ctx=context(profile, "run-initial"),
        )

        self.assertTrue(is_usable(result), result)
        self.assertEqual(result["data"]["route"], "decision")
        outcome = result["data"]["outcome"]
        self.assertEqual(outcome["attempt_id"], "run-initial:attempt:001")
        self.assertEqual(
            outcome["attempt_summary"]["attempt_id"],
            "run-initial:attempt:001",
        )
        self.assertTrue(outcome["attempt_summary"]["query_fingerprints"])

    async def test_bc_adapter_routes_b_clarification_back_to_a(self):
        profile = load_profile()
        ctx = context(profile)
        fulfillment = {
            "status": "success",
            "data": {
                "request_id": "requirement-test",
                "profile_version": profile["version"],
                "status": "needs_clarification",
                "search_result": None,
                "coverage": {
                    "fulfilled_requirement_ids": [],
                    "unsupported_requirement_ids": [],
                    "unverified_requirement_ids": [],
                    "skipped_best_effort_requirement_ids": [],
                },
                "clarification_questions": [
                    {"field": "location", "text": "Which location do you mean?"}
                ],
            },
            "issues": [],
            "meta": {
                "trace_id": ctx["trace_id"],
                "call_id": ctx["call_id"],
                "duration_ms": 0,
            },
        }

        result = await BCAttemptAdapter(
            retrieve=deterministic_retrieve
        ).adapt_fulfillment(
            fulfillment,
            profile=profile,
            query=None,
            plan=None,
            search_result=None,
            ctx=ctx,
        )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"]["route"], "clarification")
        self.assertIsNone(result["data"]["outcome"])
        self.assertEqual(
            result["data"]["clarification_questions"][0]["field"], "location"
        )

    async def test_bc_adapter_runs_screen_and_builds_attempt_summary(self):
        profile = load_profile()
        ctx = context(profile)
        result_plan = plan(profile, ctx, plan_id="mock-plan-normal")
        searched = load_search_fixture("search-success")
        searched["meta"] = {
            "trace_id": ctx["trace_id"],
            "call_id": ctx["call_id"],
            "duration_ms": 0,
        }
        adapter = BCAttemptAdapter(retrieve=deterministic_retrieve)

        result = await adapter.adapt(
            searched,
            profile=profile,
            query=query(profile),
            plan=result_plan,
            ctx=ctx,
        )

        self.assertTrue(is_usable(result), result)
        outcome = result["data"]
        self.assertGreaterEqual(len(outcome["screen_result"]["eligible"]), 3)
        self.assertEqual(
            outcome["retrieval_result"]["returned_count"],
            len(outcome["screen_result"]["eligible"]),
        )
        self.assertEqual(outcome["attempt_summary"]["attempt_id"], "attempt-001")
        self.assertTrue(
            outcome["attempt_summary"]["query_fingerprints"][0].startswith(
                "search:v1:"
            )
        )

    async def test_b_search_runner_uses_checkpoint_history_and_new_identity(self):
        profile = copy.deepcopy(load_profile())
        # This test focuses on supplementary search orchestration; removing the investigation requirement avoids turning unrelated unverified items into partial.
        profile["derived_data_requirements"] = []
        profile["open_data_requirements"] = []
        planner = RecordingPlanner()
        search = RecordingSearchService()
        runner = BSearchRunner(
            bc_adapter=BCAttemptAdapter(retrieve=deterministic_retrieve),
            query_preparer=lambda value, *, ctx: _prepared_query(value, ctx),
            planner_factory=lambda: planner,
            search_factory=lambda: search,
        )
        previous = [
            {
                "attempt_id": "attempt-001",
                "query_fingerprints": ["search:v1:{}"],
                "status": "success",
                "eligible_count": 2,
            }
        ]
        directive = {
            "reason_code": "incomplete_coverage",
            "strategy_changes": [
                {
                    "kind": "next_page",
                    "query_id": "mock-query-001",
                    "cursor": "mock-page-2",
                }
            ],
            "base_profile_version": profile["version"],
            "evidence_listing_keys": [],
        }

        result = await runner.run_attempt(
            directive,
            profile,
            previous_attempts=previous,
            ctx=context(profile, "run-continuation"),
        )

        self.assertTrue(is_usable(result), result)
        self.assertEqual(planner.previous_attempts, previous)
        self.assertEqual(planner.ctx["attempt_id"], "run-continuation:attempt:002")
        self.assertEqual(planner.ctx["call_id"], "run-continuation:search:002")
        self.assertEqual(search.ctx, planner.ctx)
        self.assertEqual(
            result["data"]["attempt_summary"]["attempt_id"],
            "run-continuation:attempt:002",
        )
        self.assertEqual(search.request["profile_id"], profile["profile_id"])

    async def test_b_search_runner_returns_a_persistable_failed_attempt(self):
        profile = load_profile()

        async def failed_prepare(value, *, ctx):
            return {
                "status": "error",
                "data": None,
                "issues": [
                    make_issue(
                        "TEMPORARY_UNAVAILABLE",
                        "query preparation unavailable",
                        retryable=True,
                    )
                ],
                "meta": {
                    "trace_id": ctx["trace_id"],
                    "call_id": ctx["call_id"],
                    "duration_ms": 0,
                },
            }

        runner = BSearchRunner(query_preparer=failed_prepare)
        previous = [
            {
                "attempt_id": "attempt-001",
                "query_fingerprints": [],
                "status": "success",
                "eligible_count": 2,
            }
        ]
        result = await runner.run_attempt(
            {
                "reason_code": "incomplete_coverage",
                "strategy_changes": [
                    {
                        "kind": "alternate_source",
                        "source": "propertyguru",
                    }
                ],
                "base_profile_version": profile["version"],
                "evidence_listing_keys": [],
            },
            profile,
            previous_attempts=previous,
            ctx=context(profile, "run-failed-continuation"),
        )

        self.assertTrue(is_usable(result), result)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["data"]["search_status"], "error")
        self.assertEqual(
            result["data"]["attempt_summary"],
            {
                "attempt_id": "run-failed-continuation:attempt:002",
                "query_fingerprints": [],
                "status": "error",
                "eligible_count": 0,
            },
        )

    async def test_postgres_wiring_uses_real_b_search_runner_by_default(self):
        deps = build_postgres_deps(
            sessions=lambda: None,
            use_deepseek=False,
        )
        self.assertIsInstance(deps.search_runner, BSearchRunner)


async def _prepared_query(profile, ctx):
    return {
        "status": "success",
        "data": query(profile),
        "issues": [],
        "meta": {
            "trace_id": ctx["trace_id"],
            "call_id": ctx["call_id"],
            "duration_ms": 0,
        },
    }


if __name__ == "__main__":
    unittest.main()
