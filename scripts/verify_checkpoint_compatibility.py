"""Create checkpoints with an old checkout and resume them with a new checkout.

Requires TEST_DATABASE_URL pointing to an isolated, migrated database. Example:
  python scripts/verify_checkpoint_compatibility.py create --source-root /tmp/old
  python scripts/verify_checkpoint_compatibility.py resume
No live model or source calls are made.
"""
import argparse
import asyncio
import os
from pathlib import Path
import sys


async def verify(mode, prefix):
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.types import Command
    from property_agent.persistence.database import build_engine, build_session_factory, normalize_psycopg_uri
    from property_agent.persistence.repositories import SqlChatRepository, SqlRequirementProfileRepository
    from property_agent.persistence.wiring import build_postgres_deps
    from property_agent.decision.graph import build_decision_graph, initial_state
    from property_agent.decision.stubs import ScriptedModuleC, ScriptedSearchRunner
    from requirement_understanding import DeepSeekRequirementInterpreter, build_requirement_graph
    from tests.test_requirement_understanding import AlwaysHousingGuard, TestTurnIntentClassifier, complete_sentence_output, mock_deepseek_client
    from tests.test_orchestration import REQUIREMENT
    from tests.support import build_ctx, build_outcome, load_profile

    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        raise SystemExit("Set TEST_DATABASE_URL to a dedicated test database.")
    engine = build_engine(url)
    sessions = build_session_factory(engine)
    a_id, c_id = prefix + "-a", prefix + "-c"
    try:
        async with AsyncPostgresSaver.from_conn_string(normalize_psycopg_uri(url)) as saver:
            await saver.setup()
            with mock_deepseek_client(complete_sentence_output()) as client:
                graph_a = build_requirement_graph(
                    interpreter=DeepSeekRequirementInterpreter(api_key="test-only", client=client),
                    input_guard=AlwaysHousingGuard(), turn_intent_classifier=TestTurnIntentClassifier(),
                    profile_repository=SqlRequirementProfileRepository(sessions),
                    checkpointer=saver, clock=lambda: "2026-09-22T10:00:00+00:00")
                config_a = {"configurable": {"thread_id": a_id}}
                if mode == "create":
                    SqlChatRepository(sessions).ensure_conversation(a_id, user_id="compat-user")
                    result = await graph_a.ainvoke({"message_id": a_id + ":1", "current_input": REQUIREMENT,
                        "user_id": "compat-user", "conversation_id": a_id, "status": "new"}, config_a)
                    assert result["status"] == "awaiting_confirmation", result["status"]
                else:
                    previous = await graph_a.aget_state(config_a)
                    assert previous.values["status"] == "awaiting_confirmation"
                    result = await graph_a.ainvoke({"message_id": a_id + ":2", "current_input": "确认",
                        "user_id": "compat-user", "conversation_id": a_id}, config_a)
                    assert result["status"] == "ready_for_b", result["status"]
                    assert result["requirement_request"]["profile_version"] == previous.values["profile"]["version"]

            deps = build_postgres_deps(sessions=sessions, module_c=ScriptedModuleC(),
                search_runner=ScriptedSearchRunner(), use_deepseek=False)
            graph_c = build_decision_graph(deps).compile(checkpointer=saver)
            config_c = {"configurable": {"thread_id": c_id}}
            if mode == "create":
                profile = load_profile()
                profile.update(profile_id=c_id + "-profile", conversation_id=c_id, user_id="compat-user")
                ctx = build_ctx(c_id, conversation_id=c_id, user_id="compat-user")
                deps.runs.prepare_run(ctx=ctx, profile=profile)
                result = await graph_c.ainvoke(initial_state(ctx=ctx, profile=profile,
                    outcome=build_outcome(eligible=2, include_over_budget=True)), config_c)
                assert "__interrupt__" in result
            else:
                previous = await graph_c.aget_state(config_c)
                assert previous.values["pending_question"]
                result = await graph_c.ainvoke(Command(resume={
                    "client_message_id": c_id + ":reply", "text": "不接受调整，保持原样"}), config_c)
                assert result["completion_reason"] == "user_declined", result["completion_reason"]
            print(f"{mode}: A confirmation and C interrupt checkpoints verified ({prefix})")
    finally:
        engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("create", "resume"))
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--prefix", default="refactor-compat")
    args = parser.parse_args()
    sys.path.insert(0, str(args.source_root.resolve()))
    asyncio.run(verify(args.mode, args.prefix))
