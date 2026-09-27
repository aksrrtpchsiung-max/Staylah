"""Use synthetic SearchResult fixtures instead of real search, for integration testing of decision / follow-up questions / persistence.

This is not a production search/screen implementation. After other teams replace SearchRunner, the decision graph needs no changes.
"""

from tests.mock_search.pipeline import (
    FIXTURES,
    first_attempt_from_fixture,
    load_search_fixture,
    outcome_from_search_result,
)
from tests.mock_search.runner import MockSearchRunner
from tests.mock_search.screen import screen_listings

__all__ = [
    "FIXTURES",
    "MockSearchRunner",
    "first_attempt_from_fixture",
    "load_search_fixture",
    "outcome_from_search_result",
    "screen_listings",
]
