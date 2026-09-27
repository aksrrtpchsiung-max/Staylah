"""DecisionFlow behavior for the conversation orchestrator."""
from __future__ import annotations
import asyncio
from typing import Any
from langgraph.types import Command
from property_agent.decision.runtime import thread_config
from .models import TurnResult
from .presentation import _format_recommendation


class DecisionFlow:
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
