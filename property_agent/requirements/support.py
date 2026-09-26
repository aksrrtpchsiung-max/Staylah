"""Support responsibilities extracted without changing behavior."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from .models import IssueCode



def utc_now() -> str:
    """返回适合 contract 和数据库保存的 UTC ISO-8601 时间。"""

    return datetime.now(timezone.utc).isoformat()


def _error_update(code: IssueCode, field: str, message: str) -> dict[str, Any]:
    """生成统一错误状态并把后续路由指向 recover_error。"""

    return {
        "requirement_issues": [{"code": code.value, "field": field, "message": message}],
        "workflow_route": "recover_error",
        "status": "failed",
    }
