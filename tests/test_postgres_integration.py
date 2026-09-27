import copy
import os
import unittest
import uuid

from langgraph.types import Command
from sqlalchemy import delete, func, inspect, select

from property_agent.decision.boundaries import ProfileVersionConflict
from property_agent.decision.graph import initial_state
from property_agent.decision.runtime import postgres_decision_graph, thread_config
from property_agent.decision.stubs import ScriptedModuleC, ScriptedSearchRunner
from property_agent.persistence.database import build_engine, build_session_factory
from property_agent.persistence.models import (
    AgentRunRow,
    ConversationProfileRow,
    ConversationRow,
    MessageRow,
    ProfileMutationRow,
    RecommendationRow,
    RunQuestionRow,
)
from property_agent.persistence.repositories import (
    SqlChatRepository,
    SqlProfileRepository,
    SqlQuestionRepository,
    SqlRecommendationRepository,
    SqlRequirementProfileRepository,
    SqlRunRepository,
)
from property_agent.persistence.wiring import build_postgres_deps
from tests.support import build_ctx, build_outcome, load_profile

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(TEST_DATABASE_URL, "set TEST_DATABASE_URL for PostgreSQL tests")
class PostgresRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = build_engine(TEST_DATABASE_URL)
        self.sessions = build_session_factory(self.engine)
        suffix = uuid.uuid4().hex
        self.profile = copy.deepcopy(load_profile())
        self.profile["profile_id"] = f"profile-{suffix}"
        self.ctx = build_ctx(
            f"run-{suffix}",
            user_id=f"user-{suffix}",
            conversation_id=f"conversation-{suffix}",
        )
        self.profile["user_id"] = self.ctx["user_id"]
        self.profile["conversation_id"] = self.ctx["conversation_id"]
        self.runs = SqlRunRepository(self.sessions)
        self.runs.prepare_run(ctx=self.ctx, profile=self.profile)

    def tearDown(self):
        with self.sessions.begin() as session:
            session.execute(
                delete(RecommendationRow).where(
                    RecommendationRow.run_id == self.ctx["run_id"]
                )
            )
            session.execute(
                delete(RunQuestionRow).where(
                    RunQuestionRow.run_id == self.ctx["run_id"]
                )
            )
            session.execute(
                delete(MessageRow).where(
                    MessageRow.conversation_id == self.ctx["conversation_id"]
                )
            )
            session.execute(
                delete(AgentRunRow).where(AgentRunRow.run_id == self.ctx["run_id"])
            )
            session.execute(
                delete(ProfileMutationRow).where(
                    ProfileMutationRow.profile_id == self.profile["profile_id"]
                )
            )
            session.execute(
                delete(ConversationProfileRow).where(
                    ConversationProfileRow.profile_id == self.profile["profile_id"]
                )
            )
            session.execute(
                delete(ConversationRow).where(
                    ConversationRow.conversation_id == self.ctx["conversation_id"]
                )
            )
        self.engine.dispose()

    def test_schema_and_profile_columns_match_current_contract(self):
        inspector = inspect(self.engine)
        self.assertIn("conversation_profiles", inspector.get_table_names())
        self.assertNotIn("user_profiles", inspector.get_table_names())
        profile_columns = {
            column["name"]
            for column in inspector.get_columns("conversation_profiles")
        }
        self.assertEqual(
            profile_columns,
            {
                "profile_id",
                "user_id",
                "conversation_id",
                "version",
                "confirmed_version",
                "status",
                "intent",
                "user_context",
                "listing_constraints",
                "derived_data_requirements",
                "open_data_requirements",
                "unresolved",
                "field_sources",
                "created_at",
                "updated_at",
                "last_user_message_at",
                "confirmed_at",
            },
        )
        self.assertEqual(
            {column["name"] for column in inspector.get_columns("messages")},
            {
                "message_id",
                "conversation_id",
                "role",
                "text",
                "client_message_id",
                "created_at",
            },
        )
        self.assertIn(
            "run_id",
            {column["name"] for column in inspector.get_columns("agent_runs")},
        )
        self.assertIn(
            "question",
            {column["name"] for column in inspector.get_columns("run_questions")},
        )
        self.assertIn(
            "recommendation",
            {column["name"] for column in inspector.get_columns("recommendations")},
        )
        with self.sessions() as session:
            stored = session.get(
                ConversationProfileRow, self.profile["profile_id"]
            )
            assert stored is not None
            self.assertEqual(stored.conversation_id, self.profile["conversation_id"])
            self.assertEqual(stored.confirmed_version, self.profile["confirmed_version"])
            self.assertEqual(stored.listing_constraints, self.profile["listing_constraints"])

    def test_business_writes_are_idempotent(self):
        proposal = {
            "proposal_id": "raise-budget",
            "field": "listing_constraints.price.amount",
            "old_value": 3500,
            "proposed_value": 3600,
            "reason": "expand match",
            "evidence_listing_keys": [],
            "requires_user_confirmation": True,
        }
        question = {
            "question_id": f"{self.ctx['run_id']}:q1",
            "text": "Should the budget be adjusted from 3500 to 3600?",
            "reason_code": "insufficient_candidates",
            "proposals": [proposal],
            "allowed_actions": [
                "answer",
                "accept_proposal",
                "decline",
                "cancel",
            ],
            "base_profile_version": 1,
            "state_version": 1,
        }
        questions = SqlQuestionRepository(self.sessions)
        self.assertEqual(questions.save_question(self.ctx["run_id"], question), question)
        self.assertEqual(questions.save_question(self.ctx["run_id"], question), question)
        self.assertTrue(
            questions.mark_answered(
                self.ctx["run_id"],
                question["question_id"],
                client_message_id="client-answer-1",
                answer_text="accept",
            )
        )
        self.assertTrue(
            questions.mark_answered(
                self.ctx["run_id"],
                question["question_id"],
                client_message_id="client-answer-1",
                answer_text="accept",
            )
        )
        self.assertFalse(
            questions.mark_answered(
                self.ctx["run_id"],
                question["question_id"],
                client_message_id="different-answer",
                answer_text="reject",
            )
        )

        profiles = SqlProfileRepository(self.sessions)
        first = profiles.apply_relaxation(
            self.profile["profile_id"],
            base_version=1,
            proposal=proposal,
            source_message_id="client-answer-1",
            op_key="profile-op-1",
        )
        replay = profiles.apply_relaxation(
            self.profile["profile_id"],
            base_version=1,
            proposal=proposal,
            source_message_id="client-answer-1",
            op_key="profile-op-1",
        )
        self.assertEqual(first, replay)
        self.assertEqual(first["version"], 2)

        recommendations = SqlRecommendationRepository(self.sessions)
        body = {"ordered_items": [], "summary": "no results", "limitations": []}
        saved = recommendations.save(
            self.ctx["run_id"], body, op_key="recommendation-op-1"
        )
        replayed = recommendations.save(
            self.ctx["run_id"], body, op_key="recommendation-op-1"
        )
        self.assertFalse(saved[1])
        self.assertTrue(replayed[1])
        self.assertEqual(saved[0], replayed[0])

        with self.sessions() as session:
            message_count = session.scalar(
                select(func.count())
                .select_from(MessageRow)
                .where(MessageRow.conversation_id == self.ctx["conversation_id"])
            )
            self.assertEqual(message_count, 2)

    def test_profile_optimistic_lock_and_chat_history(self):
        proposal = {
            "proposal_id": "raise-budget",
            "field": "listing_constraints.price.amount",
            "old_value": 3500,
            "proposed_value": 3600,
            "reason": "expand match",
            "evidence_listing_keys": [],
            "requires_user_confirmation": True,
        }
        profiles = SqlProfileRepository(self.sessions)
        profiles.apply_relaxation(
            self.profile["profile_id"],
            base_version=1,
            proposal=proposal,
            source_message_id="client-answer-1",
            op_key="profile-op-lock-1",
        )
        with self.assertRaises(ProfileVersionConflict):
            profiles.apply_relaxation(
                self.profile["profile_id"],
                base_version=1,
                proposal=proposal,
                source_message_id="client-answer-2",
                op_key="profile-op-lock-2",
            )

        chat = SqlChatRepository(self.sessions)
        first = chat.append_message(
            self.ctx["conversation_id"],
            role="user",
            content="the budget can go up to 3600",
            message_id="chat-msg-1",
            client_message_id="chat-client-1",
        )
        replay = chat.append_message(
            self.ctx["conversation_id"],
            role="user",
            content="the budget can go up to 3600",
            message_id="chat-msg-1",
            client_message_id="chat-client-1",
        )
        self.assertEqual(first, replay)
        self.assertEqual(len(chat.list_messages(self.ctx["conversation_id"])), 1)

    def test_a_profile_adapter_updates_versions_and_rejects_stale_writes(self):
        adapter = SqlRequirementProfileRepository(self.sessions)
        updated = copy.deepcopy(self.profile)
        updated["version"] = 3
        updated["confirmed_version"] = 3
        updated["status"] = "confirmed"
        updated["updated_at"] = "2026-09-22T12:00:00+00:00"
        updated["last_user_message_at"] = "2026-09-22T12:00:00+00:00"
        updated["confirmed_at"] = "2026-09-22T12:00:00+00:00"

        adapter.save(updated, user_id=self.ctx["user_id"])
        adapter.save(updated, user_id=self.ctx["user_id"])
        with self.sessions() as session:
            stored = session.get(ConversationProfileRow, updated["profile_id"])
            self.assertEqual(stored.version, 3)
            self.assertEqual(stored.confirmed_version, 3)

        stale = copy.deepcopy(updated)
        stale["version"] = 2
        stale["confirmed_version"] = 2
        with self.assertRaises(ProfileVersionConflict):
            adapter.save(stale, user_id=self.ctx["user_id"])

        conflicting = copy.deepcopy(updated)
        conflicting["unresolved"] = ["conflicting-field"]
        with self.assertRaises(ProfileVersionConflict):
            adapter.save(conflicting, user_id=self.ctx["user_id"])
        with self.assertRaises(PermissionError):
            adapter.save(updated, user_id="another-user")

    def test_a_profile_adapter_inserts_the_first_confirmed_version(self):
        suffix = uuid.uuid4().hex
        profile = copy.deepcopy(self.profile)
        profile["profile_id"] = f"profile-a-first-{suffix}"
        profile["conversation_id"] = f"conversation-a-first-{suffix}"
        adapter = SqlRequirementProfileRepository(self.sessions)
        try:
            adapter.save(profile, user_id=self.ctx["user_id"])
            adapter.save(profile, user_id=self.ctx["user_id"])
            with self.sessions() as session:
                stored = session.get(ConversationProfileRow, profile["profile_id"])
                self.assertIsNotNone(stored)
                self.assertEqual(stored.version, profile["version"])
                self.assertEqual(stored.user_id, self.ctx["user_id"])
        finally:
            with self.sessions.begin() as session:
                session.execute(
                    delete(ConversationProfileRow).where(
                        ConversationProfileRow.profile_id == profile["profile_id"]
                    )
                )
                session.execute(
                    delete(ConversationRow).where(
                        ConversationRow.conversation_id == profile["conversation_id"]
                    )
                )


@unittest.skipUnless(TEST_DATABASE_URL, "set TEST_DATABASE_URL for PostgreSQL tests")
class PostgresCheckpointRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = build_engine(TEST_DATABASE_URL)
        self.sessions = build_session_factory(self.engine)
        suffix = uuid.uuid4().hex
        self.profile = copy.deepcopy(load_profile())
        self.profile["profile_id"] = f"profile-recovery-{suffix}"
        self.ctx = build_ctx(
            f"run-recovery-{suffix}",
            user_id=f"user-recovery-{suffix}",
            conversation_id=f"conversation-recovery-{suffix}",
        )
        self.profile["user_id"] = self.ctx["user_id"]
        self.profile["conversation_id"] = self.ctx["conversation_id"]
        SqlRunRepository(self.sessions).prepare_run(
            ctx=self.ctx, profile=self.profile
        )

    async def asyncTearDown(self):
        with self.sessions.begin() as session:
            session.execute(
                delete(RecommendationRow).where(
                    RecommendationRow.run_id == self.ctx["run_id"]
                )
            )
            session.execute(
                delete(RunQuestionRow).where(
                    RunQuestionRow.run_id == self.ctx["run_id"]
                )
            )
            session.execute(
                delete(MessageRow).where(
                    MessageRow.conversation_id == self.ctx["conversation_id"]
                )
            )
            session.execute(
                delete(AgentRunRow).where(AgentRunRow.run_id == self.ctx["run_id"])
            )
            session.execute(
                delete(ProfileMutationRow).where(
                    ProfileMutationRow.profile_id == self.profile["profile_id"]
                )
            )
            session.execute(
                delete(ConversationProfileRow).where(
                    ConversationProfileRow.profile_id == self.profile["profile_id"]
                )
            )
            session.execute(
                delete(ConversationRow).where(
                    ConversationRow.conversation_id == self.ctx["conversation_id"]
                )
            )
        self.engine.dispose()

    def deps(self):
        return build_postgres_deps(
            module_c=ScriptedModuleC(),
            search_runner=ScriptedSearchRunner(),
            sessions=self.sessions,
            use_deepseek=False,
        )

    async def test_interrupt_survives_runtime_restart(self):
        config = thread_config(self.ctx["run_id"])
        async with postgres_decision_graph(self.deps()) as graph:
            interrupted = await graph.ainvoke(
                initial_state(
                    ctx=self.ctx,
                    profile=self.profile,
                    outcome=build_outcome(
                        eligible=2,
                        include_over_budget=True,
                    ),
                ),
                config,
            )
            self.assertIn("__interrupt__", interrupted)

        async with postgres_decision_graph(self.deps(), setup=False) as graph:
            final = await graph.ainvoke(
                Command(
                    resume={
                        "client_message_id": f"answer-{self.ctx['run_id']}",
                        "text": "do not accept adjustments, keep as is",
                    }
                ),
                config,
            )
        self.assertEqual(final["completion_reason"], "user_declined")
        self.assertEqual(final["status"], "completed")
        self.assertEqual(
            [item["attempt_id"] for item in final["previous_attempts"]],
            ["attempt-001"],
        )

        with self.sessions() as session:
            self.assertEqual(
                session.scalar(
                    select(func.count())
                    .select_from(RunQuestionRow)
                    .where(RunQuestionRow.run_id == self.ctx["run_id"])
                ),
                1,
            )
            self.assertEqual(
                session.scalar(
                    select(func.count())
                    .select_from(RecommendationRow)
                    .where(RecommendationRow.run_id == self.ctx["run_id"])
                ),
                1,
            )
            self.assertEqual(
                session.scalar(
                    select(func.count())
                    .select_from(MessageRow)
                    .where(
                        MessageRow.conversation_id == self.ctx["conversation_id"]
                    )
                ),
                2,
            )

    async def test_stale_state_version_keeps_waiting_after_restart(self):
        config = thread_config(self.ctx["run_id"])
        async with postgres_decision_graph(self.deps()) as graph:
            interrupted = await graph.ainvoke(
                initial_state(
                    ctx=self.ctx,
                    profile=self.profile,
                    outcome=build_outcome(
                        eligible=2,
                        include_over_budget=True,
                    ),
                ),
                config,
            )
            question = interrupted["__interrupt__"][0].value["pending_question"]

        async with postgres_decision_graph(self.deps(), setup=False) as graph:
            rejected = await graph.ainvoke(
                Command(
                    resume={
                        "client_message_id": f"stale-{self.ctx['run_id']}",
                        "question_id": question["question_id"],
                        "expected_state_version": question["state_version"] + 99,
                        "action": "decline",
                    }
                ),
                config,
            )
            self.assertIn("__interrupt__", rejected)
            final = await graph.ainvoke(
                Command(
                    resume={
                        "client_message_id": f"ok-{self.ctx['run_id']}",
                        "text": "do not accept adjustments, keep as is",
                    }
                ),
                config,
            )
        self.assertEqual(final["completion_reason"], "user_declined")


if __name__ == "__main__":
    unittest.main()
