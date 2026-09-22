"""默认 onboarding 交接对象；外部运行控制层可替换此实现。"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from property_agent.decision.boundaries import NextRunRequest


class DefaultOnboardingHandoff:
    def build_request(
        self,
        *,
        profile_id: str,
        profile_version: int,
        superseded_run_id: str,
        source_message_id: str | None,
    ) -> "NextRunRequest":
        return {
            "reason_code": "user_message",
            "profile_id": profile_id,
            "profile_version": profile_version,
            "superseded_run_id": superseded_run_id,
            "accepted_proposal_id": None,
            "source_message_id": source_message_id,
        }
