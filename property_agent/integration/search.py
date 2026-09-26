"""把 B 的搜索产物转换为 C/Decision 可消费的 attempt，并执行可恢复补搜。"""
from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable, Sequence
from time import monotonic
from typing import Any, Literal, Protocol, TypedDict

from property_agent.evaluation import service as part_c

from property_agent.search.execution.history import query_fingerprint
from property_agent.search.aggregation.requirements import build_fulfillment
from property_agent.contracts import (
    AttemptSummary,
    Clarification,
    ContractViolation,
    ConversationProfile,
    Issue,
    QueryFeatures,
    RequirementCoverage,
    RequirementFulfillment,
    RequirementRequest,
    Result,
    RunContext,
    ScreenResult,
    SearchDirective,
    SearchPlan,
    SearchResult,
)
from property_agent.decision.boundaries import AttemptOutcome
from property_agent.evaluation_trace import record_event, stage_span
from property_agent.results import CallTimer, is_usable, make_issue


class Planner(Protocol):
    async def build_search_plan(
        self,
        profile: ConversationProfile,
        query: QueryFeatures,
        previous_attempts: list[AttemptSummary],
        directive: SearchDirective | None,
        *,
        ctx: RunContext,
    ) -> Result: ...


class SearchService(Protocol):
    async def search_for_request(
        self, plan: SearchPlan, request: RequirementRequest, *, ctx: RunContext
    ) -> Result: ...


class FulfillmentService(Protocol):
    async def run(
        self, request: RequirementRequest, *, ctx: RunContext
    ) -> dict[str, Any]: ...


class BCTransition(TypedDict):
    """首次 B 履约之后交给外层编排的明确路线。"""

    route: Literal["decision", "clarification"]
    outcome: AttemptOutcome | None
    clarification_questions: list[Clarification]
    requirement_coverage: RequirementCoverage


QueryPreparer = Callable[
    [ConversationProfile], Awaitable[Result]
]  # 实际调用通过闭包绑定 ctx；仅供类型说明。
RetrieveFunction = Callable[..., Awaitable[Result]]


def _copy_error(result: Result, timer: CallTimer) -> Result:
    issues = copy.deepcopy(result.get("issues") or [])
    if not issues:
        issues = [make_issue("INTERNAL_ERROR", "上游返回 error 但没有说明原因")]
    return timer.error(*issues)


def _failed_attempt(
    timer: CallTimer,
    ctx: RunContext,
    issues: Sequence[Issue],
    *,
    plan: SearchPlan | None = None,
) -> Result:
    """把一次已发生的补搜失败保存成可 checkpoint 的 attempt outcome。"""

    problems = copy.deepcopy(list(issues))
    if not problems:
        problems = [make_issue("INTERNAL_ERROR", "补搜失败但没有说明原因")]
    attempt_id = ctx["attempt_id"]
    if attempt_id is None:
        return timer.error(*problems)
    summary: AttemptSummary = {
        "attempt_id": attempt_id,
        "query_fingerprints": (
            [query_fingerprint(plan, item) for item in plan["queries"]]
            if plan is not None
            else []
        ),
        "status": "error",
        "eligible_count": 0,
    }
    outcome: AttemptOutcome = {
        "attempt_id": attempt_id,
        "search_status": "error",
        "failure_code": problems[0]["code"],
        "listing_snapshot": None,
        "screen_result": None,
        "retrieval_result": None,
        "coverage": None,
        "requirement_coverage": None,
        "attempt_summary": summary,
    }
    return timer.partial(outcome, problems)


def _requirement_request(profile: ConversationProfile) -> RequirementRequest:
    """从本 run 固定的 confirmed profile 重建 B 调查所需的最小请求。"""

    if (
        profile["status"] != "confirmed"
        or profile["confirmed_version"] != profile["version"]
        or profile["confirmed_at"] is None
        or profile["intent"] is None
    ):
        raise ContractViolation(
            "INVALID_STATE", "profile", "补搜只能使用当前已经确认的 profile"
        )
    return {
        "request_id": (
            f"requirement-{profile['conversation_id']}-v{profile['version']}"
        ),
        "schema_version": "0.3-draft",
        "conversation_id": profile["conversation_id"],
        "profile_id": profile["profile_id"],
        "profile_version": profile["version"],
        "intent": profile["intent"],
        "user_context": copy.deepcopy(profile["user_context"]),
        "listing_constraints": copy.deepcopy(profile["listing_constraints"]),
        "derived_data_requirements": copy.deepcopy(
            profile["derived_data_requirements"]
        ),
        "open_data_requirements": copy.deepcopy(profile["open_data_requirements"]),
        "unresolved_fields": list(profile["unresolved"]),
        "confirmed_at": profile["confirmed_at"],
    }


class BCAttemptAdapter:
    """把 B 返回的候选直接交给 C.retrieve，并构造 Decision attempt。"""

    def __init__(
        self,
        *,
        retrieve: RetrieveFunction = part_c.retrieve,
        top_k: int = 12,
    ) -> None:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        self._retrieve = retrieve
        # B → C 最多移交 12 套；retrieve 只负责给这批候选打分和排序。
        self._top_k = min(top_k, part_c.MAX_EVALUATION_CANDIDATES)

    async def adapt(
        self,
        search_result: Result,
        *,
        profile: ConversationProfile,
        query: QueryFeatures,
        plan: SearchPlan,
        ctx: RunContext,
        requirement_coverage: RequirementCoverage | None = None,
        additional_issues: Sequence[Issue] = (),
    ) -> Result:
        """把一次 B 搜索和 C 的检索结果封装为单次 attempt。"""

        timer = CallTimer(ctx)
        if not is_usable(search_result):
            return _copy_error(search_result, timer)
        try:
            data: SearchResult = search_result["data"]
            if (
                data["profile_version"] != profile["version"]
                or plan["profile_version"] != profile["version"]
                or query["profile_version"] != profile["version"]
            ):
                raise ContractViolation(
                    "STATE_CONFLICT",
                    "profile_version",
                    "B、C 与 profile 必须使用同一版本",
                )
            if data["plan_id"] != plan["plan_id"]:
                raise ContractViolation(
                    "STATE_CONFLICT", "search_result.plan_id", "搜索结果不属于当前计划"
                )

            searched_listings = copy.deepcopy(data["items"])
            record_event("b_search_result", {
                "plan_id": data["plan_id"],
                "coverage": data["coverage"],
                "count": len(searched_listings),
                "listings": searched_listings,
            }, attempt_id=plan["attempt_id"])
            # 正常配置下 B 已受 candidate_limit=12 约束。这里再做一次边界保护，
            # 避免测试替身或自定义 SearchService 把 12 套以上送进 retrieve 的 LLM。
            listings: list[dict[str, Any]] = []
            seen_listing_keys: set[str] = set()
            for listing in searched_listings:
                key = listing["listing_key"]
                if key in seen_listing_keys:
                    continue
                seen_listing_keys.add(key)
                listings.append(listing)
                if len(listings) == self._top_k:
                    break
            # v0 contract 仍要求 ScreenResult。这里仅保留 B 的候选身份，不运行
            # C.screen，也不把空 checks 解释为 C 已验证硬条件。
            candidate_keys = [item["listing_key"] for item in listings]
            screened: ScreenResult = {
                "profile_version": profile["version"],
                "eligible": [{"listing_key": key, "checks": []} for key in candidate_keys],
                "rejected": [],
                "needs_verification": [],
            }
            record_event("b_candidate_handoff", {
                "candidate_count": len(candidate_keys),
                "candidate_listings": listings,
            }, attempt_id=plan["attempt_id"])
            with stage_span("C", "retrieve", attempt_id=plan["attempt_id"]):
                retrieval = await self._retrieve(
                    query, listings, top_k=self._top_k, ctx=ctx
                )
            record_event("c_retrieval_result", retrieval, attempt_id=plan["attempt_id"])
            if not is_usable(retrieval):
                return _copy_error(retrieval, timer)

            issues = [
                *copy.deepcopy(search_result.get("issues") or []),
                *copy.deepcopy(retrieval.get("issues") or []),
                *copy.deepcopy(list(additional_issues)),
            ]
            status = "partial" if issues else "success"
            summary: AttemptSummary = {
                "attempt_id": plan["attempt_id"],
                "query_fingerprints": [
                    query_fingerprint(plan, item) for item in plan["queries"]
                ],
                "status": status,
                "eligible_count": len(candidate_keys),
            }
            attempt_coverage = copy.deepcopy(data["coverage"])
            if len(seen_listing_keys) < len({item["listing_key"] for item in searched_listings}):
                attempt_coverage["truncated"] = True
            outcome: AttemptOutcome = {
                "attempt_id": plan["attempt_id"],
                "search_status": status,
                "failure_code": None,
                "listing_snapshot": {
                    "snapshot_id": f"snapshot-{plan['plan_id']}-{plan['attempt_id']}",
                    "profile_version": profile["version"],
                    "items": listings,
                },
                "screen_result": screened,
                "retrieval_result": retrieval["data"],
                "coverage": attempt_coverage,
                "requirement_coverage": copy.deepcopy(requirement_coverage),
                "attempt_summary": summary,
            }
            return timer.partial(outcome, issues) if issues else timer.ok(outcome)
        except ContractViolation as exc:
            return timer.error(
                make_issue(exc.code, str(exc), field_path=exc.field_path, source="bc_adapter")
            )
        except (KeyError, TypeError, ValueError) as exc:
            return timer.error(
                make_issue(
                    "INVALID_OUTPUT",
                    f"B→C 产物无法转换：{type(exc).__name__}",
                    source="bc_adapter",
                )
            )

    async def adapt_fulfillment(
        self,
        fulfillment: Result,
        *,
        profile: ConversationProfile,
        query: QueryFeatures | None,
        plan: SearchPlan | None,
        search_result: Result | None,
        ctx: RunContext,
    ) -> Result:
        """把 B 的公开履约响应路由到 A 澄清或 C/Decision。"""

        timer = CallTimer(ctx)
        if not is_usable(fulfillment):
            return _copy_error(fulfillment, timer)
        data: RequirementFulfillment = fulfillment["data"]
        coverage = copy.deepcopy(data["coverage"])
        if data["status"] == "needs_clarification":
            transition: BCTransition = {
                "route": "clarification",
                "outcome": None,
                "clarification_questions": copy.deepcopy(
                    data["clarification_questions"]
                ),
                "requirement_coverage": coverage,
            }
            issues = copy.deepcopy(fulfillment.get("issues") or [])
            return timer.partial(transition, issues) if issues else timer.ok(transition)
        if query is None or plan is None or search_result is None:
            return timer.error(
                make_issue(
                    "INVALID_STATE",
                    "B 已完成履约，但缺少 query、plan 或原始 search result",
                    field_path="fulfillment",
                    source="bc_adapter",
                )
            )
        search_for_c: Result = {
            "status": fulfillment["status"],
            "data": copy.deepcopy(data["search_result"]),
            "issues": copy.deepcopy(fulfillment.get("issues") or []),
            "meta": copy.deepcopy(fulfillment["meta"]),
        }
        attempted = await self.adapt(
            search_for_c,
            profile=profile,
            query=query,
            plan=plan,
            ctx=ctx,
            requirement_coverage=coverage,
        )
        if not is_usable(attempted):
            return attempted
        transition = {
            "route": "decision",
            "outcome": attempted["data"],
            "clarification_questions": [],
            "requirement_coverage": coverage,
        }
        return (
            timer.partial(transition, attempted["issues"])
            if attempted["issues"]
            else timer.ok(transition)
        )


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
