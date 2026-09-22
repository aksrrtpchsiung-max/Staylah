"""按补搜指令回放下一页 mock SearchResult。"""
from __future__ import annotations

from dataclasses import dataclass, field

from property_agent.contracts import ConversationProfile, Result, RunContext, SearchDirective
from property_agent.mock_search.pipeline import load_search_fixture, outcome_from_search_result
from property_agent.results import CallTimer, make_issue


@dataclass
class MockSearchRunner:
    """实现 SearchRunner：next_page.cursor 映射到固定夹具。"""

    pages: dict[str, str] = field(
        default_factory=lambda: {"mock-page-2": "page-2", "page-2": "page-2"}
    )
    calls: list[SearchDirective] = field(default_factory=list)

    async def run_attempt(
        self, directive: SearchDirective, profile: ConversationProfile, *, ctx: RunContext
    ) -> Result:
        timer = CallTimer(ctx)
        self.calls.append(directive)
        attempt_id = f"attempt-{len(self.calls) + 1:03d}"
        for change in directive.get("strategy_changes") or []:
            if change.get("kind") != "next_page":
                continue
            fixture = self.pages.get(change.get("cursor") or "")
            if fixture is None:
                return timer.error(
                    make_issue(
                        "SOURCE_UNAVAILABLE",
                        f"没有为 cursor={change.get('cursor')!r} 准备 mock 结果。",
                        source="mock_search",
                    )
                )
            return timer.ok(
                outcome_from_search_result(
                    load_search_fixture(fixture),
                    profile,
                    attempt_id=attempt_id,
                )
            )
        return timer.error(
            make_issue(
                "NO_NEW_QUERY",
                "本次补搜指令没有可回放的下一页。",
                source="mock_search",
            )
        )
