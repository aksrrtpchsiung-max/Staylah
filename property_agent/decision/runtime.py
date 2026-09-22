"""使用 PostgreSQL checkpoint 的 decision graph 生命周期。"""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from property_agent.decision.deps import DecisionDeps
from property_agent.decision.graph import build_decision_graph
from property_agent.persistence.database import checkpoint_database_uri


def thread_config(run_id: str, *, recursion_limit: int = 30) -> dict[str, Any]:
    """一次业务 run 对应一个稳定 LangGraph thread。"""
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
    """连接在 graph 使用期间保持打开；退出 context 后安全关闭。"""
    uri = checkpoint_uri or checkpoint_database_uri()
    async with AsyncPostgresSaver.from_conn_string(uri) as checkpointer:
        if setup:
            await checkpointer.setup()
        yield build_decision_graph(deps).compile(checkpointer=checkpointer)
