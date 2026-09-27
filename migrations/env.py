from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from property_agent.persistence.database import database_url
from property_agent.persistence.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", database_url())
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
LANGGRAPH_TABLES = {
    "checkpoint_migrations",
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
}


def include_object(object_, name, type_, reflected, compare_to):
    """LangGraph saver manages its own tables; Alembic must not generate delete operations."""
    if type_ == "table" and reflected and name in LANGGRAPH_TABLES:
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
