"""Replay the next page of mock SearchResult according to the supplementary search instruction."""
from __future__ import annotations

from dataclasses import dataclass, field

from property_agent.contracts import AttemptSummary, ConversationProfile, Result, RunContext, SearchDirective
from tests.mock_search.pipeline import load_search_fixture, outcome_from_search_result
from property_agent.results import CallTimer, make_issue


@dataclass
class MockSearchRunner:
    """Implement SearchRunner: map next_page.cursor to a fixed fixture."""

    pages: dict[str, str] = field(
        default_factory=lambda: {"mock-page-2": "page-2", "page-2": "page-2"}
    )
    calls: list[SearchDirective] = field(default_factory=list)
    histories: list[list[AttemptSummary]] = field(default_factory=list)

    async def run_attempt(
        self,
        directive: SearchDirective,
        profile: ConversationProfile,
        *,
        previous_attempts: list[AttemptSummary],
        ctx: RunContext,
    ) -> Result:
        timer = CallTimer(ctx)
        self.calls.append(directive)
        self.histories.append(list(previous_attempts))
        attempt_id = f"attempt-{len(previous_attempts) + 1:03d}"
        for change in directive.get("strategy_changes") or []:
            if change.get("kind") != "next_page":
                continue
            fixture = self.pages.get(change.get("cursor") or "")
            if fixture is None:
                return timer.error(
                    make_issue(
                        "SOURCE_UNAVAILABLE",
                        f"No mock result prepared for cursor={change.get('cursor')!r}.",
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
                "There is no replayable next page for this supplementary search instruction.",
                source="mock_search",
            )
        )
