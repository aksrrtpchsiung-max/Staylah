"""Results responsibilities extracted without changing behavior."""
from __future__ import annotations
from time import perf_counter
from typing import Any
from property_agent.contracts import Issue, Result, RunContext



def _meta(ctx: RunContext, started: float) -> dict[str, Any]:
    return {
        "trace_id": ctx["trace_id"],
        "call_id": ctx["call_id"],
        "duration_ms": max(0, round((perf_counter() - started) * 1000)),
    }


def _success(data: Any, ctx: RunContext, started: float) -> Result[Any]:
    return {"status": "success", "data": data, "issues": [], "meta": _meta(ctx, started)}


def _partial(
    data: Any,
    ctx: RunContext,
    started: float,
    message: str,
    *,
    retryable: bool,
    code: str = "RETRIEVAL_DEGRADED",
    source: str = "deepseek",
) -> Result[Any]:
    issue: Issue = {
        "code": code,  # type: ignore[typeddict-item]
        "message": message,
        "field_path": None,
        "source": source,
        "retryable": retryable,
        "retry_after_seconds": None,
    }
    return {"status": "partial", "data": data, "issues": [issue], "meta": _meta(ctx, started)}


def _error(
    ctx: RunContext,
    started: float,
    code: str,
    message: str,
    field_path: str | None = None,
    source: str | None = "part_c",
    *,
    retryable: bool = False,
    retry_after_seconds: int | None = None,
) -> Result[Any]:
    issue: Issue = {
        "code": code,  # type: ignore[typeddict-item]
        "message": message,
        "field_path": field_path,
        "source": source,
        "retryable": retryable,
        "retry_after_seconds": retry_after_seconds,
    }
    return {"status": "error", "data": None, "issues": [issue], "meta": _meta(ctx, started)}
