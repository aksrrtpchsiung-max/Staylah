"""本模块与外部的接缝：模块 C、模块 B、档案写入和持久化。

Protocol 只描述我们依赖的调用形状，实现由各自负责方提供。`evaluate` 和 `review`
的签名与 contracts_v0 完全一致，C 完成后应当可以直接替换 stub。

AttemptOutcome 和 NextRunRequest 是**新增的内部交付对象**，不属于已冻结的公共契约；
它们只在模块 A 内部和与运行控制层之间传递，接口评审确认后再决定是否公开。
"""
from __future__ import annotations

from typing import Protocol, TypedDict

from property_agent.contracts import (
    Coverage,
    EvaluationResult,
    ListingSnapshot,
    Recommendation,
    RelaxationProposal,
    Result,
    RetrievalResult,
    ReviewResult,
    RoutingPolicy,
    RunContext,
    ScreenResult,
    SearchDirective,
    UserProfile,
)


class EvaluationModule(Protocol):
    """模块 C：评价与审查。两个方法都不写业务库，也不自行决定下一步。"""

    async def evaluate(
        self,
        profile: UserProfile,
        retrieval: RetrievalResult,
        screen_result: ScreenResult,
        listing_snapshot: ListingSnapshot,
        coverage: Coverage,
        repair_context: ReviewResult | None,
        *,
        policy: RoutingPolicy,
        ctx: RunContext,
    ) -> Result: ...

    async def review(
        self,
        profile: UserProfile,
        evaluation: EvaluationResult,
        listing_snapshot: ListingSnapshot,
        *,
        policy: RoutingPolicy,
        ctx: RunContext,
    ) -> Result: ...


class AttemptOutcome(TypedDict):
    """一次搜索尝试的结果集合（内部对象）。

    attempt_id 由共享运行控制层分配；模块 A 的决策段只消费，不自行生成或清零计数。
    """

    attempt_id: str
    search_status: str  # success | partial | error
    failure_code: str | None
    listing_snapshot: ListingSnapshot | None
    screen_result: ScreenResult | None
    retrieval_result: RetrievalResult | None
    coverage: Coverage | None


class SearchRunner(Protocol):
    """模块 B 一侧：按保持硬条件的补搜指令执行下一次尝试。"""

    async def run_attempt(
        self, directive: SearchDirective, profile: UserProfile, *, ctx: RunContext
    ) -> Result: ...


class ProfileVersionConflict(Exception):
    """档案已被别的操作改过；旧回答不能覆盖新需求。"""


class ProfileWriter(Protocol):
    """档案写入由模块 A 的档案服务负责；本段只在用户明确接受提案后调用。"""

    def current_version(self, profile_id: str) -> int: ...

    def apply_relaxation(
        self,
        profile_id: str,
        *,
        base_version: int,
        proposal: RelaxationProposal,
        source_message_id: str,
        op_key: str,
    ) -> UserProfile:
        """按 op_key 幂等；base_version 与当前版本不一致时抛 ProfileVersionConflict。"""
        ...


class NextRunRequest(TypedDict):
    """旧 run 被取代时交给运行控制层的新任务请求（内部对象）。"""

    reason_code: str
    profile_id: str
    profile_version: int
    superseded_run_id: str
    accepted_proposal_id: str | None
    source_message_id: str | None


class RecommendationRepository(Protocol):
    def save(
        self, run_id: str, recommendation: Recommendation, *, op_key: str
    ) -> tuple[str, bool]:
        """返回 (final_result_id, 是否为重放)。重放不得重复插入推荐。"""
        ...


class RunRepository(Protocol):
    def update(
        self,
        run_id: str,
        *,
        status: str,
        state_version: int | None = None,
        completion_reason: str | None = None,
        final_result_id: str | None = None,
    ) -> None: ...


class QuestionRepository(Protocol):
    def save_question(self, run_id: str, question: dict) -> dict:
        """按 question_id 幂等保存；节点重跑不重复发问。"""
        ...

    def mark_answered(
        self,
        run_id: str,
        question_id: str,
        *,
        client_message_id: str | None = None,
        answer_text: str | None = None,
    ) -> bool:
        """首次消费并保存回答返回 True；重复回答或旧问题返回 False。"""
        ...
