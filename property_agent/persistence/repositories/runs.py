"""Runs responsibilities extracted without changing behavior."""
from __future__ import annotations
import copy
from datetime import datetime, timezone
from typing import Any
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from property_agent.contracts import ConversationProfile, RunContext
from property_agent.persistence.models import AgentRunRow, ConversationProfileRow, ConversationRow
from property_agent.persistence.repositories.common import SessionFactory
from property_agent.persistence.repositories.profiles import _profile_values


class SqlRunRepository:
    """The run creation interface available upstream, and also the business projection of the decision state."""

    def __init__(self, sessions: SessionFactory) -> None:
        self.sessions = sessions

    def prepare_run(self, *, ctx: RunContext, profile: ConversationProfile) -> None:
        if (
            profile["user_id"] != ctx["user_id"]
            or profile["conversation_id"] != ctx["conversation_id"]
        ):
            raise PermissionError("profile does not match the user or conversation of the current run")
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
                raise PermissionError("conversation does not belong to the current user")
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
                raise PermissionError("profile ownership or version mismatch")
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
                raise PermissionError("run identity or thread mapping conflict")

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
        """Return the run of this session that is currently waiting for the user; return None if there is none."""

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
