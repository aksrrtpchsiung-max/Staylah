"""Support responsibilities extracted without changing behavior."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from .models import IssueCode



def utc_now() -> str:
    """Return a UTC ISO-8601 time suitable for contract and database storage."""

    return datetime.now(timezone.utc).isoformat()


def _error_update(code: IssueCode, field: str, message: str) -> dict[str, Any]:
    """Generate a unified error state and direct subsequent routes to recover_error."""

    return {
        "requirement_issues": [{"code": code.value, "field": field, "message": message}],
        "workflow_route": "recover_error",
        "status": "failed",
    }
