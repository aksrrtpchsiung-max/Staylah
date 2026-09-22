"""初始化业务迁移与 LangGraph checkpoint 表。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 兼容 README 中的 `python scripts/init_postgres.py` 调用方式。直接运行脚本时，
# Python 只把 scripts/ 放进模块搜索路径，需要显式加入仓库根目录。
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
