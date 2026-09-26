"""Profiles responsibilities extracted without changing behavior."""
from __future__ import annotations
import copy
from datetime import datetime, timezone
from typing import Any
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from property_agent.contracts import ConversationProfile, RelaxationProposal
from property_agent.decision.boundaries import ProfileVersionConflict
from property_agent.profiles import apply_relaxation
from property_agent.persistence.models import ConversationProfileRow, ConversationRow, ProfileMutationRow
from property_agent.persistence.repositories.common import SessionFactory, _as_datetime


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
