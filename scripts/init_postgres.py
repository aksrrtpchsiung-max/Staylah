"""初始化业务迁移与 LangGraph checkpoint 表。"""
from __future__ import annotations

import asyncio

from alembic import command
from alembic.config import Config
from dotenv import load_dotenv
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from property_agent.persistence.database import checkpoint_database_uri


async def main() -> None:
    load_dotenv()
    command.upgrade(Config("alembic.ini"), "head")
    async with AsyncPostgresSaver.from_conn_string(
        checkpoint_database_uri()
    ) as checkpointer:
        await checkpointer.setup()
    print("PostgreSQL business migrations and LangGraph checkpoints are ready.")


if __name__ == "__main__":
    asyncio.run(main())
