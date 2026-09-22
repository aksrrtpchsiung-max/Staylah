"""把其他团队实现与 PostgreSQL/追问 Agent 组装成 decision 依赖。"""
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
    search_runner: SearchRunner,
    sessions: SessionFactory,
    use_deepseek: bool = True,
    allowed_sources: tuple[str, ...] = ("propertyguru",),
) -> DecisionDeps:
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
    if use_deepseek and os.getenv("DEEPSEEK_API_KEY"):
        return DeepSeekClarificationAdapter.from_env()
    return PassthroughClarificationAdapter()
