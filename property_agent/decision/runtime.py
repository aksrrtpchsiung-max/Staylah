"""Decision graph lifecycle using PostgreSQL checkpoint."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from property_agent.decision.deps import DecisionDeps
from property_agent.decision.graph import build_decision_graph
from property_agent.persistence.database import checkpoint_database_uri


def thread_config(run_id: str, *, recursion_limit: int = 30) -> dict[str, Any]:
    """One business run corresponds to one stable LangGraph thread."""
    return {
        "configurable": {"thread_id": run_id},
        "recursion_limit": recursion_limit,
    }


@asynccontextmanager
async def postgres_decision_graph(
    deps: DecisionDeps,
    *,
    checkpoint_uri: str | None = None,
    setup: bool = True,
) -> AsyncIterator[Any]:
    """The connection stays open while the graph is in use; it is safely closed after exiting the context."""
    uri = checkpoint_uri or checkpoint_database_uri()
    async with AsyncPostgresSaver.from_conn_string(uri) as checkpointer:
        if setup:
            await checkpointer.setup()
        yield build_decision_graph(deps).compile(checkpointer=checkpointer)
