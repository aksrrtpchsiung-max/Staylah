"""数据库连接配置。业务 repository 使用短事务的同步 SQLAlchemy Session。"""
from __future__ import annotations

import os
from collections.abc import Callable

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://property_agent:property_agent_dev"
    "@127.0.0.1:5432/property_agent"
)


def database_url() -> str:
    return normalize_sqlalchemy_url(os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL))


def checkpoint_database_uri() -> str:
    value = os.getenv("LANGGRAPH_CHECKPOINT_DB_URI")
    if value:
        return normalize_psycopg_uri(value)
    return normalize_psycopg_uri(database_url())


def normalize_sqlalchemy_url(value: str) -> str:
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+psycopg://", 1)
    return value


def normalize_psycopg_uri(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def build_engine(url: str | None = None, *, echo: bool = False) -> Engine:
    return create_engine(
        normalize_sqlalchemy_url(url or database_url()),
        echo=echo,
        pool_pre_ping=True,
    )


def build_session_factory(
    engine: Engine,
) -> Callable[[], Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
