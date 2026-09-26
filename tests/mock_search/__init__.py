"""用合成 SearchResult 夹具代替真实搜索，供联调 decision / 追问 / 持久化。

这不是生产 search/screen 实现。其他团队替换 SearchRunner 后，decision 图无需改动。
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
