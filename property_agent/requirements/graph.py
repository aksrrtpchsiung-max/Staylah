"""A-side 输入守卫、需求解析、确认、持久化与 B 请求构造 LangGraph。"""

from collections.abc import Callable
from functools import partial
from typing import Any

from .deepseek_parser import DeepSeekAPIError, DeepSeekConfigurationError, DeepSeekRequirementInterpreter, RequirementInputError, RequirementInterpreter
from .models import IssueCode, RequirementGraphState
from .housing_questions import DeepSeekHousingQuestionAnswerer, HousingQuestionAnswerer
from .response_renderer import ResponseRenderer
from .turn_router import DeepSeekTurnIntentClassifier, TurnIntentClassifier
from .workflow import DeepSeekInputGuard, InMemoryProfileRepository, InputGuard, ProfileRepository, assess_completeness, build_requirement_request, detect_conflicts, generate_confirmation, handle_confirmation, make_merge_profile_node, make_persist_confirmed_profile_node, make_validate_input_node, normalized_requirement_to_patch, recover_error, select_clarification, utc_now, validate_patch


def make_classify_turn_intent_node(
    classifier: TurnIntentClassifier | None = None,
    renderer: ResponseRenderer | None = None,
) -> Callable[[RequirementGraphState], dict[str, Any]]:
    """创建不修改 profile 的 conversation-aware 意图路由节点。"""

    implementation = classifier or DeepSeekTurnIntentClassifier()
    response_renderer = renderer or ResponseRenderer()

    def classify_turn_intent(state: RequirementGraphState) -> dict[str, Any]:
        """把消息路由到需求更新、确认、住房问答或房源 handoff。"""

        try:
            decision = implementation.classify(
                state.get("current_input", ""),
                workflow_status=state.get("status", "new"),
            )
        except DeepSeekConfigurationError as exc:
            return _failure(IssueCode.MODEL_UNAVAILABLE, "turn_intent", str(exc))
        except DeepSeekAPIError as exc:
            return _failure(IssueCode.INVALID_MODEL_OUTPUT, "turn_intent", str(exc))

        update: dict[str, Any] = {"turn_intent": decision.model_dump(mode="json")}
        if decision.intent in {"confirmation", "cancellation"}:
            update["workflow_route"] = "handle_confirmation"
        elif decision.intent == "housing_question":
            update["workflow_route"] = "answer_housing_question"
        elif decision.intent == "listing_request" and not decision.mutates_profile:
            profile = state.get("profile") or {}
            if profile.get("status") == "pending_confirmation":
                update.update(
                    workflow_route="end",
                    assistant_response=(
                        "I can search and recommend specific listings after you confirm your current "
                        "requirements. Please confirm them or tell me what you would like to change."
                    ),
                )
            elif state.get("status") == "ready_for_b":
                update.update(
                    workflow_route="end",
                    assistant_response=(
                        "Your confirmed requirements are ready for the listing retrieval and "
                        "recommendation workflow."
                    ),
                )
            else:
                update["workflow_route"] = "understand_requirement"
        elif decision.intent in {"requirement_update", "listing_request"}:
            update["workflow_route"] = "understand_requirement"
        else:
            update.update(
                workflow_route="end",
                assistant_response=response_renderer.out_of_scope(),
                status="out_of_scope",
            )
        return update

    return classify_turn_intent


def make_answer_housing_question_node(
    answerer: HousingQuestionAnswerer | None = None,
) -> Callable[[RequirementGraphState], dict[str, Any]]:
    """创建只读住房问答节点，禁止生成 profile patch。"""

    implementation = answerer or DeepSeekHousingQuestionAnswerer()

    def answer_housing_question(state: RequirementGraphState) -> dict[str, Any]:
        """调用 A 自有网页搜索工具并保持现有 workflow 状态和 profile version。"""

        decision = state.get("turn_intent") or {}
        try:
            answer = implementation.answer(
                state.get("current_input", ""),
                profile_context=state.get("profile"),
                requires_fresh_data=bool(decision.get("requires_fresh_data")),
            )
        except DeepSeekConfigurationError as exc:
            return _failure(IssueCode.MODEL_UNAVAILABLE, "housing_question", str(exc))
        except DeepSeekAPIError as exc:
            return _failure(IssueCode.MODEL_UNAVAILABLE, "housing_web_search", str(exc))
        response = answer.answer
        profile = state.get("profile") or {}
        if profile.get("status") == "pending_confirmation":
            response += (
                "\n\nYour current housing requirements are still awaiting confirmation. "
                "You can confirm them or tell me what you would like to change."
            )
        return {
            "housing_question_answer": answer.model_dump(mode="json"),
            "assistant_response": response,
            "workflow_route": "end",
        }

    return answer_housing_question


def make_understand_requirement_node(
    interpreter: RequirementInterpreter | None = None,
) -> Callable[[RequirementGraphState], dict[str, Any]]:
    """创建一次 LLM 调用并生成 profile patch 的需求解析节点。"""

    implementation = interpreter or DeepSeekRequirementInterpreter()

    def understand_requirement(state: RequirementGraphState) -> dict[str, Any]:
        """生成标准需求与候选 patch，失败时交给统一错误恢复节点。"""

        try:
            result = implementation.understand(
                state.get("current_input", ""),
                message_id=state.get("message_id", ""),
            )
            proposed_patch = normalized_requirement_to_patch(result.requirement)
        except RequirementInputError as exc:
            return _failure(IssueCode.INVALID_INPUT, "current_input", str(exc))
        except DeepSeekConfigurationError as exc:
            return _failure(IssueCode.MODEL_UNAVAILABLE, "model", str(exc))
        except DeepSeekAPIError as exc:
            return _failure(IssueCode.INVALID_MODEL_OUTPUT, "model_output", str(exc))
        return {
            "normalized_requirement": result.requirement.model_dump(mode="json"),
            "proposed_patch": proposed_patch,
            "parser_metadata": result.metadata.model_dump(mode="json"),
            "requirement_issues": [issue.model_dump(mode="json") for issue in result.issues],
            "workflow_route": "validate_patch",
        }

    return understand_requirement


def _failure(code: IssueCode, field: str, message: str) -> dict[str, Any]:
    """生成不含密钥和堆栈信息的统一失败路由。"""

    return {
        "requirement_issues": [{"code": code.value, "field": field, "message": message}],
        "workflow_route": "recover_error",
        "status": "failed",
    }


def _route(state: RequirementGraphState) -> str:
    """读取节点写入的显式 workflow_route 供条件边选择。"""

    return state.get("workflow_route", "recover_error")


def build_requirement_graph(
    *,
    interpreter: RequirementInterpreter | None = None,
    input_guard: InputGuard | None = None,
    profile_repository: ProfileRepository | None = None,
    response_renderer: ResponseRenderer | None = None,
    turn_intent_classifier: TurnIntentClassifier | None = None,
    housing_question_answerer: HousingQuestionAnswerer | None = None,
    checkpointer: Any = None,
    clock: Callable[[], str] = utc_now,
) -> Any:
    """装配 A-side 完整需求闭环；只构造 B 请求，不调用或处理 B。"""

    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as exc:
        raise RuntimeError("Install the langgraph dependency from requirements.txt first") from exc

    guard = input_guard or DeepSeekInputGuard()
    repository = profile_repository or InMemoryProfileRepository()
    renderer = response_renderer or ResponseRenderer()
    builder = StateGraph(RequirementGraphState)

    builder.add_node("validate_input", make_validate_input_node(guard, renderer))
    builder.add_node(
        "classify_turn_intent",
        make_classify_turn_intent_node(turn_intent_classifier, renderer),
    )
    builder.add_node(
        "answer_housing_question",
        make_answer_housing_question_node(housing_question_answerer),
    )
    builder.add_node("handle_confirmation", partial(handle_confirmation, renderer=renderer))
    builder.add_node("understand_requirement", make_understand_requirement_node(interpreter))
    builder.add_node("validate_patch", validate_patch)
    builder.add_node("detect_conflicts", detect_conflicts)
    builder.add_node("merge_profile", make_merge_profile_node(clock))
    builder.add_node("assess_completeness", assess_completeness)
    builder.add_node("select_clarification", partial(select_clarification, renderer=renderer))
    builder.add_node("generate_confirmation", partial(generate_confirmation, renderer=renderer))
    builder.add_node(
        "persist_confirmed_profile",
        make_persist_confirmed_profile_node(repository, clock),
    )
    builder.add_node("build_requirement_request", partial(build_requirement_request, renderer=renderer))
    builder.add_node("recover_error", partial(recover_error, renderer=renderer))

    builder.add_edge(START, "validate_input")
    builder.add_conditional_edges(
        "validate_input",
        _route,
        {
            "classify_turn_intent": "classify_turn_intent",
            "recover_error": "recover_error",
            "end": END,
        },
    )
    builder.add_conditional_edges(
        "classify_turn_intent",
        _route,
        {
            "handle_confirmation": "handle_confirmation",
            "understand_requirement": "understand_requirement",
            "answer_housing_question": "answer_housing_question",
            "recover_error": "recover_error",
            "end": END,
        },
    )
    builder.add_edge("answer_housing_question", END)
    builder.add_conditional_edges(
        "handle_confirmation",
        _route,
        {
            "persist_confirmed_profile": "persist_confirmed_profile",
            "understand_requirement": "understand_requirement",
            "end": END,
        },
    )
    builder.add_conditional_edges(
        "understand_requirement",
        _route,
        {"validate_patch": "validate_patch", "recover_error": "recover_error"},
    )
    builder.add_conditional_edges(
        "validate_patch",
        _route,
        {"detect_conflicts": "detect_conflicts", "recover_error": "recover_error"},
    )
    builder.add_conditional_edges(
        "detect_conflicts",
        _route,
        {"merge_profile": "merge_profile", "recover_error": "recover_error"},
    )
    builder.add_conditional_edges(
        "merge_profile",
        _route,
        {"assess_completeness": "assess_completeness", "recover_error": "recover_error"},
    )
    builder.add_conditional_edges(
        "assess_completeness",
        _route,
        {
            "select_clarification": "select_clarification",
            "generate_confirmation": "generate_confirmation",
        },
    )
    builder.add_edge("select_clarification", END)
    builder.add_edge("generate_confirmation", END)
    builder.add_conditional_edges(
        "persist_confirmed_profile",
        _route,
        {"build_requirement_request": "build_requirement_request", "recover_error": "recover_error"},
    )
    builder.add_edge("build_requirement_request", END)
    builder.add_edge("recover_error", END)
    return builder.compile(checkpointer=checkpointer)
