"""Common responsibilities extracted without changing behavior."""
from __future__ import annotations
import hashlib
from collections.abc import Callable
from datetime import datetime
from sqlalchemy.orm import Session



SessionFactory = Callable[[], Session]


def _as_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
