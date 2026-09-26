"""验证 A 生成的确认请求可以通过 B 的公开入口完成一次离线交接。"""

import asyncio
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from property_agent.search.api import fulfill_requirements
from property_agent.search.fulfillment import FulfillmentService
from property_agent.requirements.workflow import build_requirement_request


class RecordingPlanner:
    """记录 A 请求，并返回不依赖外部模型的合法搜索计划。"""

    def __init__(self) -> None:
        self.request = None

    async def build_for_request(self, request, query, previous_attempts, directive, *, ctx):
        """模拟 B 内部计划阶段，同时保留公开请求用于断言。"""

        self.request = request
        return {
            "status": "success",
            "data": {
                "plan_id": "plan-a-b-integration",
                "profile_version": request["profile_version"],
                "attempt_id": "attempt-a-b-integration",
                "intent": request["intent"],
                "required_filters": {
                    "currency": "SGD",
                    "max_price": 3500,
                    "price_period": "month",
                    "rental_scope": "whole_unit",
                    "locations": ["TAMPINES"],
                    "min_bedrooms": 2,
                },
                "queries": [{
                    "query_id": "query-tampines",
                    "source": "propertyguru",
                    "text": "Tampines",
                    "cursor": None,
                }],
                "page_limit": 1,
                "candidate_limit": 3,
                "source_mode": ctx["source_mode"],
                "reason": "A/B contract integration smoke test",
            },
            "issues": [],
            "meta": {
                "trace_id": ctx["trace_id"],
                "call_id": ctx["call_id"],
                "duration_ms": 0,
            },
        }


class EmptySearchService:
    """返回一次完整空结果，用于只验证通信契约而不伪造房源。"""

    async def search_for_request(self, plan, request, *, ctx):
        """模拟 B 搜索结束，保留真实 Result[SearchResult] 封装。"""

        return {
            "status": "success",
            "data": {
                "plan_id": plan["plan_id"],
                "profile_version": request["profile_version"],
                "items": [],
                "coverage": {
                    "queried_sources": ["propertyguru"],
                    "failed_sources": [],
                    "queries_completed": True,
                    "has_more": False,
                    "next_pages": [],
                    "truncated": False,
                    "applied_filters": [],
                    "unsupported_filters": [],
                },
            },
            "issues": [],
            "meta": {
                "trace_id": ctx["trace_id"],
                "call_id": ctx["call_id"],
                "duration_ms": 0,
            },
        }


def confirmed_profile() -> dict:
    """构造一份由 A 拥有、已确认且可交给 B 的会话画像。"""

    message = "整套租房，月租最多3500新币，淡滨尼，至少两个卧室。"
    source = {
        "message_id": "msg-a-b-001",
        "text": message,
        "start": 0,
        "end": len(message),
    }
    timestamp = "2026-09-21T12:00:00+08:00"
    constraints = [
        ("transaction_type", "eq", "rent"),
        ("price.currency", "eq", "SGD"),
        ("price.amount", "lte", 3500),
        ("price.period", "eq", "month"),
        ("attributes.listing_scope", "eq", "whole_unit"),
        ("bedrooms", "gte", 2),
    ]
    return {
        "profile_id": "profile-a-b-001",
        "user_id": "user-a-b-001",
        "conversation_id": "conversation-a-b-001",
        "version": 1,
        "confirmed_version": 1,
        "status": "confirmed",
        "intent": "rent",
        "user_context": [],
        "listing_constraints": [{
            "constraint_id": f"constraint:{field}",
            "field_path": field,
            "operator": operator,
            "value": value,
            "strength": "hard",
            "priority": "high",
            "source": source,
        } for field, operator, value in constraints],
        "derived_data_requirements": [{
            "requirement_id": "derived:residential-area:tampines",
            "category": "accessibility",
            "target": "Tampines",
            "metric": "residential_area",
            "operator": "eq",
            "value": "in",
            "unit": None,
            "strength": "hard",
            "priority": "high",
            "source": source,
        }],
        "open_data_requirements": [],
        "unresolved": [],
        "field_sources": {"intent": "msg-a-b-001"},
        "created_at": timestamp,
        "updated_at": timestamp,
        "last_user_message_at": timestamp,
        "confirmed_at": timestamp,
    }


class ABCommunicationTests(unittest.TestCase):
    """覆盖 A request builder 到 B fulfillment response 的公开边界。"""

    def test_confirmed_a_request_reaches_b_public_entry(self) -> None:
        """A 的实际请求应由 B 接收，并保留版本、会话和追踪标识。"""

        handoff = build_requirement_request({"profile": confirmed_profile()})
        request = handoff["requirement_request"]
        planner = RecordingPlanner()
        service = FulfillmentService(
            planner=planner,
            search_service=EmptySearchService(),
        )
        ctx = {
            "user_id": "user-a-b-001",
            "run_id": "run-a-b-001",
            "conversation_id": request["conversation_id"],
            "attempt_id": None,
            "trace_id": "trace-a-b-001",
            "call_id": "call-a-b-001",
            "deadline_at": (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat(),
            "source_mode": "mock",
        }

        with patch("property_agent.search.api.create_live_fulfillment_service", return_value=service):
            result = asyncio.run(fulfill_requirements(request, ctx=ctx))

        self.assertEqual(handoff["status"], "ready_for_b")
        self.assertEqual(planner.request, request)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["data"]["status"], "completed")
        self.assertEqual(result["data"]["request_id"], request["request_id"])
        self.assertEqual(result["data"]["profile_version"], request["profile_version"])
        self.assertEqual(result["meta"]["trace_id"], ctx["trace_id"])
        self.assertEqual(result["meta"]["call_id"], ctx["call_id"])


if __name__ == "__main__":
    unittest.main()
