"""Assemble other teams' implementations with the PostgreSQL/follow-up Agent into decision dependencies."""
from __future__ import annotations

import os

from property_agent.clarification.deepseek import DeepSeekClarificationAdapter
from property_agent.clarification.handoff import DefaultOnboardingHandoff
from property_agent.clarification.service import ClarificationAgent
from property_agent.clarification.stubs import PassthroughClarificationAdapter
from property_agent.decision.boundaries import EvaluationModule, SearchRunner
from property_agent.decision.deps import DecisionDeps
from property_agent.decision.module_c import PartCEvaluationModule
from property_agent.persistence.repositories import (
    SessionFactory,
    SqlProfileRepository,
    SqlQuestionRepository,
    SqlRecommendationRepository,
    SqlRunRepository,
)


def build_postgres_deps(
    *,
    module_c: EvaluationModule | None = None,
    search_runner: SearchRunner | None = None,
    sessions: SessionFactory,
    use_deepseek: bool = True,
    allowed_sources: tuple[str, ...] = ("propertyguru",),
) -> DecisionDeps:
    if search_runner is None:
        from property_agent.integration import BSearchRunner

        search_runner = BSearchRunner()
    adapter = _clarification_adapter(use_deepseek=use_deepseek)
    return DecisionDeps(
        module_c=module_c if module_c is not None else PartCEvaluationModule(),
        search_runner=search_runner,
        profiles=SqlProfileRepository(sessions),
        recommendations=SqlRecommendationRepository(sessions),
        questions=SqlQuestionRepository(sessions),
        runs=SqlRunRepository(sessions),
        clarification=ClarificationAgent(
            polisher=adapter,
            interpreter=adapter,
        ),
        onboarding_handoff=DefaultOnboardingHandoff(),
        allowed_sources=allowed_sources,
    )


def _clarification_adapter(*, use_deepseek: bool):
    from property_agent.runtime.settings import load_runtime_settings

    key_env = load_runtime_settings().deepseek.api_key_env
    if use_deepseek and os.getenv(key_env):
        return DeepSeekClarificationAdapter.from_env()
    return PassthroughClarificationAdapter()
