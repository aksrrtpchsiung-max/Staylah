"""现有 decision Protocol 的 PostgreSQL 实现。"""
from __future__ import annotations

import copy
import hashlib
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from property_agent.contracts import (
    ChatMessage,
    ConversationProfile,
    Recommendation,
    RelaxationProposal,
    RunContext,
)
from property_agent.decision.boundaries import ProfileVersionConflict
from property_agent.profiles import apply_relaxation
from property_agent.persistence.models import (
    AgentRunRow,
    ConversationRow,
    MessageRow,
    ProfileMutationRow,
    RecommendationRow,
    RunQuestionRow,
    UserProfileRow,
)

SessionFactory = Callable[[], Session]


class SqlProfileRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self.sessions = sessions

    def put(self, profile: ConversationProfile, *, user_id: str) -> None:
        statement = pg_insert(UserProfileRow).values(
            profile_id=profile["profile_id"],
            user_id=user_id,
            version=profile["version"],
            body=profile,
        )
        statement = statement.on_conflict_do_nothing(index_elements=["profile_id"])
        with self.sessions.begin() as session:
            session.execute(statement)
            stored = session.get(UserProfileRow, profile["profile_id"])
            if stored is None or stored.user_id != user_id:
                raise PermissionError("profile 不属于当前用户")

    def current_version(self, profile_id: str) -> int:
        with self.sessions() as session:
            row = session.get(UserProfileRow, profile_id)
            if row is None:
                raise KeyError(profile_id)
            return row.version

    def apply_relaxation(
        self,
        profile_id: str,
        *,
        base_version: int,
        proposal: RelaxationProposal,
        source_message_id: str,
        op_key: str,
    ) -> ConversationProfile:
        with self.sessions.begin() as session:
            replay = session.get(ProfileMutationRow, op_key)
            if replay is not None:
                return copy.deepcopy(replay.result_body)  # type: ignore[return-value]

            row = session.execute(
                select(UserProfileRow)
                .where(UserProfileRow.profile_id == profile_id)
                .with_for_update()
            ).scalar_one()
            replay = session.get(ProfileMutationRow, op_key)
            if replay is not None:
                return copy.deepcopy(replay.result_body)  # type: ignore[return-value]
            if row.version != base_version:
                raise ProfileVersionConflict(
                    f"档案已是 v{row.version}，提案基于 v{base_version}"
                )
            updated = apply_relaxation(
                row.body,
                proposal=proposal,
                source_message_id=source_message_id,
            )
            row.version = updated["version"]
            row.body = updated
            session.add(
                ProfileMutationRow(
                    op_key=op_key,
                    profile_id=profile_id,
                    base_version=base_version,
                    result_version=updated["version"],
                    result_body=updated,
                )
            )
            return copy.deepcopy(updated)


class SqlRunRepository:
    """上游可用的 run 创建接口，也是 decision 状态的业务投影。"""

    def __init__(self, sessions: SessionFactory) -> None:
        self.sessions = sessions

    def prepare_run(self, *, ctx: RunContext, profile: ConversationProfile) -> None:
        conversation = pg_insert(ConversationRow).values(
            id=ctx["conversation_id"],
            user_id=ctx["user_id"],
        )
        conversation = conversation.on_conflict_do_nothing(index_elements=["id"])
        run = pg_insert(AgentRunRow).values(
            id=ctx["run_id"],
            user_id=ctx["user_id"],
            conversation_id=ctx["conversation_id"],
            profile_id=profile["profile_id"],
            profile_version=profile["version"],
            profile_snapshot=profile,
            graph_thread_id=ctx["run_id"],
            status="running",
            state_version=0,
        )
        run = run.on_conflict_do_nothing(index_elements=["id"])
        with self.sessions.begin() as session:
            session.execute(conversation)
            existing_profile = session.get(UserProfileRow, profile["profile_id"])
            if existing_profile is None:
                session.add(
                    UserProfileRow(
                        profile_id=profile["profile_id"],
                        user_id=ctx["user_id"],
                        version=profile["version"],
                        body=profile,
                    )
                )
                session.flush()
            elif (
                existing_profile.user_id != ctx["user_id"]
                or existing_profile.version != profile["version"]
            ):
                raise PermissionError("profile 归属或版本不匹配")
            session.execute(run)
            stored = session.get(AgentRunRow, ctx["run_id"])
            if (
                stored is None
                or stored.user_id != ctx["user_id"]
                or stored.graph_thread_id != ctx["run_id"]
                or stored.conversation_id != ctx["conversation_id"]
                or stored.profile_id != profile["profile_id"]
                or stored.profile_version != profile["version"]
            ):
                raise PermissionError("run 身份或 thread 映射冲突")

    def update(
        self,
        run_id: str,
        *,
        status: str,
        state_version: int | None = None,
        completion_reason: str | None = None,
        final_result_id: str | None = None,
    ) -> None:
        values: dict[str, Any] = {
            "status": status,
            "completion_reason": completion_reason,
            "updated_at": datetime.now(timezone.utc),
        }
        if state_version is not None:
            values["state_version"] = state_version
        if final_result_id is not None:
            values["final_result_id"] = final_result_id
        with self.sessions.begin() as session:
            result = session.execute(
                update(AgentRunRow).where(AgentRunRow.id == run_id).values(**values)
            )
            if result.rowcount != 1:
                raise KeyError(run_id)


class SqlQuestionRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self.sessions = sessions

    def save_question(self, run_id: str, question: dict) -> dict:
        with self.sessions.begin() as session:
            run = session.get(AgentRunRow, run_id)
            if run is None:
                raise KeyError(f"run 不存在: {run_id}")
            statement = pg_insert(RunQuestionRow).values(
                run_id=run_id,
                question_id=question["question_id"],
                payload=question,
            )
            statement = statement.on_conflict_do_nothing(
                index_elements=["run_id", "question_id"]
            )
            session.execute(statement)
            message = pg_insert(MessageRow).values(
                id=f"assistant:{question['question_id']}",
                conversation_id=run.conversation_id,
                role="assistant",
                content=question["text"],
                client_message_id=None,
            )
            session.execute(message.on_conflict_do_nothing(index_elements=["id"]))
            saved = session.get(RunQuestionRow, (run_id, question["question_id"]))
            assert saved is not None
            if saved.payload != question:
                raise ValueError("相同 question_id 对应了不同内容")
            return copy.deepcopy(saved.payload)

    def mark_answered(
        self,
        run_id: str,
        question_id: str,
        *,
        client_message_id: str | None = None,
        answer_text: str | None = None,
    ) -> bool:
        with self.sessions.begin() as session:
            row = session.execute(
                select(RunQuestionRow)
                .where(
                    RunQuestionRow.run_id == run_id,
                    RunQuestionRow.question_id == question_id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if row is None:
                return False
            if row.answered_at is not None:
                return bool(
                    client_message_id
                    and row.answer_message_id == client_message_id
                    and row.answer_text == answer_text
                )
            run = session.get(AgentRunRow, run_id)
            if run is None:
                return False

            message_id = client_message_id or f"{question_id}:answer"
            message = pg_insert(MessageRow).values(
                id=message_id,
                conversation_id=run.conversation_id,
                role="user",
                content=answer_text or "",
                client_message_id=message_id,
            )
            session.execute(
                message.on_conflict_do_nothing(
                    index_elements=["conversation_id", "client_message_id"]
                )
            )
            row.answered_at = datetime.now(timezone.utc)
            row.answer_message_id = message_id
            row.answer_text = answer_text
            return True


class SqlChatRepository:
    """给 onboarding 预留的聊天读写；追问节点本身只通过 QuestionRepository 写消息。"""

    def __init__(self, sessions: SessionFactory) -> None:
        self.sessions = sessions

    def append_message(
        self,
        conversation_id: str,
        *,
        role: str,
        content: str,
        message_id: str,
        client_message_id: str | None = None,
    ) -> ChatMessage:
        with self.sessions.begin() as session:
            if session.get(ConversationRow, conversation_id) is None:
                raise KeyError(conversation_id)
            statement = pg_insert(MessageRow).values(
                id=message_id,
                conversation_id=conversation_id,
                role=role,
                content=content,
                client_message_id=client_message_id,
            )
            session.execute(statement.on_conflict_do_nothing(index_elements=["id"]))
            stored = session.get(MessageRow, message_id)
            if stored is None:
                raise KeyError(message_id)
            if (
                stored.conversation_id != conversation_id
                or stored.role != role
                or stored.content != content
                or stored.client_message_id != client_message_id
            ):
                raise ValueError("相同 message_id 对应了不同内容")
            return {
                "message_id": stored.id,
                "role": stored.role,  # type: ignore[typeddict-item]
                "text": stored.content,
            }

    def list_messages(
        self,
        conversation_id: str,
        *,
        after: str | None = None,
        limit: int = 50,
    ) -> list[ChatMessage]:
        with self.sessions() as session:
            statement = (
                select(MessageRow)
                .where(MessageRow.conversation_id == conversation_id)
                .order_by(MessageRow.created_at, MessageRow.id)
            )
            if after:
                current = session.get(MessageRow, after)
                if current is None:
                    return []
                statement = statement.where(
                    (MessageRow.created_at > current.created_at)
                    | (
                        (MessageRow.created_at == current.created_at)
                        & (MessageRow.id > current.id)
                    )
                )
            rows = session.execute(statement.limit(limit)).scalars().all()
            return [
                {"message_id": row.id, "role": row.role, "text": row.content}  # type: ignore[typeddict-item]
                for row in rows
            ]


class SqlRecommendationRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self.sessions = sessions

    def save(
        self, run_id: str, recommendation: Recommendation, *, op_key: str
    ) -> tuple[str, bool]:
        final_result_id = f"rec-{run_id}-{_short_hash(op_key)}"
        with self.sessions.begin() as session:
            existing = session.execute(
                select(RecommendationRow).where(
                    RecommendationRow.op_key == op_key
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing.final_result_id, True
            statement = pg_insert(RecommendationRow).values(
                final_result_id=final_result_id,
                run_id=run_id,
                op_key=op_key,
                body=recommendation,
            )
            inserted = session.execute(
                statement.on_conflict_do_nothing().returning(
                    RecommendationRow.final_result_id
                )
            ).scalar_one_or_none()
            if inserted is not None:
                return inserted, False
            existing = session.execute(
                select(RecommendationRow).where(
                    RecommendationRow.op_key == op_key
                )
            ).scalar_one_or_none()
            if existing is None:
                raise ValueError("该 run 已由不同操作发布推荐")
            return existing.final_result_id, True


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
