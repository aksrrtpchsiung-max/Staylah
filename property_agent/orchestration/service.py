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
from property_agent.requirements.response_renderer import ResponseRenderer
from property_agent.requirements.workflow import build_requirement_request
from property_agent.requirements.workflow_constants import DEFAULT_USER_ID
from .boundaries import SearchCoordinator, ChatStore, RunStore, ProfileStore
from .models import TurnResult, TurnPhase
from .presentation import _format_recommendation
from .requirement_flow import RequirementFlow
from .search_flow import SearchFlow
from .decision_flow import DecisionFlow


class ConversationOrchestrator(RequirementFlow, SearchFlow, DecisionFlow):
    """Own dependencies, message idempotency and the conversation lifecycle."""

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
