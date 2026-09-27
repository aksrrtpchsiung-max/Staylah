"""Compatibility facade; implementations live in focused modules."""
from __future__ import annotations
import copy
import hashlib
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from property_agent.contracts import (
    ChatMessage,
    ConversationProfile,
    Recommendation,
    RelaxationProposal,
    RunContext,
)
from property_agent.decision.boundaries import ProfileVersionConflict
from property_agent.favorites import ConversationFavorite
from property_agent.profiles import apply_relaxation
from property_agent.persistence.models import (
    AgentRunRow,
    ConversationFavoriteRow,
    ConversationProfileRow,
    ConversationRow,
    MessageRow,
    ProfileMutationRow,
    RecommendationRow,
    RunQuestionRow,
)

from property_agent.persistence.repositories.common import (
    SessionFactory, _as_datetime, _short_hash,
)
from property_agent.persistence.repositories.profiles import (
    _profile_values, _profile_from_row, _write_profile, _row_matches_profile, SqlProfileRepository, SqlRequirementProfileRepository,
)
from property_agent.persistence.repositories.runs import (
    SqlRunRepository,
)
from property_agent.persistence.repositories.questions import (
    SqlQuestionRepository,
)
from property_agent.persistence.repositories.chat import (
    SqlChatRepository,
)
from property_agent.persistence.repositories.favorites import (
    SqlConversationFavoriteRepository,
)
from property_agent.persistence.repositories.recommendations import (
    SqlRecommendationRepository,
)
