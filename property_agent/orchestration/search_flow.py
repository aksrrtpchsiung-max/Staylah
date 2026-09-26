"""SearchFlow behavior for the conversation orchestrator."""
from __future__ import annotations
import asyncio
import hashlib
from typing import Any
from property_agent.decision.graph import initial_state
from property_agent.decision.runtime import thread_config
from property_agent.results import is_usable
from .models import TurnResult


class SearchFlow:
    async def _start_search(
        self,
        request: dict[str, Any],
        profile: dict[str, Any],
        *,
        conversation_id: str,
        user_id: str,
        run_id: str | None = None,
    ) -> TurnResult:
        if run_id is None:
            identity = "\0".join(
                [user_id, conversation_id, str(request.get("request_id") or "")]
            )
            run_id = f"run:{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]}"
        ctx = self._build_ctx(
            conversation_id=conversation_id, user_id=user_id, run_id=run_id
        )
        handed = await self.search_runner.run_initial(request, profile, ctx=ctx)  # type: ignore[arg-type]
        if not is_usable(handed):
            return TurnResult(
                phase="failed",
                assistant_response=self.renderer.search_failed(handed.get("issues") or []),
                conversation_id=conversation_id,
                user_id=user_id,
                run_id=run_id,
                status="error",
                requirement_request=request,
                issues=list(handed.get("issues") or []),
            )
        transition = handed["data"]
        if transition["route"] == "clarification":
            questions = list(transition.get("clarification_questions") or [])
            reply = self.renderer.clarification(questions)
            await self.a_graph.aupdate_state(
                self._a_config(conversation_id),
                {
                    "status": "awaiting_clarification",
                    "clarification_questions": questions,
                    "assistant_response": reply,
                    "requirement_request": None,
                    "search_request_id": None,
                },
            )
            return TurnResult(
                phase="b_clarification",
                assistant_response=reply,
                conversation_id=conversation_id,
                user_id=user_id,
                run_id=run_id,
                status="awaiting_clarification",
                clarification_questions=questions,
                requirement_request=request,
                issues=list(handed.get("issues") or []),
            )

        outcome = transition["outcome"]
        put = getattr(self.profiles, "put", None)
        if callable(put):
            put(profile, user_id=user_id)
        self.runs.prepare_run(ctx=ctx, profile=profile)  # type: ignore[arg-type]
        try:
            decision_state = await self.decision_graph.ainvoke(
                initial_state(ctx=ctx, profile=profile, outcome=outcome),  # type: ignore[arg-type]
                thread_config(run_id),
            )
        except asyncio.CancelledError:
            self.runs.update(run_id, status="cancelled", completion_reason="cancelled")
            raise
        except Exception:
            # 业务 run 已经创建但图尚未得到可恢复结果；明确结束该 run，
            # 同一 RequirementRequest 仍可由下一条用户消息重试。
            self.runs.update(
                run_id,
                status="failed",
                completion_reason="orchestration_error",
            )
            raise
        result = await self._finish_decision(
            decision_state,
            conversation_id=conversation_id,
            user_id=user_id,
            run_id=run_id,
        )
        # 只有 Decision 已经得到可恢复结果后才把 A 请求标记为已消费。
        # 下游抛错时保留重试机会，避免 ready_for_b 永久卡住。
        if result.phase != "failed":
            await self.a_graph.aupdate_state(
                self._a_config(conversation_id),
                {"search_request_id": request.get("request_id")},
            )
        return result
