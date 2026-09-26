"""RequirementFlow behavior for the conversation orchestrator."""
from __future__ import annotations
from typing import Any
from uuid import uuid4
from property_agent.evaluation_trace import record_event, stage_span
from property_agent.requirements.workflow import build_requirement_request
from .models import TurnResult


class RequirementFlow:
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
