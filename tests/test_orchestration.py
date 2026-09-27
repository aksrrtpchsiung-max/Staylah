"""Outer orchestration: A confirmation -> B fulfillment -> C decision, plus the clarification / next_run_request closed loop."""
from __future__ import annotations

import copy
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from langgraph.checkpoint.memory import InMemorySaver

from property_agent.decision import build_decision_graph, build_stub_deps
from property_agent.orchestration import ConversationOrchestrator, InMemoryChatRepository
from property_agent.results import CallTimer
from property_agent.requirements import (
    DeepSeekRequirementInterpreter,
    InMemoryProfileRepository,
    build_requirement_graph,
)
from tests.support import build_outcome
from tests.test_requirement_understanding import (
    AlwaysHousingGuard,
    TestTurnIntentClassifier,
    complete_sentence_output,
    mock_deepseek_client,
)

REQUIREMENT = "Two people renting a whole unit, living near NUS campus, close to a bus stop, with a private bathroom, monthly rent below 1800 SGD"


def _ok(data: dict[str, Any], ctx: dict[str, Any] | None = None) -> dict[str, Any]:
    meta = {
        "trace_id": (ctx or {}).get("trace_id", "trace"),
        "call_id": (ctx or {}).get("call_id", "call"),
        "duration_ms": 0,
    }
    return {"status": "success", "data": data, "issues": [], "meta": meta}


def _stamp_outcome(outcome: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    version = profile["version"]
    stamped = copy.deepcopy(outcome)
    if stamped.get("listing_snapshot"):
        stamped["listing_snapshot"]["profile_version"] = version
    if stamped.get("screen_result"):
        stamped["screen_result"]["profile_version"] = version
    if stamped.get("retrieval_result"):
        stamped["retrieval_result"]["profile_version"] = version
    return stamped


class FakeBSearchRunner:
    def __init__(self, initials: list[Any]) -> None:
        self._initials = list(initials)
        self.requests: list[dict[str, Any]] = []

    async def run_initial(self, request, profile, *, ctx):
        self.requests.append(copy.deepcopy(request))
        item = self._initials.pop(0)
        if callable(item):
            item = item(request, profile, ctx)
        if item.get("meta") is None and ctx is not None:
            item = {**item, "meta": CallTimer(ctx).meta()}
        return item

    async def run_attempt(self, directive, profile, *, previous_attempts, ctx):
        raise AssertionError("research should not reach FakeB.run_attempt")


class FailedDecisionGraph:
    async def ainvoke(self, state, config):
        return {
            "status": "failed",
            "completion_reason": "service_failure",
            "last_issues": [{"code": "MODEL_UNAVAILABLE", "message": "C unavailable"}],
        }


class ExplodingDecisionGraph:
    async def ainvoke(self, state, config):
        raise RuntimeError("decision crashed")


def _decision_handoff(profile, ctx, **outcome_kwargs):
    outcome = _stamp_outcome(build_outcome(**outcome_kwargs), profile)
    outcome["attempt_id"] = f"{ctx['run_id']}:attempt:001"
    if outcome.get("attempt_summary"):
        outcome["attempt_summary"]["attempt_id"] = outcome["attempt_id"]
    return _ok(
        {
            "route": "decision",
            "outcome": outcome,
            "clarification_questions": [],
            "requirement_coverage": {
                "fulfilled_requirement_ids": [],
                "unsupported_requirement_ids": [],
                "unverified_requirement_ids": [],
                "skipped_best_effort_requirement_ids": [],
            },
        },
        ctx,
    )


class OrchestrationTests(unittest.IsolatedAsyncioTestCase):
    def _build_a_graph(self, interpreter, repository=None):
        return build_requirement_graph(
            interpreter=interpreter,
            input_guard=AlwaysHousingGuard(),
            turn_intent_classifier=TestTurnIntentClassifier(),
            profile_repository=repository or InMemoryProfileRepository(),
            checkpointer=InMemorySaver(),
            clock=lambda: "2026-09-22T10:00:00+00:00",
        )

    def _build_orchestrator(
        self, interpreter, fake_b, repository=None, decision_graph=None
    ):
        deps = build_stub_deps()
        return ConversationOrchestrator(
            a_graph=self._build_a_graph(interpreter, repository),
            decision_graph=(
                decision_graph
                if decision_graph is not None
                else build_decision_graph(deps).compile(checkpointer=InMemorySaver())
            ),
            search_runner=fake_b,
            chat=InMemoryChatRepository(),
            runs=deps.runs,
            profiles=deps.profiles,
            source_mode="live",
            deadline_seconds=120,
        )

    async def _confirm(self, orchestrator, conversation_id="conversation-orch"):
        first = await orchestrator.handle_message(
            REQUIREMENT,
            conversation_id=conversation_id,
            user_id="user-orch",
            client_message_id=f"{conversation_id}:1",
        )
        self.assertEqual(first.phase, "a_dialogue")
        self.assertEqual(first.status, "awaiting_confirmation")
        confirmed = await orchestrator.handle_message(
            "confirm",
            conversation_id=conversation_id,
            user_id="user-orch",
            client_message_id=f"{conversation_id}:2",
        )
        return confirmed

    async def test_confirmed_requirements_publish_through_b_and_c(self):
        fake_b = FakeBSearchRunner(
            [lambda request, profile, ctx: _decision_handoff(profile, ctx, eligible=3)]
        )
        with mock_deepseek_client(complete_sentence_output()) as client:
            interpreter = DeepSeekRequirementInterpreter(api_key="test-only", client=client)
            orchestrator = self._build_orchestrator(interpreter, fake_b)
            result = await self._confirm(orchestrator)

        self.assertEqual(result.phase, "published")
        self.assertEqual(len(fake_b.requests), 1)
        self.assertEqual(result.recommendation["summary"].startswith("A total of 3 qualified candidates"), True)
        self.assertIn("1.", result.assistant_response)

    async def test_duplicate_client_message_replays_without_updating_profile(self):
        fake_b = FakeBSearchRunner([])
        with mock_deepseek_client(complete_sentence_output()) as client:
            interpreter = DeepSeekRequirementInterpreter(api_key="test-only", client=client)
            orchestrator = self._build_orchestrator(interpreter, fake_b)
            first = await orchestrator.handle_message(
                REQUIREMENT,
                conversation_id="conversation-replay",
                user_id="user-orch",
                client_message_id="conversation-replay:1",
            )
            replay = await orchestrator.handle_message(
                REQUIREMENT,
                conversation_id="conversation-replay",
                user_id="user-orch",
                client_message_id="conversation-replay:1",
            )

        checkpoint = await orchestrator.a_graph.aget_state(
            {"configurable": {"thread_id": "conversation-replay"}}
        )
        self.assertEqual(first, replay)
        self.assertEqual(checkpoint.values["profile"]["version"], 1)

    async def test_decision_failure_is_returned_as_failed(self):
        fake_b = FakeBSearchRunner(
            [lambda request, profile, ctx: _decision_handoff(profile, ctx, eligible=3)]
        )
        with mock_deepseek_client(complete_sentence_output()) as client:
            interpreter = DeepSeekRequirementInterpreter(api_key="test-only", client=client)
            orchestrator = self._build_orchestrator(
                interpreter, fake_b, decision_graph=FailedDecisionGraph()
            )
            result = await self._confirm(orchestrator, "conversation-failed")

        self.assertEqual(result.phase, "failed")
        self.assertEqual(result.status, "failed")
        self.assertIn("recommendation service is unavailable", result.assistant_response)
        checkpoint = await orchestrator.a_graph.aget_state(
            {"configurable": {"thread_id": "conversation-failed"}}
        )
        self.assertIsNone(checkpoint.values.get("search_request_id"))

    async def test_failed_decision_does_not_mark_a_request_as_consumed(self):
        fake_b = FakeBSearchRunner(
            [
                lambda request, profile, ctx: _decision_handoff(profile, ctx, eligible=3),
                lambda request, profile, ctx: _decision_handoff(profile, ctx, eligible=3),
            ]
        )
        with mock_deepseek_client(complete_sentence_output()) as client:
            interpreter = DeepSeekRequirementInterpreter(api_key="test-only", client=client)
            orchestrator = self._build_orchestrator(
                interpreter, fake_b, decision_graph=ExplodingDecisionGraph()
            )
            await orchestrator.handle_message(
                REQUIREMENT,
                conversation_id="conversation-crash",
                user_id="user-orch",
                client_message_id="conversation-crash:1",
            )
            with self.assertRaisesRegex(RuntimeError, "decision crashed"):
                await orchestrator.handle_message(
                    "confirm",
                    conversation_id="conversation-crash",
                    user_id="user-orch",
                    client_message_id="conversation-crash:2",
                )
            with self.assertRaisesRegex(RuntimeError, "decision crashed"):
                await orchestrator.handle_message(
                    "confirm",
                    conversation_id="conversation-crash",
                    user_id="user-orch",
                    client_message_id="conversation-crash:3",
                )

        checkpoint = await orchestrator.a_graph.aget_state(
            {"configurable": {"thread_id": "conversation-crash"}}
        )
        self.assertIsNone(checkpoint.values.get("search_request_id"))
        failed_runs = list(orchestrator.runs.runs.values())
        self.assertEqual(len(failed_runs), 1)
        self.assertEqual(failed_runs[-1]["status"], "failed")
        self.assertEqual(
            failed_runs[-1]["completion_reason"], "orchestration_error"
        )

    async def test_b_clarification_returns_to_a(self):
        questions = [{"field": "location", "text": "Which location do you mean?"}]

        def clarify(request, profile, ctx):
            return _ok(
                {
                    "route": "clarification",
                    "outcome": None,
                    "clarification_questions": questions,
                    "requirement_coverage": {
                        "fulfilled_requirement_ids": [],
                        "unsupported_requirement_ids": [],
                        "unverified_requirement_ids": [],
                        "skipped_best_effort_requirement_ids": [],
                    },
                },
                ctx,
            )

        fake_b = FakeBSearchRunner([clarify])
        with mock_deepseek_client(complete_sentence_output()) as client:
            interpreter = DeepSeekRequirementInterpreter(api_key="test-only", client=client)
            orchestrator = self._build_orchestrator(interpreter, fake_b)
            result = await self._confirm(orchestrator, "conversation-clarify")
            self.assertEqual(result.phase, "b_clarification")
            answered = await orchestrator.handle_message(
                "The location is Tampines",
                conversation_id="conversation-clarify",
                user_id="user-orch",
                client_message_id="conversation-clarify:3",
            )

        self.assertEqual(result.clarification_questions, questions)
        self.assertIn("Which location do you mean?", result.assistant_response)
        self.assertEqual(answered.phase, "a_dialogue")
        self.assertEqual(len(fake_b.requests), 1)
        checkpoint = await orchestrator.a_graph.aget_state(
            {"configurable": {"thread_id": "conversation-clarify"}}
        )
        self.assertIn(
            checkpoint.values["status"],
            {"awaiting_clarification", "awaiting_confirmation"},
        )

    async def test_c_waiting_user_then_decline_closes_run(self):
        fake_b = FakeBSearchRunner(
            [
                lambda request, profile, ctx: _decision_handoff(
                    profile, ctx, eligible=2, include_over_budget=True
                )
            ]
        )
        with mock_deepseek_client(complete_sentence_output()) as client:
            interpreter = DeepSeekRequirementInterpreter(api_key="test-only", client=client)
            orchestrator = self._build_orchestrator(interpreter, fake_b)
            waiting = await self._confirm(orchestrator, "conversation-decline")
            self.assertEqual(waiting.phase, "waiting_user")
            closed = await orchestrator.handle_message(
                "Do not accept adjustments, keep it as is",
                conversation_id="conversation-decline",
                user_id="user-orch",
                client_message_id="conversation-decline:3",
            )

        self.assertEqual(closed.phase, "published")
        self.assertEqual(len(closed.recommendation["ordered_items"]), 2)

    async def test_c_free_text_hands_back_to_a(self):
        fake_b = FakeBSearchRunner(
            [
                lambda request, profile, ctx: _decision_handoff(
                    profile, ctx, eligible=2, include_over_budget=True
                )
            ]
        )
        with mock_deepseek_client(complete_sentence_output()) as client:
            interpreter = DeepSeekRequirementInterpreter(api_key="test-only", client=client)
            orchestrator = self._build_orchestrator(interpreter, fake_b)
            waiting = await self._confirm(orchestrator, "conversation-handoff")
            self.assertEqual(waiting.phase, "waiting_user")
            handed = await orchestrator.handle_message(
                "I want to change it to near Tampines",
                conversation_id="conversation-handoff",
                user_id="user-orch",
                client_message_id="conversation-handoff:3",
            )

        self.assertEqual(handed.phase, "a_dialogue")
        self.assertEqual(handed.next_run_request["reason_code"], "user_message")
        self.assertIn(handed.status, {"awaiting_confirmation", "awaiting_clarification", "confirmed", "ready_for_b"})

    async def test_accepting_relaxation_starts_a_new_search(self):
        fake_b = FakeBSearchRunner(
            [
                lambda request, profile, ctx: _decision_handoff(
                    profile, ctx, eligible=2, include_over_budget=True
                ),
                lambda request, profile, ctx: _decision_handoff(profile, ctx, eligible=3),
            ]
        )
        with mock_deepseek_client(complete_sentence_output()) as client:
            interpreter = DeepSeekRequirementInterpreter(api_key="test-only", client=client)
            orchestrator = self._build_orchestrator(interpreter, fake_b)
            waiting = await self._confirm(orchestrator, "conversation-relax")
            self.assertEqual(waiting.phase, "waiting_user")
            published = await orchestrator.handle_message(
                "Okay, I accept raising the budget",
                conversation_id="conversation-relax",
                user_id="user-orch",
                client_message_id="conversation-relax:3",
            )

        self.assertEqual(published.phase, "published")
        self.assertEqual(len(fake_b.requests), 2)
        amount = next(
            item["value"]
            for item in fake_b.requests[1]["listing_constraints"]
            if item["field_path"] == "price.amount"
        )
        self.assertGreater(amount, 1800)


class RuntimeSettingsTests(unittest.TestCase):
    def test_runtime_toml_exposes_all_model_stacks(self):
        from property_agent.runtime.settings import load_runtime_settings

        settings = load_runtime_settings(reload=True)
        self.assertEqual(settings.deepseek.api_key_env, "DEEPSEEK_API_KEY")
        self.assertEqual(settings.deepseek.model, "deepseek-v4-flash")
        self.assertEqual(settings.deepseek.clarification_model, "deepseek-flash")
        self.assertEqual(settings.run.source_mode, "live")
        self.assertGreater(settings.search.page_limit, 0)

    def test_database_environment_is_resolved_into_settings(self):
        from property_agent.persistence.database import checkpoint_database_uri, database_url
        from property_agent.runtime.settings import load_runtime_settings

        settings = load_runtime_settings(
            environ={
                "DATABASE_URL": "postgresql://example:5432/falcon",
                "LANGGRAPH_CHECKPOINT_DB_URI": "postgresql://checkpoint:5432/falcon",
            }
        )
        self.assertEqual(
            database_url(settings.database),
            "postgresql+psycopg://example:5432/falcon",
        )
        self.assertEqual(
            checkpoint_database_uri(settings.database),
            "postgresql://checkpoint:5432/falcon",
        )

    def test_installed_runtime_falls_back_when_toml_is_absent(self):
        from property_agent.runtime import settings as runtime_settings

        missing = Path("/private/tmp/falcon-missing-runtime.toml")
        try:
            with patch.object(runtime_settings, "DEFAULT_RUNTIME_FILE", missing):
                settings = runtime_settings.load_runtime_settings(reload=True)
            self.assertEqual(settings.run.source_mode, "live")
            self.assertEqual(settings.database.url_env, "DATABASE_URL")
        finally:
            runtime_settings.load_runtime_settings(reload=True)


if __name__ == "__main__":
    unittest.main()
