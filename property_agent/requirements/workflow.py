"""Compatibility facade; implementations live in focused modules."""
from __future__ import annotations
import copy
import json
import os
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, Protocol
import httpx
from .deepseek_parser import DeepSeekAPIError, DeepSeekConfigurationError, DeepSeekParserConfig
from .models import (
    ConversationProfileModel,
    InputGuardDecision,
    Intent,
    IssueCode,
    NormalizedRequirement,
    PreferenceRequirement,
    PreferenceTopic,
    ProfileChangeModel,
    RequirementConfirmationModel,
    RequirementGraphState,
    RequirementRequestModel,
)
from .response_renderer import ResponseRenderer
from .workflow_constants import DEFAULT_USER_ID, FALCON_SCOPE_MESSAGE, SAFE_ERROR_MESSAGE

from property_agent.requirements.constants import (
    MAX_INPUT_CHARS, QUERYABLE_LISTING_FIELDS, LISTING_OPERATORS, FURNISHING_VALUES, FURNISHING_RULES, DERIVED_CATEGORIES, DERIVED_OPERATORS, OPEN_REQUIREMENT_HANDLING, PROFILE_FACT_FIELDS,
)
from property_agent.requirements.support import (
    utc_now, _error_update,
)
from property_agent.requirements.input_guard import (
    InputGuard, DeepSeekInputGuard, make_validate_input_node,
)
from property_agent.requirements.profile_repository import (
    ProfileRepository, InMemoryProfileRepository,
)
from property_agent.requirements.patches import (
    normalized_requirement_to_patch, _listing_constraint, _derived_requirements, _open_data_requirement, _open_data_requirements, _derived, _furnishing_value, _preference_listing_constraints,
)
from property_agent.requirements.validation import (
    validate_patch, detect_conflicts, _validate_listing_constraint, _validate_derived_requirement, _validate_open_data_requirement, _validate_profile_fact, _validate_source,
)
from property_agent.requirements.profile import (
    make_merge_profile_node, _upsert, _profile_fact_identity,
)
from property_agent.requirements.progress import (
    route_workflow, assess_completeness, select_clarification, recover_error,
)
from property_agent.requirements.confirmation import (
    handle_confirmation, generate_confirmation, make_persist_confirmed_profile_node, build_requirement_request,
)
from property_agent.requirements.presentation import (
    _profile_summary,
)
