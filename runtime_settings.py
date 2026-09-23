"""集中读取 runtime.toml；密钥只从环境变量或 .env 注入。"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from tomllib import loads
from typing import Any, Mapping

from dotenv import dotenv_values

DEFAULT_RUNTIME_FILE = Path(__file__).resolve().with_name("runtime.toml")
DEFAULT_ENV_FILE = Path(__file__).resolve().with_name(".env")

# wheel 安装不会把根目录的 runtime.toml 放到 py-module 旁边。这里保留同一套
# 非密钥默认值作为安装包回退；源码运行时仍优先读取可编辑的 runtime.toml。
DEFAULT_RUNTIME_TOML = """
[deepseek]
base_url = "https://api.deepseek.com"
model = "deepseek-v4-flash"
api_key_env = "DEEPSEEK_API_KEY"
timeout_seconds = 45.0
max_tokens = 2600
clarification_model = "deepseek-flash"
clarification_max_tokens = 700
clarification_timeout_seconds = 30.0
clarification_max_calls = 6

[database]
url_env = "DATABASE_URL"
checkpoint_url_env = "LANGGRAPH_CHECKPOINT_DB_URI"
default_url = "postgresql+psycopg://property_agent:property_agent_dev@127.0.0.1:5432/property_agent"

[search]
page_limit = 4
candidate_limit = 12
page_result_limit = 6
provider_timeout_seconds = 30.0
max_retries = 1
planner_timeout_seconds = 20.0
supervisor_timeout_seconds = 8.0
supervisor_max_calls = 3
finalize_reserve_seconds = 10.0

[run]
deadline_seconds = 300
source_mode = "live"
"""

_CACHE: RuntimeSettings | None = None


class RuntimeConfigurationError(ValueError):
    """运行时配置缺失或非法；错误信息不包含密钥。"""


@dataclass(frozen=True)
class DeepSeekSettings:
    base_url: str
    model: str
    api_key_env: str
    timeout_seconds: float
    max_tokens: int
    clarification_model: str
    clarification_max_tokens: int
    clarification_timeout_seconds: float
    clarification_max_calls: int

    def api_key(self, environ: Mapping[str, str] | None = None) -> str:
        values = os.environ if environ is None else environ
        return (values.get(self.api_key_env) or "").strip()


@dataclass(frozen=True)
class DatabaseSettings:
    url_env: str
    checkpoint_url_env: str
    default_url: str
    url: str
    checkpoint_url: str | None


@dataclass(frozen=True)
class SearchSettings:
    page_limit: int
    candidate_limit: int
    page_result_limit: int
    provider_timeout_seconds: float
    max_retries: int
    planner_timeout_seconds: float
    supervisor_timeout_seconds: float
    supervisor_max_calls: int
    finalize_reserve_seconds: float


@dataclass(frozen=True)
class RunSettings:
    deadline_seconds: int
    source_mode: str


@dataclass(frozen=True)
class RuntimeSettings:
    deepseek: DeepSeekSettings
    database: DatabaseSettings
    search: SearchSettings
    run: RunSettings
    path: Path


def load_runtime_settings(
    path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    env_file: str | Path | None = DEFAULT_ENV_FILE,
    reload: bool = False,
) -> RuntimeSettings:
    """读取 runtime.toml；进程环境优先于 .env，再覆盖文件中的非密钥默认值。"""
    global _CACHE
    runtime_path = Path(path) if path is not None else DEFAULT_RUNTIME_FILE
    if (
        not reload
        and _CACHE is not None
        and path is None
        and environ is None
        and env_file == DEFAULT_ENV_FILE
    ):
        return _CACHE
    if runtime_path.is_file():
        data = loads(runtime_path.read_text(encoding="utf-8"))
    elif path is None:
        data = loads(DEFAULT_RUNTIME_TOML)
    else:
        raise RuntimeConfigurationError(f"找不到运行时配置：{runtime_path}")
    merged = _merged_environ(env_file=env_file, environ=environ)
    settings = RuntimeSettings(
        deepseek=_deepseek(data.get("deepseek") or {}, merged),
        database=_database(data.get("database") or {}, merged),
        search=_search(data.get("search") or {}, merged),
        run=_run(data.get("run") or {}, merged),
        path=runtime_path,
    )
    if path is None and environ is None and env_file == DEFAULT_ENV_FILE:
        _CACHE = settings
    return settings


def secret_environ(
    env_file: str | Path | None = DEFAULT_ENV_FILE,
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """返回已合并的进程环境，供需要 os.getenv 的既有客户端使用。"""
    return _merged_environ(env_file=env_file, environ=environ)


def _merged_environ(
    *,
    env_file: str | Path | None,
    environ: Mapping[str, str] | None,
) -> dict[str, str]:
    values: dict[str, str] = {}
    if env_file:
        file_path = Path(env_file)
        if file_path.is_file():
            loaded = dotenv_values(file_path, interpolate=False)
            values.update(
                {key: value for key, value in loaded.items() if key and value is not None}
            )
    values.update(os.environ if environ is None else dict(environ))
    return values


def _section(raw: dict[str, Any], key: str, default: Any) -> Any:
    value = raw.get(key, default)
    return default if value is None else value


def _deepseek(raw: dict[str, Any], environ: Mapping[str, str]) -> DeepSeekSettings:
    return DeepSeekSettings(
        base_url=_override(raw, environ, "DEEPSEEK_API_BASE", "base_url", "https://api.deepseek.com"),
        model=_override(raw, environ, "DEEPSEEK_MODEL", "model", "deepseek-v4-flash"),
        api_key_env=str(_section(raw, "api_key_env", "DEEPSEEK_API_KEY")),
        timeout_seconds=float(
            _override(raw, environ, "DEEPSEEK_TIMEOUT_SECONDS", "timeout_seconds", 45.0)
        ),
        max_tokens=int(_section(raw, "max_tokens", 2600)),
        clarification_model=str(
            _override(
                raw,
                environ,
                "DEEPSEEK_CLARIFICATION_MODEL",
                "clarification_model",
                "deepseek-flash",
            )
        ),
        clarification_max_tokens=int(_section(raw, "clarification_max_tokens", 700)),
        clarification_timeout_seconds=float(
            _override(
                raw,
                environ,
                "DEEPSEEK_CLARIFICATION_TIMEOUT_SECONDS",
                "clarification_timeout_seconds",
                30.0,
            )
        ),
        clarification_max_calls=int(
            _override(
                raw,
                environ,
                "DEEPSEEK_CLARIFICATION_MAX_CALLS",
                "clarification_max_calls",
                6,
            )
        ),
    )


def _database(raw: dict[str, Any], environ: Mapping[str, str]) -> DatabaseSettings:
    url_env = str(_section(raw, "url_env", "DATABASE_URL"))
    checkpoint_url_env = str(
        _section(raw, "checkpoint_url_env", "LANGGRAPH_CHECKPOINT_DB_URI")
    )
    default_url = str(
        _section(
            raw,
            "default_url",
            "postgresql+psycopg://property_agent:property_agent_dev@127.0.0.1:5432/property_agent",
        )
    )
    return DatabaseSettings(
        url_env=url_env,
        checkpoint_url_env=checkpoint_url_env,
        default_url=default_url,
        url=str(environ.get(url_env) or default_url),
        checkpoint_url=(str(environ[checkpoint_url_env]) if environ.get(checkpoint_url_env) else None),
    )


def _search(raw: dict[str, Any], environ: Mapping[str, str]) -> SearchSettings:
    return SearchSettings(
        page_limit=int(_override(raw, environ, "SEARCH_PAGE_LIMIT", "page_limit", 4)),
        candidate_limit=int(
            _override(raw, environ, "SEARCH_CANDIDATE_LIMIT", "candidate_limit", 12)
        ),
        page_result_limit=int(
            _override(raw, environ, "SEARCH_PAGE_RESULT_LIMIT", "page_result_limit", 6)
        ),
        provider_timeout_seconds=float(
            _override(
                raw,
                environ,
                "SEARCH_PROVIDER_TIMEOUT_SECONDS",
                "provider_timeout_seconds",
                30.0,
            )
        ),
        max_retries=int(_override(raw, environ, "SEARCH_MAX_RETRIES", "max_retries", 1)),
        planner_timeout_seconds=float(
            _override(
                raw,
                environ,
                "SEARCH_PLANNER_TIMEOUT_SECONDS",
                "planner_timeout_seconds",
                20.0,
            )
        ),
        supervisor_timeout_seconds=float(
            _override(
                raw,
                environ,
                "SEARCH_SUPERVISOR_TIMEOUT_SECONDS",
                "supervisor_timeout_seconds",
                8.0,
            )
        ),
        supervisor_max_calls=int(
            _override(raw, environ, "SEARCH_SUPERVISOR_MAX_CALLS", "supervisor_max_calls", 3)
        ),
        finalize_reserve_seconds=float(
            _override(
                raw,
                environ,
                "SEARCH_FINALIZE_RESERVE_SECONDS",
                "finalize_reserve_seconds",
                10.0,
            )
        ),
    )


def _run(raw: dict[str, Any], environ: Mapping[str, str]) -> RunSettings:
    source_mode = str(_override(raw, environ, "SOURCE_MODE", "source_mode", "live"))
    if source_mode not in {"live", "mock"}:
        raise RuntimeConfigurationError("run.source_mode 只能是 live 或 mock")
    return RunSettings(
        deadline_seconds=int(
            _override(raw, environ, "RUN_DEADLINE_SECONDS", "deadline_seconds", 300)
        ),
        source_mode=source_mode,
    )


def _override(
    raw: dict[str, Any],
    environ: Mapping[str, str],
    env_name: str,
    field: str,
    default: Any,
) -> Any:
    if env_name in environ and str(environ[env_name]).strip():
        return environ[env_name]
    return _section(raw, field, default)
