import copy
import os
import unittest
import uuid

from langgraph.types import Command
from sqlalchemy import delete, func, select

from property_agent.decision.boundaries import ProfileVersionConflict
from property_agent.decision.graph import initial_state
from property_agent.decision.runtime import postgres_decision_graph, thread_config
from property_agent.decision.stubs import ScriptedModuleC, ScriptedSearchRunner
from property_agent.persistence.database import build_engine, build_session_factory
from property_agent.persistence.models import (
    AgentRunRow,
    ConversationRow,
    MessageRow,
    ProfileMutationRow,
    RecommendationRow,
    RunQuestionRow,
    UserProfileRow,
)
from property_agent.persistence.repositories import (
    SqlChatRepository,
    SqlProfileRepository,
    SqlQuestionRepository,
    SqlRecommendationRepository,
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
                delete(AgentRunRow).where(AgentRunRow.id == self.ctx["run_id"])
            )
            session.execute(
                delete(ProfileMutationRow).where(
                    ProfileMutationRow.profile_id == self.profile["profile_id"]
                )
            )
            session.execute(
                delete(ConversationRow).where(
                    ConversationRow.id == self.ctx["conversation_id"]
                )
            )
            session.execute(
                delete(UserProfileRow).where(
                    UserProfileRow.profile_id == self.profile["profile_id"]
                )
            )
        self.engine.dispose()

    def test_business_writes_are_idempotent(self):
        proposal = {
            "proposal_id": "raise-budget",
            "field": "hard_constraints.max_price",
            "old_value": 3500,
            "proposed_value": 3600,
            "reason": "扩大匹配",
            "evidence_listing_keys": [],
            "requires_user_confirmation": True,
        }
        question = {
            "question_id": f"{self.ctx['run_id']}:q1",
            "text": "是否把预算从 3500 调整到 3600？",
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
                answer_text="接受",
            )
        )
        self.assertTrue(
            questions.mark_answered(
                self.ctx["run_id"],
                question["question_id"],
                client_message_id="client-answer-1",
                answer_text="接受",
            )
        )
        self.assertFalse(
            questions.mark_answered(
                self.ctx["run_id"],
                question["question_id"],
                client_message_id="different-answer",
                answer_text="拒绝",
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
        body = {"ordered_items": [], "summary": "没有结果", "limitations": []}
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
            "field": "hard_constraints.max_price",
            "old_value": 3500,
            "proposed_value": 3600,
            "reason": "扩大匹配",
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
            content="预算可以到 3600",
            message_id="chat-msg-1",
            client_message_id="chat-client-1",
        )
        replay = chat.append_message(
            self.ctx["conversation_id"],
            role="user",
            content="预算可以到 3600",
            message_id="chat-msg-1",
            client_message_id="chat-client-1",
        )
        self.assertEqual(first, replay)
        self.assertEqual(len(chat.list_messages(self.ctx["conversation_id"])), 1)


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
                delete(AgentRunRow).where(AgentRunRow.id == self.ctx["run_id"])
            )
            session.execute(
                delete(ProfileMutationRow).where(
                    ProfileMutationRow.profile_id == self.profile["profile_id"]
                )
            )
            session.execute(
                delete(ConversationRow).where(
                    ConversationRow.id == self.ctx["conversation_id"]
                )
            )
            session.execute(
                delete(UserProfileRow).where(
                    UserProfileRow.profile_id == self.profile["profile_id"]
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
                        "text": "不接受调整，保持原样",
                    }
                ),
                config,
            )
        self.assertEqual(final["completion_reason"], "user_declined")
        self.assertEqual(final["status"], "completed")

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
                        "text": "不接受调整，保持原样",
                    }
                ),
                config,
            )
        self.assertEqual(final["completion_reason"], "user_declined")


if __name__ == "__main__":
    unittest.main()
