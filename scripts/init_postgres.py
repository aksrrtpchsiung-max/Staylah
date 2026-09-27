"""Initialize business migration and LangGraph checkpoint tables."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Compatible with the `python scripts/init_postgres.py` invocation in the README. When running the script directly,
# Python only puts scripts/ on the module search path, so the repository root directory must be added explicitly.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
