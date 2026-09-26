"""旧联调入口：C.screen 仅保留 v0 形状，不再检查硬条件。"""
from __future__ import annotations

from typing import Any

from property_agent.evaluation import service as part_c

from property_agent.contracts import ConversationProfile, ScreenResult


def screen_listings(listings: list[dict[str, Any]], profile: ConversationProfile) -> ScreenResult:
    return part_c.screen(listings, profile)  # type: ignore[arg-type]
