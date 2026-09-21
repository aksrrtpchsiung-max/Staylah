"""Result 三态封装的构造与判定。

契约规定：success 必有合法 data；partial 必有可用 data 和至少一条 issue；
error 的 data=null 且至少一条 issue。这里把规则写成构造函数，避免各模块各自拼字典。
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
    """retryable 只描述故障性质；调用方仍要自己检查已用额度。"""
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
    """一次逻辑模块调用：技术重试复用同一个 call_id，因此 meta 也复用。"""

    ctx: RunContext
    started: float = field(default_factory=time.perf_counter)

    def meta(self) -> ResultMeta:
        return {
            "trace_id": self.ctx["trace_id"],
            "call_id": self.ctx["call_id"],
            "duration_ms": int((time.perf_counter() - self.started) * 1000),
        }

    def ok(self, data: Any, issues: Sequence[Issue] = ()) -> Result:
        """success 允许带非致命提醒，但不允许带 data=None。"""
        if data is None:
            raise ValueError("success 必须有 data")
        return {"status": "success", "data": data, "issues": list(issues), "meta": self.meta()}

    def partial(self, data: Any, issues: Sequence[Issue]) -> Result:
        if data is None:
            raise ValueError("partial 必须有可用 data")
        if not issues:
            raise ValueError("partial 必须至少有一条 issue")
        return {"status": "partial", "data": data, "issues": list(issues), "meta": self.meta()}

    def error(self, *issues: Issue) -> Result:
        if not issues:
            raise ValueError("error 必须至少有一条 issue")
        return {"status": "error", "data": None, "issues": list(issues), "meta": self.meta()}


def is_usable(result: Result) -> bool:
    """success 和 partial 都带可用 data；error 不带。"""
    return result["status"] in ("success", "partial") and result["data"] is not None


def first_issue_code(result: Result, default: str = "INTERNAL_ERROR") -> str:
    issues = result.get("issues") or []
    return issues[0]["code"] if issues else default
