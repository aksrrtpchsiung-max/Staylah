"""LLM 直接输出标准化需求链路的契约与 LangGraph 测试。"""

import json
import os
import unittest
from unittest.mock import patch

import httpx

from requirement_understanding import (
    DeepSeekRequirementInterpreter,
    FALCON_SCOPE_MESSAGE,
    InMemoryProfileRepository,
    InputGuardDecision,
    build_requirement_graph,
)
from requirement_understanding.workflow import validate_patch
from requirement_understanding.housing_questions import HousingQuestionAnswer, HousingSearchResult
from requirement_understanding.turn_router import TurnIntentDecision


class AlwaysHousingGuard:
    """在测试中把非空输入判定为住房相关，避免额外模型调用。"""

    def check(self, text: str, *, workflow_status: str) -> InputGuardDecision:
        """返回可预测的输入范围判断。"""

        valid = bool(text.strip())
        return InputGuardDecision(valid=valid, housing_related=valid)


class OutOfScopeGuard:
    """在测试中固定返回业务范围外判断。"""

    def check(self, text: str, *, workflow_status: str) -> InputGuardDecision:
        """保持输入结构有效但标记为非住房问题。"""

        return InputGuardDecision(valid=True, housing_related=False)


class FailingProfileRepository:
    """模拟数据库写入失败以验证统一 recover_error 路径。"""

    def save(self, profile: dict) -> None:
        """固定抛出持久化异常。"""

        raise RuntimeError("database unavailable")


class TestTurnIntentClassifier:
    """为测试提供确定性 turn intent，避免增加模型调用。"""

    def classify(self, text: str, *, workflow_status: str) -> TurnIntentDecision:
        """识别确认、住房问题和普通需求更新。"""

        normalized = text.strip().lower()
        if normalized in {"确认", "confirm"}:
            intent = "confirmation"
        elif normalized == "what is the average rental in singapore":
            intent = "housing_question"
        elif normalized == "show me available listings":
            intent = "listing_request"
        else:
            intent = "requirement_update"
        return TurnIntentDecision(
            intent=intent,
            requires_fresh_data=intent == "housing_question",
            mutates_profile=intent == "requirement_update",
        )


class RecordingHousingQuestionAnswerer:
    """记录住房问答调用并返回固定带来源答案。"""

    def __init__(self) -> None:
        """初始化调用记录。"""

        self.calls: list[dict] = []

    def answer(
        self,
        question: str,
        *,
        profile_context: dict | None,
        requires_fresh_data: bool,
    ) -> HousingQuestionAnswer:
        """返回不会修改 profile 的固定答案。"""

        self.calls.append({
            "question": question,
            "profile_context": profile_context,
            "requires_fresh_data": requires_fresh_data,
        })
        return HousingQuestionAnswer(
            answer="Average rent depends on property type, location, and unit size.",
            as_of="2026-09-20T00:00:00+00:00",
            sources=[HousingSearchResult(
                title="Singapore rental statistics",
                url="https://example.com/rent",
                snippet="Rental statistics by property type.",
            )],
        )


def requested_sentence_output() -> dict:
    """返回指定 NUS 租房句子的标准化模型输出 fixture。"""

    return {
        "intent": {
            "value": "rent",
            "strength": "hard",
            "source_text": "月租",
        },
        "budget": {
            "currency": "SGD",
            "max_price": 1800,
            "period": "month",
            "approximate": False,
            "strength": "hard",
            "source_text": "一个月月租1800新以下",
        },
        "rental_scope": None,
        "locations": [
            {
                "raw_name": "nus学校",
                "relation": "near",
                "resolution_status": "unresolved",
                "strength": "hard",
                "source_text": "nus学校附近",
            }
        ],
        "bedrooms": None,
        "property_types": [],
        "commute": [],
        "preferences": [
            {
                "topic": "near_bus_stop",
                "value": True,
                "priority": "high",
                "strength": "hard",
                "source_text": "近公交站",
            },
            {
                "topic": "ensuite_bathroom",
                "value": True,
                "priority": "high",
                "strength": "hard",
                "source_text": "有独立卫浴",
            },
        ],
        "unresolved_fields": ["rental_scope"],
    }


def complete_sentence_output() -> dict:
    """返回补齐整租范围、可以进入确认节点的模型输出 fixture。"""

    output = requested_sentence_output()
    output["user_context"] = [{
        "field": "household.occupant_count",
        "value": 2,
        "source_text": "两个人",
    }]
    output["rental_scope"] = {
        "value": "whole_unit",
        "strength": "hard",
        "source_text": "整租",
    }
    output["unresolved_fields"] = []
    return output


def mock_deepseek_client(output: dict) -> httpx.Client:
    """创建校验模型参数并返回指定 JSON 内容的本地 HTTP 客户端。"""

    def handler(request: httpx.Request) -> httpx.Response:
        """模拟一次 DeepSeek Chat Completions JSON 响应。"""

        payload = json.loads(request.content)
        assert payload["model"] == "deepseek-v4-flash"
        assert payload["response_format"] == {"type": "json_object"}
        assert "JSON Schema" in payload["messages"][1]["content"]
        assert "test-only" not in request.content.decode()
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "request-test-001",
                "model": "deepseek-flash",
                "choices": [{"message": {"content": json.dumps(output)}}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 80,
                    "total_tokens": 180,
                },
            },
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def mock_routed_deepseek_client(outputs: dict[str, dict]) -> httpx.Client:
    """按用户原文为多轮 workflow 返回不同的结构化模型输出。"""

    def handler(request: httpx.Request) -> httpx.Response:
        """从 schema prompt 中定位用户输入并返回匹配 fixture。"""

        payload = json.loads(request.content)
        prompt = payload["messages"][1]["content"]
        output = next(value for text, value in outputs.items() if f"User input: {text}\n" in prompt)
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "request-test-routed",
                "model": "deepseek-flash",
                "choices": [{"message": {"content": json.dumps(output)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 80, "total_tokens": 180},
            },
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


class RequirementUnderstandingTests(unittest.TestCase):
    """验证直接结构化生成、来源核验、错误状态和 checkpoint。"""

    def test_llm_directly_returns_normalized_requirement(self) -> None:
        """指定句子应一次得到标准金额、地点关系和稳定偏好主题。"""

        text = "住在nus学校附近，近公交站，有独立卫浴，一个月月租1800新以下"
        with mock_deepseek_client(requested_sentence_output()) as client:
            interpreter = DeepSeekRequirementInterpreter(api_key="test-only", client=client)
            result = interpreter.understand(text, message_id="msg-001")
        requirement = result.requirement

        self.assertEqual(requirement.intent.value.value, "rent")
        self.assertEqual(requirement.budget.max_price, 1800)
        self.assertEqual(requirement.budget.period.value, "month")
        self.assertEqual(requirement.locations[0].raw_name, "nus学校")
        self.assertEqual(requirement.locations[0].relation.value, "near")
        self.assertEqual(requirement.locations[0].resolution_status, "unresolved")
        self.assertEqual(
            [preference.topic.value for preference in requirement.preferences],
            ["near_bus_stop", "ensuite_bathroom"],
        )
        self.assertEqual(requirement.unresolved_fields, ["rental_scope"])
        self.assertEqual(result.issues, [])

    def test_graph_builds_profile_and_asks_for_missing_scope(self) -> None:
        """完整图应生成 draft profile，并优先追问缺失的租赁范围。"""

        text = "住在nus学校附近，近公交站，有独立卫浴，一个月月租1800新以下"
        with mock_deepseek_client(requested_sentence_output()) as client:
            interpreter = DeepSeekRequirementInterpreter(api_key="test-only", client=client)
            graph = build_requirement_graph(
                interpreter=interpreter,
                input_guard=AlwaysHousingGuard(),
                turn_intent_classifier=TestTurnIntentClassifier(),
            )
            state = graph.invoke({
                "message_id": "msg-002",
                "current_input": text,
                "user_id": "user-001",
                "conversation_id": "conversation-001",
                "status": "new",
            })

        self.assertEqual(state["status"], "awaiting_clarification")
        self.assertIn("normalized_requirement", state)
        self.assertEqual(state["profile"]["version"], 1)
        self.assertEqual(state["profile"]["listing_constraints"][0]["field_path"], "price.amount")
        self.assertEqual(
            state["clarification_questions"][0]["field"],
            "listing_constraints.attributes.listing_scope",
        )

    def test_confirmation_persists_profile_and_builds_request(self) -> None:
        """同一 thread_id 的确认应持久化版本并生成给 B 的请求。"""

        from langgraph.checkpoint.memory import InMemorySaver

        text = "两个人整租，住在nus学校附近，近公交站，有独立卫浴，一个月月租1800新以下"
        repository = InMemoryProfileRepository()
        with mock_deepseek_client(complete_sentence_output()) as client:
            interpreter = DeepSeekRequirementInterpreter(api_key="test-only", client=client)
            graph = build_requirement_graph(
                interpreter=interpreter,
                input_guard=AlwaysHousingGuard(),
                turn_intent_classifier=TestTurnIntentClassifier(),
                profile_repository=repository,
                checkpointer=InMemorySaver(),
                clock=lambda: "2026-09-19T10:00:00+00:00",
            )
            config = {"configurable": {"thread_id": "thread-test-001"}}
            first = graph.invoke({
                "message_id": "msg-003",
                "current_input": text,
                "user_id": "user-001",
                "conversation_id": "thread-test-001",
                "status": "new",
            }, config)
            state = graph.invoke({"message_id": "msg-004", "current_input": "确认"}, config)
            checkpoint = graph.get_state(config)

        self.assertEqual(first["status"], "awaiting_confirmation")
        self.assertEqual(first["profile"]["user_context"][0]["value"], 2)
        self.assertNotIn(" lte ", first["assistant_response"])
        self.assertNotIn("accessibility", first["assistant_response"])
        self.assertEqual(state["status"], "ready_for_b")
        self.assertEqual(state["requirement_request"]["profile_version"], 1)
        self.assertEqual(checkpoint.values["profile"]["confirmed_version"], 1)
        self.assertIsNotNone(repository.get("profile-thread-test-001"))

    def test_housing_question_is_read_only_while_confirmation_is_pending(self) -> None:
        """住房市场问题应调用 A 搜索问答，并保持待确认 profile 的版本不变。"""

        from langgraph.checkpoint.memory import InMemorySaver

        text = "两个人整租，住在nus学校附近，近公交站，有独立卫浴，一个月月租1800新以下"
        answerer = RecordingHousingQuestionAnswerer()
        with mock_deepseek_client(complete_sentence_output()) as client:
            graph = build_requirement_graph(
                interpreter=DeepSeekRequirementInterpreter(api_key="test-only", client=client),
                input_guard=AlwaysHousingGuard(),
                turn_intent_classifier=TestTurnIntentClassifier(),
                housing_question_answerer=answerer,
                checkpointer=InMemorySaver(),
                clock=lambda: "2026-09-20T10:00:00+00:00",
            )
            config = {"configurable": {"thread_id": "thread-housing-question"}}
            draft = graph.invoke({
                "message_id": "msg-040",
                "current_input": text,
                "user_id": "user-001",
                "conversation_id": "thread-housing-question",
                "status": "new",
            }, config)
            question_state = graph.invoke({
                "message_id": "msg-041",
                "current_input": "what is the average rental in singapore",
            }, config)
            listing_state = graph.invoke({
                "message_id": "msg-042",
                "current_input": "show me available listings",
            }, config)

        self.assertEqual(draft["profile"]["version"], 1)
        self.assertEqual(question_state["profile"]["version"], 1)
        self.assertEqual(question_state["confirmation"]["profile_version"], 1)
        self.assertEqual(question_state["status"], "awaiting_confirmation")
        self.assertEqual(question_state["turn_intent"]["intent"], "housing_question")
        self.assertIn("Average rent depends", question_state["assistant_response"])
        self.assertIn("still awaiting confirmation", question_state["assistant_response"])
        self.assertEqual(len(answerer.calls), 1)
        self.assertTrue(answerer.calls[0]["requires_fresh_data"])
        self.assertEqual(listing_state["profile"]["version"], 1)
        self.assertEqual(listing_state["turn_intent"]["intent"], "listing_request")
        self.assertIn("after you confirm", listing_state["assistant_response"])
        self.assertEqual(len(answerer.calls), 1)

    def test_non_verbatim_source_is_dropped(self) -> None:
        """LLM 改写的 source_text 不能成为标准需求的证据。"""

        output = requested_sentence_output()
        output["preferences"][0]["source_text"] = "靠近公共汽车站"
        text = "住在nus学校附近，近公交站，有独立卫浴，一个月月租1800新以下"
        with mock_deepseek_client(output) as client:
            interpreter = DeepSeekRequirementInterpreter(api_key="test-only", client=client)
            result = interpreter.understand(text, message_id="msg-004")

        self.assertEqual(
            [preference.topic.value for preference in result.requirement.preferences],
            ["ensuite_bathroom"],
        )
        self.assertEqual(result.issues[0].code.value, "unsupported_source")

    def test_correction_directly_replaces_existing_budget_constraint(self) -> None:
        """等待确认时的新预算应替换旧 constraint，并生成新的确认版本。"""

        from langgraph.checkpoint.memory import InMemorySaver

        first_text = "两个人整租，住在nus学校附近，近公交站，有独立卫浴，一个月月租1800新以下"
        correction_text = "预算改成一个月月租2000新以下"
        correction_output = {
            "intent": None,
            "budget": {
                "currency": "SGD",
                "max_price": 2000,
                "period": "month",
                "approximate": False,
                "strength": "hard",
                "source_text": "一个月月租2000新以下",
            },
            "rental_scope": None,
            "locations": [],
            "bedrooms": None,
            "property_types": [],
            "commute": [],
            "preferences": [],
            "unresolved_fields": [],
        }
        with mock_routed_deepseek_client({
            first_text: complete_sentence_output(),
            correction_text: correction_output,
        }) as client:
            graph = build_requirement_graph(
                interpreter=DeepSeekRequirementInterpreter(api_key="test-only", client=client),
                input_guard=AlwaysHousingGuard(),
                turn_intent_classifier=TestTurnIntentClassifier(),
                checkpointer=InMemorySaver(),
                clock=lambda: "2026-09-19T10:00:00+00:00",
            )
            config = {"configurable": {"thread_id": "thread-correction"}}
            graph.invoke({
                "message_id": "msg-010",
                "current_input": first_text,
                "user_id": "user-001",
                "conversation_id": "thread-correction",
                "status": "new",
            }, config)
            state = graph.invoke({"message_id": "msg-011", "current_input": correction_text}, config)

        budgets = [
            item for item in state["profile"]["listing_constraints"]
            if item["field_path"] == "price.amount"
        ]
        self.assertEqual(state["status"], "awaiting_confirmation")
        self.assertEqual(state["profile"]["version"], 2)
        self.assertEqual(len(budgets), 1)
        self.assertEqual(budgets[0]["value"], 2000)
        self.assertEqual(state["confirmation"]["profile_version"], 2)

    def test_persistence_failure_is_recovered_without_b_request(self) -> None:
        """confirmed profile 写入失败时应安全失败且不得生成 B 请求。"""

        from langgraph.checkpoint.memory import InMemorySaver

        text = "两个人整租，住在nus学校附近，近公交站，有独立卫浴，一个月月租1800新以下"
        with mock_deepseek_client(complete_sentence_output()) as client:
            graph = build_requirement_graph(
                interpreter=DeepSeekRequirementInterpreter(api_key="test-only", client=client),
                input_guard=AlwaysHousingGuard(),
                turn_intent_classifier=TestTurnIntentClassifier(),
                profile_repository=FailingProfileRepository(),
                checkpointer=InMemorySaver(),
                clock=lambda: "2026-09-19T10:00:00+00:00",
            )
            config = {"configurable": {"thread_id": "thread-persistence-error"}}
            graph.invoke({
                "message_id": "msg-012",
                "current_input": text,
                "user_id": "user-001",
                "conversation_id": "thread-persistence-error",
                "status": "new",
            }, config)
            state = graph.invoke({"message_id": "msg-013", "current_input": "确认"}, config)

        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["requirement_issues"][0]["code"], "persistence_error")
        self.assertNotIn("requirement_request", state)

    def test_missing_api_key_becomes_failed_graph_state(self) -> None:
        """没有本地 API Key 时应返回安全失败状态而不是尝试网络调用。"""

        with patch.dict(os.environ, {}, clear=True):
            graph = build_requirement_graph(
                input_guard=AlwaysHousingGuard(),
                turn_intent_classifier=TestTurnIntentClassifier(),
            )
            state = graph.invoke({
                "message_id": "msg-005",
                "current_input": "月租1800",
                "user_id": "user-001",
                "conversation_id": "conversation-005",
                "status": "new",
            })

        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["requirement_issues"][0]["code"], "model_unavailable")
        self.assertIn("try again later", state["assistant_response"])
        self.assertNotIn("normalized_requirement", state)

    def test_second_turn_missing_hints_do_not_erase_accumulated_profile(self) -> None:
        """第二轮只补充房型时，不应重新追问首轮已保存的意图、预算和地点。"""

        first_text = (
            "My girlfriend and I work at Clementi and Marina Bay with respect. "
            "rental should be aroun 2000, and it should be convenient to commute, "
            "with tranquilt environment"
        )
        second_text = "Whole unit, 1bed, prefer condo, but i can accept hdb"
        first_output = {
            "intent": {"value": "rent", "strength": "hard", "source_text": "rental"},
            "user_context": [
                {
                    "field": "occupant.workplace",
                    "value": "Clementi",
                    "source_text": "Clementi",
                },
                {
                    "field": "occupant.workplace",
                    "value": "Marina Bay",
                    "source_text": "Marina Bay",
                },
            ],
            "budget": {
                "currency": "SGD",
                "max_price": 2000,
                "period": "month",
                "approximate": True,
                "strength": "soft",
                "source_text": "aroun 2000",
            },
            "rental_scope": None,
            "locations": [],
            "bedrooms": None,
            "property_types": [],
            "commute": [
                {
                    "destination": "Clementi",
                    "destination_type": "workplace",
                    "travel_mode": "unknown",
                    "max_minutes": None,
                    "strength": "soft",
                    "source_text": "Clementi",
                },
                {
                    "destination": "Marina Bay",
                    "destination_type": "workplace",
                    "travel_mode": "unknown",
                    "max_minutes": None,
                    "strength": "soft",
                    "source_text": "Marina Bay",
                },
                {
                    "destination": "unknown",
                    "destination_type": "unknown",
                    "travel_mode": "unknown",
                    "max_minutes": None,
                    "strength": "soft",
                    "source_text": "convenient to commute",
                },
            ],
            "preferences": [
                {
                    "topic": "quietness",
                    "value": True,
                    "priority": "medium",
                    "strength": "soft",
                    "source_text": "tranquilt environment",
                },
                {
                    "topic": "other",
                    "value": "convenient commute",
                    "priority": "medium",
                    "strength": "soft",
                    "source_text": "convenient to commute",
                },
            ],
            "unresolved_fields": ["rental_scope", "bedrooms", "property_types"],
        }
        second_output = {
            "intent": None,
            "user_context": [],
            "budget": None,
            "rental_scope": {
                "value": "whole_unit",
                "strength": "hard",
                "source_text": "Whole unit",
            },
            "locations": [],
            "bedrooms": {
                "operator": "eq",
                "value": 1,
                "strength": "hard",
                "source_text": "1bed",
            },
            "property_types": [
                {"value": "condo", "strength": "soft", "source_text": "condo"},
                {"value": "hdb", "strength": "soft", "source_text": "hdb"},
            ],
            "commute": [],
            "preferences": [],
            "unresolved_fields": ["intent", "budget", "locations"],
        }
        with mock_routed_deepseek_client({
            first_text: first_output,
            second_text: second_output,
        }) as client:
            from langgraph.checkpoint.memory import InMemorySaver

            graph = build_requirement_graph(
                interpreter=DeepSeekRequirementInterpreter(api_key="test-only", client=client),
                input_guard=AlwaysHousingGuard(),
                turn_intent_classifier=TestTurnIntentClassifier(),
                checkpointer=InMemorySaver(),
            )
            config = {"configurable": {"thread_id": "thread-accumulated-profile"}}
            first = graph.invoke({
                "message_id": "msg-020",
                "current_input": first_text,
                "user_id": "user-001",
                "conversation_id": "thread-accumulated-profile",
                "status": "new",
            }, config)
            second = graph.invoke({
                "message_id": "msg-021",
                "current_input": second_text,
            }, config)

        self.assertEqual(first["profile"]["unresolved"], [
            "listing_constraints.attributes.listing_scope",
        ])
        self.assertEqual(second["status"], "awaiting_confirmation")
        self.assertEqual(second["profile"]["unresolved"], [])
        self.assertEqual(second["profile"]["intent"], "rent")
        fields = {item["field_path"]: item for item in second["profile"]["listing_constraints"]}
        self.assertEqual(fields["price.amount"]["value"], 2000)
        self.assertEqual(fields["attributes.listing_scope"]["value"], "whole_unit")
        self.assertEqual(fields["bedrooms"]["value"], 1)
        targets = {item.get("target") for item in second["profile"]["derived_data_requirements"]}
        self.assertTrue({"Clementi", "Marina Bay"}.issubset(targets))
        self.assertNotIn("unknown", targets)
        self.assertEqual(second["profile"]["open_data_requirements"], [])
        workplaces = {
            item["value"] for item in second["profile"]["user_context"]
            if item["field"] == "occupant.workplace"
        }
        self.assertEqual(workplaces, {"Clementi", "Marina Bay"})

    def test_unmapped_preference_becomes_non_blocking_open_requirement(self) -> None:
        """无法映射的住房偏好应进入 best-effort 兜底，并随确认请求交给 B。"""

        from langgraph.checkpoint.memory import InMemorySaver

        text = (
            "两个人整租，住在nus学校附近，近公交站，有独立卫浴，"
            "一个月月租1800新以下，附近有网球场"
        )
        output = complete_sentence_output()
        output["preferences"].append({
            "topic": "other",
            "value": "nearby tennis court",
            "priority": "medium",
            "strength": "soft",
            "source_text": "附近有网球场",
        })
        with mock_deepseek_client(output) as client:
            graph = build_requirement_graph(
                interpreter=DeepSeekRequirementInterpreter(api_key="test-only", client=client),
                input_guard=AlwaysHousingGuard(),
                turn_intent_classifier=TestTurnIntentClassifier(),
                checkpointer=InMemorySaver(),
                clock=lambda: "2026-09-19T10:00:00+00:00",
            )
            config = {"configurable": {"thread_id": "thread-open-requirement"}}
            draft = graph.invoke({
                "message_id": "msg-030",
                "current_input": text,
                "user_id": "user-001",
                "conversation_id": "thread-open-requirement",
                "status": "new",
            }, config)
            confirmed = graph.invoke({
                "message_id": "msg-031",
                "current_input": "confirm",
            }, config)

        open_requirements = draft["profile"]["open_data_requirements"]
        self.assertEqual(len(open_requirements), 1)
        self.assertEqual(open_requirements[0]["description"], "附近有网球场")
        self.assertEqual(open_requirements[0]["handling"], "best_effort")
        self.assertEqual(confirmed["status"], "ready_for_b")
        self.assertEqual(
            confirmed["requirement_request"]["open_data_requirements"],
            open_requirements,
        )
        self.assertEqual(confirmed["requirement_request"]["schema_version"], "0.3-draft")

    def test_empty_input_returns_fixed_scope_message(self) -> None:
        """空白用户输入应在模型调用前返回固定 Falcon 提示。"""

        graph = build_requirement_graph(input_guard=AlwaysHousingGuard())
        state = graph.invoke({
            "message_id": "msg-006",
            "current_input": "   ",
            "user_id": "user-001",
            "conversation_id": "conversation-006",
            "status": "new",
        })

        self.assertEqual(state["status"], "out_of_scope")
        self.assertEqual(state["assistant_response"], FALCON_SCOPE_MESSAGE)

    def test_unrelated_input_returns_exact_fixed_message(self) -> None:
        """与找房无关的输入不得调用需求解析器或更新 profile。"""

        graph = build_requirement_graph(input_guard=OutOfScopeGuard())
        state = graph.invoke({
            "message_id": "msg-007",
            "current_input": "帮我写一首歌",
            "user_id": "user-001",
            "conversation_id": "conversation-007",
            "status": "new",
        })

        self.assertEqual(state["status"], "out_of_scope")
        self.assertEqual(state["assistant_response"], FALCON_SCOPE_MESSAGE)
        self.assertNotIn("profile", state)

    def test_validate_patch_rejects_non_queryable_listing_field(self) -> None:
        """patch 校验节点应拒绝用户需求不允许约束的 Listing 元数据。"""

        text = "我想指定抓取时间"
        result = validate_patch({
            "message_id": "msg-008",
            "current_input": text,
            "proposed_patch": [{
                "operation": "append",
                "field": "listing_constraints",
                "value": {
                    "constraint_id": "bad",
                    "field_path": "fetched_at",
                    "operator": "eq",
                    "value": "today",
                    "strength": "hard",
                    "priority": "high",
                    "source": {
                        "message_id": "msg-008",
                        "text": text,
                        "start": 0,
                        "end": len(text),
                    },
                },
                "source_message_id": "msg-008",
            }],
        })

        self.assertEqual(result["workflow_route"], "recover_error")
        self.assertEqual(result["requirement_issues"][0]["code"], "invalid_patch")


if __name__ == "__main__":
    unittest.main()
