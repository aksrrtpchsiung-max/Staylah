"""Questions responsibilities extracted without changing behavior."""
from __future__ import annotations
import copy
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from property_agent.persistence.models import AgentRunRow, MessageRow, RunQuestionRow
from property_agent.persistence.repositories.common import SessionFactory


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
