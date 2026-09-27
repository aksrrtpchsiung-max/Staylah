"""Legacy integration entry point: C.screen only retains the v0 shape and no longer checks hard conditions."""
from __future__ import annotations

from typing import Any

from property_agent.evaluation import service as part_c

from property_agent.contracts import ConversationProfile, ScreenResult


def screen_listings(listings: list[dict[str, Any]], profile: ConversationProfile) -> ScreenResult:
    return part_c.screen(listings, profile)  # type: ignore[arg-type]
