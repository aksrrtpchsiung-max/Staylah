"""Compatibility facade; implementations live in focused modules."""
from __future__ import annotations
import copy
from collections.abc import Awaitable, Callable, Sequence
from time import monotonic
from typing import Any, Literal, Protocol, TypedDict
from property_agent.evaluation import service as part_c
from property_agent.search.execution.history import query_fingerprint
from property_agent.search.aggregation.requirements import build_fulfillment
from property_agent.contracts import (
    AttemptSummary,
    Clarification,
    ContractViolation,
    ConversationProfile,
    Issue,
    QueryFeatures,
    RequirementCoverage,
    RequirementFulfillment,
    RequirementRequest,
    Result,
    RunContext,
    ScreenResult,
    SearchDirective,
    SearchPlan,
    SearchResult,
)
from property_agent.decision.boundaries import AttemptOutcome
from property_agent.evaluation_trace import record_event, stage_span
from property_agent.results import CallTimer, is_usable, make_issue

from property_agent.integration.boundaries import (
    Planner, SearchService, FulfillmentService, BCTransition, QueryPreparer, RetrieveFunction,
)
from property_agent.integration.attempts import (
    _copy_error, _failed_attempt, _requirement_request, BCAttemptAdapter,
)
from property_agent.integration.runner import (
    BSearchRunner,
)
