"""Runner responsibilities extracted without changing behavior."""
from __future__ import annotations
import copy
from collections.abc import Awaitable, Callable
from time import monotonic
from property_agent.search.aggregation.requirements import build_fulfillment
from property_agent.contracts import AttemptSummary, Clarification, ContractViolation, ConversationProfile, QueryFeatures, RequirementRequest, Result, RunContext, SearchDirective, SearchPlan
from property_agent.evaluation_trace import stage_span
from property_agent.results import CallTimer, is_usable, make_issue
from property_agent.integration.attempts import BCAttemptAdapter, _failed_attempt, _requirement_request
from property_agent.integration.boundaries import FulfillmentService, Planner, SearchService


class BSearchRunner:
    """无 run 内存状态的正式补搜实现；历史完全由 Decision state 传入。"""

    def __init__(
        self,
        *,
        bc_adapter: BCAttemptAdapter | None = None,
        query_preparer: Callable[..., Awaitable[Result]] | None = None,
        planner_factory: Callable[[], Planner] | None = None,
        search_factory: Callable[[], SearchService] | None = None,
        fulfillment_factory: Callable[[], FulfillmentService] | None = None,
    ) -> None:
        self._bc = bc_adapter or BCAttemptAdapter()
        self._query_preparer = query_preparer
        self._planner_factory = planner_factory
        self._search_factory = search_factory
        self._fulfillment_factory = fulfillment_factory

    @staticmethod
    def _attempt_context(
        ctx: RunContext, previous_attempts: list[AttemptSummary]
    ) -> RunContext:
        number = len(previous_attempts) + 1
        used = {item["attempt_id"] for item in previous_attempts}
        while f"{ctx['run_id']}:attempt:{number:03d}" in used:
            number += 1
        updated = copy.deepcopy(ctx)
        updated["attempt_id"] = f"{ctx['run_id']}:attempt:{number:03d}"
        updated["call_id"] = f"{ctx['run_id']}:search:{number:03d}"
        return updated

    @staticmethod
    async def _default_prepare(
        profile: ConversationProfile, *, ctx: RunContext
    ) -> Result:
        from property_agent.search.api import prepare_query

        return await prepare_query(profile, ctx=ctx)

    @staticmethod
    def _default_planner() -> Planner:
        from property_agent.search.api import create_live_planner_service

        return create_live_planner_service()

    @staticmethod
    def _default_search() -> SearchService:
        from property_agent.search.api import create_live_search_service

        return create_live_search_service()

    @staticmethod
    def _default_fulfillment() -> FulfillmentService:
        from property_agent.search.api import create_live_fulfillment_service

        return create_live_fulfillment_service()

    async def run_initial(
        self,
        request: RequirementRequest,
        profile: ConversationProfile,
        *,
        ctx: RunContext,
    ) -> Result:
        """执行首次 A→B 履约并生成可作为 Decision 初始输入的转换结果。"""

        attempt_ctx = self._attempt_context(ctx, [])
        timer = CallTimer(attempt_ctx)
        try:
            if (
                request["profile_id"] != profile["profile_id"]
                or request["profile_version"] != profile["version"]
                or request["conversation_id"] != profile["conversation_id"]
            ):
                raise ContractViolation(
                    "STATE_CONFLICT", "request", "A 请求与 confirmed profile 不一致"
                )
            service = (
                self._fulfillment_factory or self._default_fulfillment
            )()
            with stage_span("B", "initial_search", attempt_id=attempt_ctx["attempt_id"]):
                state = await service.run(request, ctx=attempt_ctx)
            result = state.get("result")
            if not isinstance(result, dict):
                return timer.error(
                    make_issue(
                        "INVALID_OUTPUT",
                        "B fulfillment state 缺少 result",
                        source="b_search_runner",
                    )
                )
            return await self._bc.adapt_fulfillment(
                result,
                profile=profile,
                query=state.get("query"),
                plan=state.get("plan"),
                search_result=state.get("search_result"),
                ctx=attempt_ctx,
            )
        except ContractViolation as exc:
            return timer.error(
                make_issue(
                    exc.code,
                    str(exc),
                    field_path=exc.field_path,
                    source="b_search_runner",
                )
            )
        except Exception as exc:
            return timer.error(
                make_issue(
                    "INTERNAL_ERROR",
                    f"首次搜索适配失败：{type(exc).__name__}",
                    source="b_search_runner",
                )
            )

    async def run_attempt(
        self,
        directive: SearchDirective,
        profile: ConversationProfile,
        *,
        previous_attempts: list[AttemptSummary],
        ctx: RunContext,
    ) -> Result:
        attempt_ctx = self._attempt_context(ctx, previous_attempts)
        timer = CallTimer(attempt_ctx)
        started = monotonic()
        plan: SearchPlan | None = None
        try:
            prepare = self._query_preparer or self._default_prepare
            with stage_span("B", "prepare_query", attempt_id=attempt_ctx["attempt_id"]):
                prepared = await prepare(profile, ctx=attempt_ctx)
            if not is_usable(prepared):
                return _failed_attempt(
                    timer, attempt_ctx, prepared.get("issues") or []
                )
            query: QueryFeatures = prepared["data"]

            planner = (
                self._planner_factory or self._default_planner
            )()
            with stage_span("B", "build_search_plan", attempt_id=attempt_ctx["attempt_id"]):
                planned = await planner.build_search_plan(
                    profile,
                    query,
                    copy.deepcopy(previous_attempts),
                    copy.deepcopy(directive),
                    ctx=attempt_ctx,
                )
            if not is_usable(planned):
                return _failed_attempt(
                    timer, attempt_ctx, planned.get("issues") or []
                )
            plan = planned["data"]

            request = _requirement_request(profile)
            service = (self._search_factory or self._default_search)()
            with stage_span("B", "search_for_request", attempt_id=attempt_ctx["attempt_id"]):
                searched = await service.search_for_request(
                    plan, request, ctx=attempt_ctx
                )
            if not is_usable(searched):
                return _failed_attempt(
                    timer,
                    attempt_ctx,
                    searched.get("issues") or [],
                    plan=plan,
                )

            fulfillment = build_fulfillment(
                request,
                searched,
                ctx=attempt_ctx,
                started=started,
                filters=plan["required_filters"],
            )
            if not is_usable(fulfillment):
                return _failed_attempt(
                    timer,
                    attempt_ctx,
                    fulfillment.get("issues") or [],
                    plan=plan,
                )
            fulfillment_data = fulfillment["data"]
            if fulfillment_data["status"] == "needs_clarification":
                questions: list[Clarification] = fulfillment_data[
                    "clarification_questions"
                ]
                return timer.error(
                    make_issue(
                        "INVALID_STATE",
                        "B 补搜仍需澄清，必须返回 A："
                        + "; ".join(item["text"] for item in questions),
                        field_path="fulfillment.clarification_questions",
                        source="b_search_runner",
                    )
                )
            search_for_c: Result = {
                "status": fulfillment["status"],
                "data": fulfillment_data["search_result"],
                "issues": copy.deepcopy(fulfillment.get("issues") or []),
                "meta": copy.deepcopy(fulfillment["meta"]),
            }
            attempted = await self._bc.adapt(
                search_for_c,
                profile=profile,
                query=query,
                plan=plan,
                ctx=attempt_ctx,
                requirement_coverage=fulfillment_data["coverage"],
            )
            if not is_usable(attempted):
                return _failed_attempt(
                    timer,
                    attempt_ctx,
                    attempted.get("issues") or [],
                    plan=plan,
                )
            return attempted
        except ContractViolation as exc:
            return _failed_attempt(
                timer,
                attempt_ctx,
                [make_issue(
                    exc.code,
                    str(exc),
                    field_path=exc.field_path,
                    source="b_search_runner",
                )],
                plan=plan,
            )
        except Exception as exc:
            return _failed_attempt(
                timer,
                attempt_ctx,
                [make_issue(
                    "INTERNAL_ERROR",
                    f"补搜适配失败：{type(exc).__name__}",
                    source="b_search_runner",
                )],
                plan=plan,
            )
