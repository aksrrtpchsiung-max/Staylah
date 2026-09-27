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
    """Map the public contract field by field to conversation_profiles, without storing an opaque body."""
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
    """Determine whether saving the same version is a complete idempotent replay."""

    return all(
        getattr(row, field) == value
        for field, value in _profile_values(profile).items()
    )


class SqlProfileRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self.sessions = sessions

    def put(self, profile: ConversationProfile, *, user_id: str) -> None:
        if profile["user_id"] != user_id:
            raise PermissionError("profile.user_id does not match the current user")
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
                raise PermissionError("conversation does not belong to the current user")
            session.execute(statement)
            stored = session.get(ConversationProfileRow, profile["profile_id"])
            if (
                stored is None
                or stored.user_id != user_id
                or stored.conversation_id != profile["conversation_id"]
            ):
                raise PermissionError("profile does not belong to the current user")

    def save_confirmed(
        self, profile: ConversationProfile, *, user_id: str
    ) -> None:
        """Save the profile confirmed by A; allow version advancement, and reject stale or conflicting writes."""

        if profile["user_id"] != user_id:
            raise PermissionError("profile.user_id does not match the current user")
        if (
            profile["status"] != "confirmed"
            or profile["confirmed_version"] != profile["version"]
            or profile["confirmed_at"] is None
        ):
            raise ValueError("A can only save a profile already confirmed at the current version")

        conversation = pg_insert(ConversationRow).values(
            conversation_id=profile["conversation_id"],
            user_id=user_id,
        )
        conversation = conversation.on_conflict_do_nothing(
            index_elements=["conversation_id"]
        )
        # Do not specify a conflict target, covering both the profile_id and conversation_id unique constraints; then
        # read and explicitly verify the actual conflicting object, to avoid silently treating an ownership conflict as success.
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
                raise PermissionError("conversation does not belong to the current user")

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
                        "conversation is already bound to another profile"
                    )
                raise RuntimeError("confirmed profile insert did not persist")
            if (
                stored.user_id != user_id
                or stored.conversation_id != profile["conversation_id"]
            ):
                raise PermissionError("profile does not belong to the current user or conversation")
            if stored.version > profile["version"]:
                raise ProfileVersionConflict(
                    f"profile is already v{stored.version}, cannot write old version v{profile['version']}"
                )
            if stored.version == profile["version"]:
                if _row_matches_profile(stored, profile):
                    return
                raise ProfileVersionConflict(
                    f"profile v{stored.version} already exists with different content"
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
                    f"profile is already v{row.version}, but the proposal is based on v{base_version}"
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
    """Connect A's ``save(profile, user_id=...)`` boundary to the SQL profile repository."""

    def __init__(self, sessions: SessionFactory) -> None:
        self._profiles = SqlProfileRepository(sessions)

    def save(self, profile: dict[str, Any], *, user_id: str) -> None:
        self._profiles.save_confirmed(profile, user_id=user_id)  # type: ignore[arg-type]

    def get(self, profile_id: str) -> dict[str, Any] | None:
        return self._profiles.get(profile_id)
