"""外层运行控制：把 A 确认、B 履约和 C 决策串成一次会话循环。"""
from __future__ import annotations

import asyncio
import copy
import hashlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Protocol
from uuid import uuid4

from langgraph.types import Command

from property_agent.contracts import ConversationProfile, RequirementRequest, Result, RunContext
from property_agent.decision.graph import initial_state
from property_agent.decision.runtime import thread_config
from property_agent.evaluation_trace import record_event, stage_span
from property_agent.persistence.boundaries import ConversationFavoriteRepository
from property_agent.results import is_usable
from requirement_understanding.response_renderer import ResponseRenderer
from requirement_understanding.workflow import build_requirement_request
from requirement_understanding.workflow_constants import DEFAULT_USER_ID

TurnPhase = Literal[
    "a_dialogue",
    "waiting_user",
    "published",
    "b_clarification",
    "finished",
    "failed",
]


class SearchCoordinator(Protocol):
    async def run_initial(
        self,
        request: RequirementRequest,
        profile: ConversationProfile,
        *,
        ctx: RunContext,
    ) -> Result: ...

    async def run_attempt(
        self,
        directive: Any,
        profile: ConversationProfile,
        *,
        previous_attempts: list[Any],
        ctx: RunContext,
    ) -> Result: ...


class ChatStore(Protocol):
    def ensure_conversation(self, conversation_id: str, *, user_id: str) -> None: ...

    def set_conversation_title(
        self,
        conversation_id: str,
        *,
        user_id: str,
        title: str,
        overwrite: bool = False,
    ) -> None: ...

    def list_conversations(
        self, *, user_id: str, limit: int = 50
    ) -> list[dict[str, Any]]: ...

    def list_messages(
        self, conversation_id: str, *, after: str | None = None, limit: int = 50
    ) -> list[Any]: ...

    def append_message(
        self,
        conversation_id: str,
        *,
        role: str,
        content: str,
        message_id: str,
        client_message_id: str | None = None,
    ) -> Any: ...


class RunStore(Protocol):
    def prepare_run(self, *, ctx: RunContext, profile: ConversationProfile) -> None: ...

    def update(
        self,
        run_id: str,
        *,
        status: str,
        state_version: int | None = None,
        completion_reason: str | None = None,
        final_result_id: str | None = None,
    ) -> None: ...

    def find_waiting_run(
        self, conversation_id: str, *, user_id: str
    ) -> dict[str, Any] | None: ...


class ProfileStore(Protocol):
    def get(self, profile_id: str) -> ConversationProfile | None: ...


@dataclass(frozen=True)
class TurnResult:
    phase: TurnPhase
    assistant_response: str
    conversation_id: str
    user_id: str
    run_id: str | None = None
    status: str | None = None
    pending_question: dict[str, Any] | None = None
    recommendation: dict[str, Any] | None = None
    clarification_questions: list[dict[str, str]] = field(default_factory=list)
    next_run_request: dict[str, Any] | None = None
    requirement_request: dict[str, Any] | None = None
    issues: list[dict[str, Any]] = field(default_factory=list)


class ConversationOrchestrator:
    """产品主循环：串联 A、B 首次履约，以及 C/Decision 的运行与回跳。"""

    def __init__(
        self,
        *,
        a_graph: Any,
        decision_graph: Any,
        search_runner: SearchCoordinator,
        chat: ChatStore,
        runs: RunStore,
        profiles: ProfileStore,
        favorites: ConversationFavoriteRepository | None = None,
        renderer: ResponseRenderer | None = None,
        source_mode: str = "live",
        deadline_seconds: int = 300,
    ) -> None:
        if source_mode not in {"live", "mock"}:
            raise ValueError("source_mode 只能是 live 或 mock")
        self.a_graph = a_graph
        self.decision_graph = decision_graph
        self.search_runner = search_runner
        self.chat = chat
        self.runs = runs
        self.profiles = profiles
        self.favorites = favorites
        self.renderer = renderer or ResponseRenderer()
        self.source_mode = source_mode
        self.deadline_seconds = deadline_seconds

    async def handle_message(
        self,
        text: str,
        *,
        conversation_id: str,
        user_id: str = DEFAULT_USER_ID,
        client_message_id: str | None = None,
        message_id: str | None = None,
    ) -> TurnResult:
        clean = text.strip()
        if not clean:
            raise ValueError("user message must not be empty")
        self.chat.ensure_conversation(conversation_id, user_id=user_id)
        # client_message_id 是客户端重试时保持稳定的幂等键；若同时传入两个 ID，
        # 也优先用它作为消息主键，避免同一客户端消息产生两个业务 turn。
        user_message_id = client_message_id or message_id or f"msg-{uuid4().hex}"
        self.chat.append_message(
            conversation_id,
            role="user",
            content=clean,
            message_id=user_message_id,
            client_message_id=client_message_id or user_message_id,
        )

        config = self._a_config(conversation_id)
        snapshot = await self.a_graph.aget_state(config)
        processed = (snapshot.values or {}).get("processed_turns") or {}
        replay = processed.get(user_message_id)
        if isinstance(replay, dict):
            return TurnResult(**copy.deepcopy(replay))

        waiting = self.runs.find_waiting_run(conversation_id, user_id=user_id)
        if waiting is not None:
            result = await self._resume_decision(
                waiting,
                text=clean,
                client_message_id=client_message_id or user_message_id,
            )
        else:
            result = await self._run_requirement_turn(
                text=clean,
                conversation_id=conversation_id,
                user_id=user_id,
                message_id=user_message_id,
            )

        # C 的 ask_user 已经把 pending_question 写入 messages，这里不再重复。
        if result.assistant_response and result.phase != "waiting_user":
            self.chat.append_message(
                conversation_id,
                role="assistant",
                content=result.assistant_response,
                message_id=f"assistant:{user_message_id}",
                client_message_id=None,
            )
        await self._remember_turn(user_message_id, result)
        return result

    async def _remember_turn(self, message_id: str, result: TurnResult) -> None:
        """把已完成 turn 的结果放进 A checkpoint，供客户端安全重试。"""

        config = self._a_config(result.conversation_id)
        snapshot = await self.a_graph.aget_state(config)
        processed = dict((snapshot.values or {}).get("processed_turns") or {})
        processed[message_id] = asdict(result)
        # checkpoint 不是无限聊天档案；消息正文仍保存在 messages 表中。
        while len(processed) > 100:
            processed.pop(next(iter(processed)))
        await self.a_graph.aupdate_state(config, {"processed_turns": processed})

    def _a_config(self, conversation_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": conversation_id}, "recursion_limit": 20}

    def _build_ctx(
        self, *, conversation_id: str, user_id: str, run_id: str
    ) -> RunContext:
        return {
            "user_id": user_id,
            "run_id": run_id,
            "conversation_id": conversation_id,
            "attempt_id": None,
            "trace_id": f"trace-{run_id}",
            "call_id": f"call-{run_id}",
            "deadline_at": (
                datetime.now(timezone.utc) + timedelta(seconds=self.deadline_seconds)
            ).isoformat(),
            "source_mode": self.source_mode,  # type: ignore[typeddict-item]
        }

    async def _run_requirement_turn(
        self,
        *,
        text: str,
        conversation_id: str,
        user_id: str,
        message_id: str,
    ) -> TurnResult:
        config = self._a_config(conversation_id)
        snapshot = await self.a_graph.aget_state(config)
        payload: dict[str, Any] = {
            "message_id": message_id,
            "current_input": text,
            "user_id": user_id,
            "conversation_id": conversation_id,
        }
        if not snapshot.values:
            payload["status"] = "new"
        with stage_span("A", "requirement_turn"):
            a_state = await self.a_graph.ainvoke(payload, config)
        record_event("a_state", {
            "status": a_state.get("status"),
            "profile": a_state.get("profile"),
            "clarification_questions": a_state.get("clarification_questions"),
            "assistant_response": a_state.get("assistant_response"),
            "requirement_request": a_state.get("requirement_request"),
        })
        return await self._continue_from_a(
            a_state,
            conversation_id=conversation_id,
            user_id=user_id,
        )

    async def _continue_from_a(
        self,
        a_state: dict[str, Any],
        *,
        conversation_id: str,
        user_id: str,
    ) -> TurnResult:
        request = a_state.get("requirement_request")
        started = a_state.get("search_request_id")
        if (
            a_state.get("status") == "ready_for_b"
            and isinstance(request, dict)
            and request.get("request_id")
            and started != request["request_id"]
        ):
            return await self._start_search(
                request,
                a_state.get("profile") or {},
                conversation_id=conversation_id,
                user_id=user_id,
            )
        return TurnResult(
            phase="a_dialogue",
            assistant_response=a_state.get("assistant_response") or "",
            conversation_id=conversation_id,
            user_id=user_id,
            status=a_state.get("status"),
            requirement_request=request,
            clarification_questions=list(a_state.get("clarification_questions") or []),
            issues=list(a_state.get("requirement_issues") or []),
        )

    async def _start_search(
        self,
        request: dict[str, Any],
        profile: dict[str, Any],
        *,
        conversation_id: str,
        user_id: str,
        run_id: str | None = None,
    ) -> TurnResult:
        if run_id is None:
            identity = "\0".join(
                [user_id, conversation_id, str(request.get("request_id") or "")]
            )
            run_id = f"run:{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"
        ctx = self._build_ctx(
            conversation_id=conversation_id, user_id=user_id, run_id=run_id
        )
        handed = await self.search_runner.run_initial(request, profile, ctx=ctx)  # type: ignore[arg-type]
        if not is_usable(handed):
            return TurnResult(
                phase="failed",
                assistant_response=self.renderer.search_failed(handed.get("issues") or []),
                conversation_id=conversation_id,
                user_id=user_id,
                run_id=run_id,
                status="error",
                requirement_request=request,
                issues=list(handed.get("issues") or []),
            )
        transition = handed["data"]
        if transition["route"] == "clarification":
            questions = list(transition.get("clarification_questions") or [])
            reply = self.renderer.clarification(questions)
            await self.a_graph.aupdate_state(
                self._a_config(conversation_id),
                {
                    "status": "awaiting_clarification",
                    "clarification_questions": questions,
                    "assistant_response": reply,
                    "requirement_request": None,
                    "search_request_id": None,
                },
            )
            return TurnResult(
                phase="b_clarification",
                assistant_response=reply,
                conversation_id=conversation_id,
                user_id=user_id,
                run_id=run_id,
                status="awaiting_clarification",
                clarification_questions=questions,
                requirement_request=request,
                issues=list(handed.get("issues") or []),
            )

        outcome = transition["outcome"]
        put = getattr(self.profiles, "put", None)
        if callable(put):
            put(profile, user_id=user_id)
        self.runs.prepare_run(ctx=ctx, profile=profile)  # type: ignore[arg-type]
        try:
            decision_state = await self.decision_graph.ainvoke(
                initial_state(ctx=ctx, profile=profile, outcome=outcome),  # type: ignore[arg-type]
                thread_config(run_id),
            )
        except asyncio.CancelledError:
            self.runs.update(run_id, status="cancelled", completion_reason="cancelled")
            raise
        except Exception:
            # 业务 run 已经创建但图尚未得到可恢复结果；明确结束该 run，
            # 同一 RequirementRequest 仍可由下一条用户消息重试。
            self.runs.update(
                run_id,
                status="failed",
                completion_reason="orchestration_error",
            )
            raise
        result = await self._finish_decision(
            decision_state,
            conversation_id=conversation_id,
            user_id=user_id,
            run_id=run_id,
        )
        # 只有 Decision 已经得到可恢复结果后才把 A 请求标记为已消费。
        # 下游抛错时保留重试机会，避免 ready_for_b 永久卡住。
        if result.phase != "failed":
            await self.a_graph.aupdate_state(
                self._a_config(conversation_id),
                {"search_request_id": request.get("request_id")},
            )
        return result

    async def _resume_decision(
        self,
        waiting: dict[str, Any],
        *,
        text: str,
        client_message_id: str,
    ) -> TurnResult:
        run_id = waiting["run_id"]
        conversation_id = waiting["conversation_id"]
        user_id = waiting["user_id"]
        try:
            decision_state = await self.decision_graph.ainvoke(
                Command(resume={"client_message_id": client_message_id, "text": text}),
                thread_config(run_id),
            )
        except asyncio.CancelledError:
            self.runs.update(run_id, status="cancelled", completion_reason="cancelled")
            raise
        return await self._finish_decision(
            decision_state,
            conversation_id=conversation_id,
            user_id=user_id,
            run_id=run_id,
            source_message_id=client_message_id,
            source_text=text,
        )

    async def _finish_decision(
        self,
        decision_state: dict[str, Any],
        *,
        conversation_id: str,
        user_id: str,
        run_id: str,
        source_message_id: str | None = None,
        source_text: str | None = None,
    ) -> TurnResult:
        if "__interrupt__" in decision_state:
            question = decision_state["__interrupt__"][0].value["pending_question"]
            return TurnResult(
                phase="waiting_user",
                assistant_response=question["text"],
                conversation_id=conversation_id,
                user_id=user_id,
                run_id=run_id,
                status="waiting_user",
                pending_question=question,
            )

        next_run = decision_state.get("next_run_request")
        if next_run:
            return await self._handle_next_run(
                next_run,
                conversation_id=conversation_id,
                user_id=user_id,
                source_message_id=source_message_id,
                source_text=source_text,
            )

        recommendation = decision_state.get("published_recommendation")
        if recommendation:
            text = self.renderer.recommendation(
                _format_recommendation(decision_state)
            )
            return TurnResult(
                phase="published",
                assistant_response=text,
                conversation_id=conversation_id,
                user_id=user_id,
                run_id=run_id,
                status=decision_state.get("status"),
                recommendation=recommendation,
            )
        if decision_state.get("status") == "failed":
            issues = list(decision_state.get("last_issues") or [])
            return TurnResult(
                phase="failed",
                assistant_response=self.renderer.search_failed(issues),
                conversation_id=conversation_id,
                user_id=user_id,
                run_id=run_id,
                status="failed",
                issues=issues,
            )
        return TurnResult(
            phase="finished",
            assistant_response=self.renderer.run_finished(
                decision_state.get("completion_reason")
            ),
            conversation_id=conversation_id,
            user_id=user_id,
            run_id=run_id,
            status=decision_state.get("status"),
            issues=list(decision_state.get("last_issues") or []),
        )

    async def _handle_next_run(
        self,
        next_run: dict[str, Any],
        *,
        conversation_id: str,
        user_id: str,
        source_message_id: str | None,
        source_text: str | None,
    ) -> TurnResult:
        reason = next_run.get("reason_code")
        if reason == "relaxation_accepted":
            profile = self.profiles.get(next_run["profile_id"])
            if profile is None:
                return TurnResult(
                    phase="failed",
                    assistant_response=self.renderer.search_failed(
                        [{"message": "Updated requirements were not found."}]
                    ),
                    conversation_id=conversation_id,
                    user_id=user_id,
                    next_run_request=next_run,
                )
            handoff = build_requirement_request({"profile": profile})
            request = handoff["requirement_request"]
            await self.a_graph.aupdate_state(
                self._a_config(conversation_id),
                {
                    "profile": profile,
                    "status": "ready_for_b",
                    "requirement_request": request,
                    "search_request_id": None,
                    "assistant_response": self.renderer.ready_for_b(),
                },
            )
            return await self._start_search(
                request,
                profile,
                conversation_id=conversation_id,
                user_id=user_id,
            )

        if source_text:
            await self.a_graph.aupdate_state(
                self._a_config(conversation_id),
                {
                    "status": "confirmed",
                    "requirement_request": None,
                    "search_request_id": None,
                },
            )
            with stage_span("A", "return_from_c"):
                a_state = await self.a_graph.ainvoke(
                    {
                        "message_id": source_message_id or f"msg-{uuid4().hex}",
                        "current_input": source_text,
                        "user_id": user_id,
                        "conversation_id": conversation_id,
                    },
                    self._a_config(conversation_id),
                )
            record_event("a_state", {
                "status": a_state.get("status"),
                "profile": a_state.get("profile"),
                "clarification_questions": a_state.get("clarification_questions"),
                "assistant_response": a_state.get("assistant_response"),
                "requirement_request": a_state.get("requirement_request"),
            })
            result = await self._continue_from_a(
                a_state,
                conversation_id=conversation_id,
                user_id=user_id,
            )
            return TurnResult(
                phase=result.phase,
                assistant_response=result.assistant_response,
                conversation_id=result.conversation_id,
                user_id=result.user_id,
                run_id=result.run_id,
                status=result.status,
                pending_question=result.pending_question,
                recommendation=result.recommendation,
                clarification_questions=result.clarification_questions,
                next_run_request=next_run,
                requirement_request=result.requirement_request,
                issues=result.issues,
            )
        return TurnResult(
            phase="a_dialogue",
            assistant_response=self.renderer.run_finished("handed_to_onboarding"),
            conversation_id=conversation_id,
            user_id=user_id,
            next_run_request=next_run,
        )


def _format_recommendation(state: dict[str, Any]) -> str:
    recommendation = state.get("published_recommendation") or {}
    snapshot = state.get("listing_snapshot") or {}
    by_key = {
        item["listing_key"]: item for item in snapshot.get("items") or []
    }
    lines: list[str] = []
    summary = (recommendation.get("summary") or "").strip()
    if summary:
        lines.append(summary)
    for item in recommendation.get("ordered_items") or []:
        listing = by_key.get(item["listing_key"]) or {}
        title = listing.get("title") or item["listing_key"]
        reasons = [
            claim.get("text")
            for claim in item.get("reasons") or []
            if isinstance(claim, dict) and claim.get("text")
        ]
        suffix = f" — {'; '.join(reasons)}" if reasons else ""
        lines.append(f"{item.get('rank', '?')}. {title}{suffix}")
    limitations = recommendation.get("limitations") or []
    if limitations:
        lines.append("Limitations: " + "; ".join(str(item) for item in limitations))
    return "\n".join(lines)
