"""现有 decision Protocol 的 PostgreSQL 实现。"""
from __future__ import annotations

import copy
import hashlib
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select, update
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
    ConversationProfileRow,
    ConversationRow,
    MessageRow,
    ProfileMutationRow,
    RecommendationRow,
    RunQuestionRow,
)

SessionFactory = Callable[[], Session]


def _as_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _profile_values(profile: ConversationProfile) -> dict[str, Any]:
    """把公开合同逐字段映射到 conversation_profiles，不保存不透明 body。"""
    return {
        "profile_id": profile["profile_id"],
        "user_id": profile["user_id"],
        "conversation_id": profile["conversation_id"],
        "version": profile["version"],
        "confirmed_version": profile["confirmed_version"],
        "status": profile["status"],
        "intent": profile["intent"],
        "user_context": profile["user_context"],
        "listing_constraints": profile["listing_constraints"],
        "derived_data_requirements": profile["derived_data_requirements"],
        "open_data_requirements": profile["open_data_requirements"],
        "unresolved": profile["unresolved"],
        "field_sources": profile["field_sources"],
        "created_at": _as_datetime(profile["created_at"]),
        "updated_at": _as_datetime(profile["updated_at"]),
        "last_user_message_at": _as_datetime(profile["last_user_message_at"]),
        "confirmed_at": (
            _as_datetime(profile["confirmed_at"])
            if profile["confirmed_at"] is not None
            else None
        ),
    }


def _profile_from_row(row: ConversationProfileRow) -> ConversationProfile:
    return {
        "profile_id": row.profile_id,
        "user_id": row.user_id,
        "conversation_id": row.conversation_id,
        "version": row.version,
        "confirmed_version": row.confirmed_version,
        "status": row.status,  # type: ignore[typeddict-item]
        "intent": row.intent,  # type: ignore[typeddict-item]
        "user_context": copy.deepcopy(row.user_context),  # type: ignore[typeddict-item]
        "listing_constraints": copy.deepcopy(row.listing_constraints),  # type: ignore[typeddict-item]
        "derived_data_requirements": copy.deepcopy(row.derived_data_requirements),  # type: ignore[typeddict-item]
        "open_data_requirements": copy.deepcopy(row.open_data_requirements),  # type: ignore[typeddict-item]
        "unresolved": copy.deepcopy(row.unresolved),
        "field_sources": copy.deepcopy(row.field_sources),
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "last_user_message_at": row.last_user_message_at.isoformat(),
        "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None,
    }


def _write_profile(row: ConversationProfileRow, profile: ConversationProfile) -> None:
    for field, value in _profile_values(profile).items():
        if field != "profile_id":
            setattr(row, field, value)


def _row_matches_profile(
    row: ConversationProfileRow, profile: ConversationProfile
) -> bool:
    """判断相同版本的保存是否为完整幂等重放。"""

    return all(
        getattr(row, field) == value
        for field, value in _profile_values(profile).items()
    )


class SqlProfileRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self.sessions = sessions

    def put(self, profile: ConversationProfile, *, user_id: str) -> None:
        if profile["user_id"] != user_id:
            raise PermissionError("profile.user_id 与当前用户不一致")
        conversation = pg_insert(ConversationRow).values(
            conversation_id=profile["conversation_id"],
            user_id=user_id,
        )
        conversation = conversation.on_conflict_do_nothing(
            index_elements=["conversation_id"]
        )
        statement = pg_insert(ConversationProfileRow).values(**_profile_values(profile))
        statement = statement.on_conflict_do_nothing(index_elements=["profile_id"])
        with self.sessions.begin() as session:
            session.execute(conversation)
            stored_conversation = session.get(
                ConversationRow, profile["conversation_id"]
            )
            if stored_conversation is None or stored_conversation.user_id != user_id:
                raise PermissionError("conversation 不属于当前用户")
            session.execute(statement)
            stored = session.get(ConversationProfileRow, profile["profile_id"])
            if (
                stored is None
                or stored.user_id != user_id
                or stored.conversation_id != profile["conversation_id"]
            ):
                raise PermissionError("profile 不属于当前用户")

    def save_confirmed(
        self, profile: ConversationProfile, *, user_id: str
    ) -> None:
        """保存 A 确认的画像；允许版本前进，并拒绝陈旧或冲突写入。"""

        if profile["user_id"] != user_id:
            raise PermissionError("profile.user_id 与当前用户不一致")
        if (
            profile["status"] != "confirmed"
            or profile["confirmed_version"] != profile["version"]
            or profile["confirmed_at"] is None
        ):
            raise ValueError("A 只能保存当前版本已经确认的 profile")

        conversation = pg_insert(ConversationRow).values(
            conversation_id=profile["conversation_id"],
            user_id=user_id,
        )
        conversation = conversation.on_conflict_do_nothing(
            index_elements=["conversation_id"]
        )
        # 不指定冲突目标，同时覆盖 profile_id 与 conversation_id 唯一约束；随后
        # 读取并显式验证实际冲突对象，避免把归属冲突静默当成成功。
        insert_profile = pg_insert(ConversationProfileRow).values(
            **_profile_values(profile)
        ).on_conflict_do_nothing()

        with self.sessions.begin() as session:
            session.execute(conversation)
            stored_conversation = session.execute(
                select(ConversationRow)
                .where(ConversationRow.conversation_id == profile["conversation_id"])
                .with_for_update()
            ).scalar_one()
            if stored_conversation.user_id != user_id:
                raise PermissionError("conversation 不属于当前用户")

            session.execute(insert_profile)
            stored = session.execute(
                select(ConversationProfileRow)
                .where(ConversationProfileRow.profile_id == profile["profile_id"])
                .with_for_update()
            ).scalar_one_or_none()
            if stored is None:
                conflicting = session.execute(
                    select(ConversationProfileRow).where(
                        ConversationProfileRow.conversation_id
                        == profile["conversation_id"]
                    )
                ).scalar_one_or_none()
                if conflicting is not None:
                    raise ProfileVersionConflict(
                        "conversation 已绑定到另一个 profile"
                    )
                raise RuntimeError("confirmed profile insert did not persist")
            if (
                stored.user_id != user_id
                or stored.conversation_id != profile["conversation_id"]
            ):
                raise PermissionError("profile 不属于当前用户或 conversation")
            if stored.version > profile["version"]:
                raise ProfileVersionConflict(
                    f"档案已是 v{stored.version}，不能写入旧版本 v{profile['version']}"
                )
            if stored.version == profile["version"]:
                if _row_matches_profile(stored, profile):
                    return
                raise ProfileVersionConflict(
                    f"档案 v{stored.version} 已存在不同内容"
                )
            _write_profile(stored, profile)

    def current_version(self, profile_id: str) -> int:
        with self.sessions() as session:
            row = session.get(ConversationProfileRow, profile_id)
            if row is None:
                raise KeyError(profile_id)
            return row.version

    def get(self, profile_id: str) -> ConversationProfile | None:
        with self.sessions() as session:
            row = session.get(ConversationProfileRow, profile_id)
            return None if row is None else _profile_from_row(row)

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
                return copy.deepcopy(replay.result_profile)  # type: ignore[return-value]

            row = session.execute(
                select(ConversationProfileRow)
                .where(ConversationProfileRow.profile_id == profile_id)
                .with_for_update()
            ).scalar_one()
            replay = session.get(ProfileMutationRow, op_key)
            if replay is not None:
                return copy.deepcopy(replay.result_profile)  # type: ignore[return-value]
            if row.version != base_version:
                raise ProfileVersionConflict(
                    f"档案已是 v{row.version}，提案基于 v{base_version}"
                )
            updated = apply_relaxation(
                _profile_from_row(row),
                proposal=proposal,
                source_message_id=source_message_id,
            )
            updated["updated_at"] = datetime.now(timezone.utc).isoformat()
            _write_profile(row, updated)
            session.add(
                ProfileMutationRow(
                    op_key=op_key,
                    profile_id=profile_id,
                    base_version=base_version,
                    result_version=updated["version"],
                    result_profile=updated,
                )
            )
            return copy.deepcopy(updated)


class SqlRequirementProfileRepository:
    """把 A 的 ``save(profile, user_id=...)`` 边界接到 SQL profile 仓储。"""

    def __init__(self, sessions: SessionFactory) -> None:
        self._profiles = SqlProfileRepository(sessions)

    def save(self, profile: dict[str, Any], *, user_id: str) -> None:
        self._profiles.save_confirmed(profile, user_id=user_id)  # type: ignore[arg-type]

    def get(self, profile_id: str) -> dict[str, Any] | None:
        return self._profiles.get(profile_id)


class SqlRunRepository:
    """上游可用的 run 创建接口，也是 decision 状态的业务投影。"""

    def __init__(self, sessions: SessionFactory) -> None:
        self.sessions = sessions

    def prepare_run(self, *, ctx: RunContext, profile: ConversationProfile) -> None:
        if (
            profile["user_id"] != ctx["user_id"]
            or profile["conversation_id"] != ctx["conversation_id"]
        ):
            raise PermissionError("profile 与当前 run 的用户或 conversation 不匹配")
        conversation = pg_insert(ConversationRow).values(
            conversation_id=ctx["conversation_id"],
            user_id=ctx["user_id"],
        )
        conversation = conversation.on_conflict_do_nothing(
            index_elements=["conversation_id"]
        )
        run = pg_insert(AgentRunRow).values(
            run_id=ctx["run_id"],
            user_id=ctx["user_id"],
            conversation_id=ctx["conversation_id"],
            profile_id=profile["profile_id"],
            profile_version=profile["version"],
            profile_snapshot=profile,
            graph_thread_id=ctx["run_id"],
            status="running",
            state_version=0,
        )
        run = run.on_conflict_do_nothing(index_elements=["run_id"])
        with self.sessions.begin() as session:
            session.execute(conversation)
            stored_conversation = session.get(ConversationRow, ctx["conversation_id"])
            if (
                stored_conversation is None
                or stored_conversation.user_id != ctx["user_id"]
            ):
                raise PermissionError("conversation 不属于当前用户")
            existing_profile = session.get(
                ConversationProfileRow, profile["profile_id"]
            )
            if existing_profile is None:
                session.add(ConversationProfileRow(**_profile_values(profile)))
                session.flush()
            elif (
                existing_profile.user_id != ctx["user_id"]
                or existing_profile.conversation_id != ctx["conversation_id"]
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
                update(AgentRunRow)
                .where(AgentRunRow.run_id == run_id)
                .values(**values)
            )
            if result.rowcount != 1:
                raise KeyError(run_id)

    def find_waiting_run(
        self, conversation_id: str, *, user_id: str
    ) -> dict[str, Any] | None:
        """返回该会话当前等待用户的 run；没有则返回 None。"""

        with self.sessions() as session:
            row = session.execute(
                select(AgentRunRow)
                .where(
                    AgentRunRow.conversation_id == conversation_id,
                    AgentRunRow.user_id == user_id,
                    AgentRunRow.status == "waiting_user",
                )
                .order_by(AgentRunRow.updated_at.desc())
            ).scalars().first()
            if row is None:
                return None
            return {
                "run_id": row.run_id,
                "user_id": row.user_id,
                "conversation_id": row.conversation_id,
                "profile_id": row.profile_id,
                "profile_version": row.profile_version,
                "profile_snapshot": copy.deepcopy(row.profile_snapshot),
                "status": row.status,
                "ctx": None,
            }


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
                question=question,
            )
            statement = statement.on_conflict_do_nothing(
                index_elements=["run_id", "question_id"]
            )
            session.execute(statement)
            message = pg_insert(MessageRow).values(
                message_id=f"assistant:{question['question_id']}",
                conversation_id=run.conversation_id,
                role="assistant",
                text=question["text"],
                client_message_id=None,
            )
            session.execute(
                message.on_conflict_do_nothing(index_elements=["message_id"])
            )
            saved = session.get(RunQuestionRow, (run_id, question["question_id"]))
            assert saved is not None
            if saved.question != question:
                raise ValueError("相同 question_id 对应了不同内容")
            return copy.deepcopy(saved.question)

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
                message_id=message_id,
                conversation_id=run.conversation_id,
                role="user",
                text=answer_text or "",
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

    def ensure_conversation(self, conversation_id: str, *, user_id: str) -> None:
        conversation = pg_insert(ConversationRow).values(
            conversation_id=conversation_id,
            user_id=user_id,
        )
        conversation = conversation.on_conflict_do_nothing(
            index_elements=["conversation_id"]
        )
        with self.sessions.begin() as session:
            session.execute(conversation)
            stored = session.get(ConversationRow, conversation_id)
            if stored is None or stored.user_id != user_id:
                raise PermissionError("conversation 不属于当前用户")

    def set_conversation_title(
        self,
        conversation_id: str,
        *,
        user_id: str,
        title: str,
        overwrite: bool = False,
    ) -> None:
        clean = " ".join(title.split())[:500]
        if not clean:
            return
        conditions = [
            ConversationRow.conversation_id == conversation_id,
            ConversationRow.user_id == user_id,
        ]
        if not overwrite:
            conditions.append(ConversationRow.title.is_(None))
        with self.sessions.begin() as session:
            session.execute(
                update(ConversationRow)
                .where(*conditions)
                .values(title=clean)
            )

    def list_conversations(
        self, *, user_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        last_message = (
            select(
                MessageRow.conversation_id,
                func.max(MessageRow.created_at).label("last_message_at"),
            )
            .group_by(MessageRow.conversation_id)
            .subquery()
        )
        first_user_message = (
            select(
                MessageRow.conversation_id,
                MessageRow.text.label("first_text"),
                func.row_number()
                .over(
                    partition_by=MessageRow.conversation_id,
                    order_by=(MessageRow.created_at, MessageRow.message_id),
                )
                .label("position"),
            )
            .where(MessageRow.role == "user")
            .subquery()
        )
        with self.sessions() as session:
            rows = session.execute(
                select(
                    ConversationRow,
                    last_message.c.last_message_at,
                    first_user_message.c.first_text,
                )
                .outerjoin(
                    last_message,
                    last_message.c.conversation_id == ConversationRow.conversation_id,
                )
                .outerjoin(
                    first_user_message,
                    (first_user_message.c.conversation_id == ConversationRow.conversation_id)
                    & (first_user_message.c.position == 1),
                )
                .where(
                    ConversationRow.user_id == user_id,
                    or_(
                        ConversationRow.title.is_not(None),
                        first_user_message.c.first_text.is_not(None),
                    ),
                )
                .order_by(
                    func.coalesce(
                        last_message.c.last_message_at, ConversationRow.created_at
                    ).desc()
                )
                .limit(limit)
            ).all()
            return [
                {
                    "conversation_id": conversation.conversation_id,
                    "title": conversation.title
                    or " ".join(first_text.split())[:80],
                    "updated_at": (last_at or conversation.created_at).isoformat(),
                }
                for conversation, last_at, first_text in rows
            ]

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
                message_id=message_id,
                conversation_id=conversation_id,
                role=role,
                text=content,
                client_message_id=client_message_id,
            )
            session.execute(
                statement.on_conflict_do_nothing(index_elements=["message_id"])
            )
            stored = session.get(MessageRow, message_id)
            if stored is None:
                raise KeyError(message_id)
            if (
                stored.conversation_id != conversation_id
                or stored.role != role
                or stored.text != content
                or stored.client_message_id != client_message_id
            ):
                raise ValueError("相同 message_id 对应了不同内容")
            return {
                "message_id": stored.message_id,
                "role": stored.role,  # type: ignore[typeddict-item]
                "text": stored.text,
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
                .order_by(MessageRow.created_at, MessageRow.message_id)
            )
            if after:
                current = session.get(MessageRow, after)
                if current is None:
                    return []
                statement = statement.where(
                    (MessageRow.created_at > current.created_at)
                    | (
                        (MessageRow.created_at == current.created_at)
                        & (MessageRow.message_id > current.message_id)
                    )
                )
            rows = session.execute(statement.limit(limit)).scalars().all()
            return [
                {"message_id": row.message_id, "role": row.role, "text": row.text}  # type: ignore[typeddict-item]
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
                recommendation=recommendation,
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
