"""Profile repository responsibilities extracted without changing behavior."""
from __future__ import annotations
import copy
from typing import Any, Protocol



class ProfileRepository(Protocol):
    """Define the persistence interface for a confirmed ConversationProfile."""

    def save(self, profile: dict[str, Any], *, user_id: str) -> None:
        """Atomically save a version of the confirmed profile as the current calling user."""


class InMemoryProfileRepository:
    """Provide a non-persistent profile repository for local development and testing only."""

    def __init__(self) -> None:
        """Initialize isolated in-memory storage keyed by profile_id."""

        self._profiles: dict[str, dict[str, Any]] = {}

    def save(self, profile: dict[str, Any], *, user_id: str) -> None:
        """After verifying user ownership, save a deep copy to avoid subsequent state modifications polluting the confirmed version."""

        if profile.get("user_id") != user_id:
            raise PermissionError("profile.user_id does not match the current user")
        self._profiles[profile["profile_id"]] = copy.deepcopy(profile)

    def get(self, profile_id: str) -> dict[str, Any] | None:
        """Read a copy of the specified profile, returning None if it is missing."""

        value = self._profiles.get(profile_id)
        return copy.deepcopy(value) if value is not None else None
