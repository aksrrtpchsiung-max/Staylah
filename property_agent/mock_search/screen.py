"""Mock 联调用 C 的 screen：硬条件筛选由 part_c 实现，避免两套规则分叉。"""
from __future__ import annotations

from typing import Any

import part_c

from property_agent.contracts import ConversationProfile, ScreenResult


def screen_listings(listings: list[dict[str, Any]], profile: ConversationProfile) -> ScreenResult:
    return part_c.screen(listings, profile)  # type: ignore[arg-type]
