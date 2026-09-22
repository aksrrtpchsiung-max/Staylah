"""把 A graph、B 搜索和 C 决策接到同一套 PostgreSQL checkpoint / 业务库。"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from property_agent.decision.graph import build_decision_graph
from property_agent.decision.module_c import PartCEvaluationModule
from property_agent.orchestration.service import ConversationOrchestrator
from property_agent.persistence.database import (
    build_engine,
    build_session_factory,
    checkpoint_database_uri,
    database_url,
)
from property_agent.persistence.repositories import (
    SqlChatRepository,
    SqlProfileRepository,
    SqlRequirementProfileRepository,
    SqlRunRepository,
)
from property_agent.persistence.wiring import build_postgres_deps
from requirement_understanding import (
    DeepSeekHousingQuestionAnswerer,
    DeepSeekInputGuard,
    DeepSeekParserConfig,
    DeepSeekRequirementInterpreter,
    DeepSeekTurnIntentClassifier,
    build_requirement_graph,
)
from runtime_settings import RuntimeSettings, load_runtime_settings


def build_postgres_requirement_graph(
    *,
    sessions: Any,
    checkpointer: Any,
    settings: RuntimeSettings | None = None,
) -> Any:
    """A graph 使用 SQL profile 仓储和 Postgres checkpoint，thread_id=conversation_id。"""

    resolved = settings or load_runtime_settings()
    parser_config = DeepSeekParserConfig.from_runtime(resolved.deepseek)
    return build_requirement_graph(
        interpreter=DeepSeekRequirementInterpreter(config=parser_config),
        input_guard=DeepSeekInputGuard(config=parser_config),
        turn_intent_classifier=DeepSeekTurnIntentClassifier(config=parser_config),
        housing_question_answerer=DeepSeekHousingQuestionAnswerer(config=parser_config),
        profile_repository=SqlRequirementProfileRepository(sessions),
        checkpointer=checkpointer,
    )


@asynccontextmanager
async def postgres_conversation_runtime(
    *,
    settings: RuntimeSettings | None = None,
    setup: bool = True,
) -> AsyncIterator[ConversationOrchestrator]:
    """A/C 共享 AsyncPostgresSaver；业务表走同步 Session。"""

    import part_c

    settings = settings or load_runtime_settings()
    engine = build_engine(database_url(settings.database))
    sessions = build_session_factory(engine)
    uri = checkpoint_database_uri(settings.database)
    use_deepseek = bool(os.getenv(settings.deepseek.api_key_env))
    if os.getenv(settings.llm_gateway.api_key_env):
        part_c.configure_gateway_keyword_matcher(
            base_url=settings.llm_gateway.url,
            model_id=settings.llm_gateway.model,
        )
        part_c.configure_gateway_evaluation_review_model(
            base_url=settings.llm_gateway.url,
            model_id=settings.llm_gateway.model,
        )
    async with AsyncPostgresSaver.from_conn_string(uri) as checkpointer:
        if setup:
            await checkpointer.setup()
        a_graph = build_postgres_requirement_graph(
            sessions=sessions,
            checkpointer=checkpointer,
            settings=settings,
        )
        deps = build_postgres_deps(
            module_c=PartCEvaluationModule(),
            sessions=sessions,
            use_deepseek=use_deepseek,
        )
        decision_graph = build_decision_graph(deps).compile(checkpointer=checkpointer)
        orchestrator = ConversationOrchestrator(
            a_graph=a_graph,
            decision_graph=decision_graph,
            search_runner=deps.search_runner,
            chat=SqlChatRepository(sessions),
            runs=SqlRunRepository(sessions),
            profiles=SqlProfileRepository(sessions),
            source_mode=settings.run.source_mode,
            deadline_seconds=settings.run.deadline_seconds,
        )
        try:
            yield orchestrator
        finally:
            engine.dispose()
