"""Construction and determination of the Result three-state wrapper.

The contract specifies: success must have valid data; partial must have usable data and at least one issue;
error has data=null and at least one issue. Here the rules are written as constructors to avoid each module assembling dictionaries on its own.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from property_agent.contracts import Issue, Result, ResultMeta, RunContext

RETRYABLE_CODES = frozenset({
    "TIMEOUT", "RATE_LIMITED", "TEMPORARY_UNAVAILABLE", "MODEL_UNAVAILABLE",
})


def make_issue(
    code: str,
    message: str,
    *,
    field_path: str | None = None,
    source: str | None = None,
    retryable: bool | None = None,
    retry_after_seconds: int | None = None,
) -> Issue:
    """retryable only describes the nature of the failure; the caller must still check the quota already used on its own."""
    return {
        "code": code,  # type: ignore[typeddict-item]
        "message": message,
        "field_path": field_path,
        "source": source,
        "retryable": code in RETRYABLE_CODES if retryable is None else retryable,
        "retry_after_seconds": retry_after_seconds,
    }


@dataclass
class CallTimer:
    """A single logical module call: technical retries reuse the same call_id, so meta is reused as well."""

    ctx: RunContext
    started: float = field(default_factory=time.perf_counter)

    def meta(self) -> ResultMeta:
        return {
            "trace_id": self.ctx["trace_id"],
            "call_id": self.ctx["call_id"],
            "duration_ms": int((time.perf_counter() - self.started) * 1000),
        }

    def ok(self, data: Any, issues: Sequence[Issue] = ()) -> Result:
        """success allows non-fatal reminders, but does not allow data=None."""
        if data is None:
            raise ValueError("success must have data")
        return {"status": "success", "data": data, "issues": list(issues), "meta": self.meta()}

    def partial(self, data: Any, issues: Sequence[Issue]) -> Result:
        if data is None:
            raise ValueError("partial must have usable data")
        if not issues:
            raise ValueError("partial must have at least one issue")
        return {"status": "partial", "data": data, "issues": list(issues), "meta": self.meta()}

    def error(self, *issues: Issue) -> Result:
        if not issues:
            raise ValueError("error must have at least one issue")
        return {"status": "error", "data": None, "issues": list(issues), "meta": self.meta()}


def is_usable(result: Result) -> bool:
    """Both success and partial carry usable data; error does not."""
    return result["status"] in ("success", "partial") and result["data"] is not None


def first_issue_code(result: Result, default: str = "INTERNAL_ERROR") -> str:
    issues = result.get("issues") or []
    return issues[0]["code"] if issues else default
