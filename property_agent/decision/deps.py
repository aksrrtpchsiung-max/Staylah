"""Dependency injection. Business parameters do not carry connections or secrets; dependencies are passed in when constructing the service."""
from __future__ import annotations

from dataclasses import dataclass

from property_agent.clarification.boundaries import OnboardingHandoff
from property_agent.clarification.handoff import DefaultOnboardingHandoff
from property_agent.clarification.service import ClarificationAgent
from property_agent.clarification.stubs import PassthroughClarificationAdapter
from property_agent.contracts import ConversationProfile
from property_agent.decision.boundaries import (
    EvaluationModule,
    ProfileWriter,
    QuestionRepository,
    RecommendationRepository,
    RunRepository,
    SearchRunner,
)
from property_agent.decision.stubs import (
    InMemoryProfileWriter,
    InMemoryQuestionRepository,
    InMemoryRecommendationRepository,
    InMemoryRunRepository,
    ScriptedModuleC,
    ScriptedSearchRunner,
)


@dataclass
class DecisionDeps:
    module_c: EvaluationModule
    search_runner: SearchRunner
    profiles: ProfileWriter
    recommendations: RecommendationRepository
    questions: QuestionRepository
    runs: RunRepository
    clarification: ClarificationAgent
    onboarding_handoff: OnboardingHandoff
    # Backend-restricted source allowlist; the model cannot point to sites outside the list via alternate_source.
    allowed_sources: tuple[str, ...] = ("propertyguru",)


def build_stub_deps(profile: ConversationProfile | None = None) -> DecisionDeps:
    """Development-time dependencies: both module C and module B use stand-ins, and archiving and persistence use in-memory implementations."""
    writer = InMemoryProfileWriter()
    if profile is not None:
        writer.put(profile)
    clarification_adapter = PassthroughClarificationAdapter()
    return DecisionDeps(
        module_c=ScriptedModuleC(),
        search_runner=ScriptedSearchRunner(),
        profiles=writer,
        recommendations=InMemoryRecommendationRepository(),
        questions=InMemoryQuestionRepository(),
        runs=InMemoryRunRepository(),
        clarification=ClarificationAgent(
            polisher=clarification_adapter,
            interpreter=clarification_adapter,
        ),
        onboarding_handoff=DefaultOnboardingHandoff(),
    )
