"""Opt-in evaluation trace; normal production calls are unaffected."""
from __future__ import annotations

import copy
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Iterator


_ACTIVE: ContextVar[dict[str, Any] | None] = ContextVar("evaluation_trace", default=None)


@contextmanager
def capture_trace(case_id: str) -> Iterator[dict[str, Any]]:
    trace: dict[str, Any] = {"case_id": case_id, "events": [], "spans": []}
    token = _ACTIVE.set(trace)
    try:
        yield trace
    finally:
        _ACTIVE.reset(token)


@contextmanager
def stage_span(stage: str, operation: str, *, attempt_id: str | None = None) -> Iterator[None]:
    trace = _ACTIVE.get()
    if trace is None:
        yield
        return
    start = perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    span = {
        "stage": stage,
        "operation": operation,
        "attempt_id": attempt_id,
        "started_at": started_at,
        "duration_ms": 0.0,
        "status": "running",
    }
    trace["spans"].append(span)
    try:
        yield
    except BaseException:
        span["status"] = "error"
        raise
    else:
        span["status"] = "ok"
    finally:
        span["duration_ms"] = round((perf_counter() - start) * 1000, 2)


def record_event(kind: str, data: Any, *, attempt_id: str | None = None) -> None:
    trace = _ACTIVE.get()
    if trace is not None:
        trace["events"].append({
            "kind": kind,
            "attempt_id": attempt_id,
            "at": datetime.now(timezone.utc).isoformat(),
            "data": copy.deepcopy(data),
        })


def stage_totals(trace: dict[str, Any]) -> dict[str, float]:
    totals = {"A": 0.0, "B": 0.0, "C": 0.0}
    for span in trace["spans"]:
        totals[span["stage"]] += span["duration_ms"]
    return {stage: round(value, 2) for stage, value in totals.items()}
