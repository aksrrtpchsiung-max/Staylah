"""Compatibility facade; implementations live in focused modules."""
from __future__ import annotations
import asyncio
import json
import re
from datetime import datetime, timedelta
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Any, Iterable, Protocol
from property_agent.runtime.model_client import (
    DeepSeekChatClient,
    DeepSeekChatError,
    ModelConfigurationError,
    create_deepseek_client,
    load_model_settings,
)
from property_agent.contracts import (
    Assessment,
    Claim,
    ConversationProfile,
    ContractViolation,
    Coverage,
    DecisionState,
    EvaluationResult,
    Evidence,
    Issue,
    Listing,
    ListingConstraint,
    ListingSnapshot,
    Result,
    RetrievalCandidate,
    RetrievalResult,
    ReviewIssue,
    ReviewResult,
    RouteDecision,
    RoutingPolicy,
    RunContext,
    ScreenResult,
    ScreenedListing,
    SearchDirective,
    QueryFeatures,
)

from property_agent.evaluation.types import (
    DEFAULT_LLM_MODEL, LLM_METHOD_VERSION, DETERMINISTIC_METHOD_VERSION, FRESHNESS_DAYS, MAX_EVALUATION_CANDIDATES, MAX_RECOMMENDATIONS, KeywordMatcherError, EvaluationReviewModelError, KeywordMatch, KeywordMatcher, EvaluationDecision, EvaluationReviewModel,
)
from property_agent.evaluation.keyword_model import (
    DeepSeekKeywordMatcher,
)
from property_agent.evaluation.review_model import (
    DeepSeekEvaluationReviewModel,
)
from property_agent.evaluation.configuration import (
    _keyword_matcher, _auto_deepseek_matcher, _evaluation_review_model, _auto_evaluation_review_model, configure_keyword_matcher, configure_deepseek_keyword_matcher, configure_evaluation_review_model, configure_deepseek_evaluation_review_model, _resolve_keyword_matcher, _resolve_evaluation_review_model,
)
from property_agent.evaluation.validation import (
    _violation, _validate_policy, _validate_profile, _validate_listing, _evidence_ids, _listing_field_value, _constraint_matches,
)
from property_agent.evaluation.results import (
    _meta, _success, _partial, _error,
)
from property_agent.evaluation.screening import (
    screen,
)
from property_agent.evaluation.retrieval import (
    _retrieval_result, _requirement_weight, _deterministic_requirement_rows, _deterministic_retrieval_fallback, retrieve,
)
from property_agent.evaluation.recommendations import (
    _soft_preference_score, _fact_claim, _recommendation_item, _make_directive,
)
from property_agent.evaluation.evaluation import (
    evaluate,
)
from property_agent.evaluation.evidence import (
    _parse_timestamp, _is_stale, _review_issue,
)
from property_agent.evaluation.review import (
    review,
)
from property_agent.evaluation.routing import (
    _route, decide_next,
)

__all__ = [
    "DEFAULT_LLM_MODEL",
    "DETERMINISTIC_METHOD_VERSION",
    "DeepSeekChatClient",
    "DeepSeekChatError",
    "DeepSeekKeywordMatcher",
    "DeepSeekEvaluationReviewModel",
    "EvaluationDecision",
    "EvaluationReviewModel",
    "EvaluationReviewModelError",
    "KeywordMatch",
    "KeywordMatcher",
    "KeywordMatcherError",
    "configure_deepseek_keyword_matcher",
    "configure_deepseek_evaluation_review_model",
    "configure_evaluation_review_model",
    "configure_keyword_matcher",
    "screen",
    "retrieve",
    "evaluate",
    "review",
    "decide_next",
]
