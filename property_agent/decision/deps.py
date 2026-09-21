"""依赖注入。业务参数不携带连接或密钥，依赖在构造服务时传入。"""
from __future__ import annotations

from dataclasses import dataclass

from property_agent.clarification.boundaries import OnboardingHandoff
from property_agent.clarification.handoff import DefaultOnboardingHandoff
from property_agent.clarification.service import ClarificationAgent
from property_agent.clarification.stubs import PassthroughClarificationAdapter
from property_agent.contracts import UserProfile
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
    # 后端限定的来源白名单；模型不能通过 alternate_source 指向名单外的站点。
    allowed_sources: tuple[str, ...] = ("propertyguru",)


def build_stub_deps(profile: UserProfile | None = None) -> DecisionDeps:
    """开发期依赖：模块 C 与模块 B 都用替身，档案与持久化走内存实现。"""
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
