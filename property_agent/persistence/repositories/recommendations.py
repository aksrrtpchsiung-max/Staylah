"""Recommendations responsibilities extracted without changing behavior."""
from __future__ import annotations
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from property_agent.contracts import Recommendation
from property_agent.persistence.models import RecommendationRow
from property_agent.persistence.repositories.common import SessionFactory, _short_hash


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
                raise ValueError("This run has already had recommendations published by a different operation")
            return existing.final_result_id, True
