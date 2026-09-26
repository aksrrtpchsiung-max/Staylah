"""Type signatures for executable contract examples; these are documentation stubs.

All shared types are imported from the canonical property_agent.contracts module."""
from property_agent.contracts import *  # noqa: F401,F403

# 依赖（模型、词表、Provider 注册表等）由服务构造时注入，业务参数不携带连接。
# 下列仅是签名；不提供假实现。
async def onboard(request: UserMessage, profile: ConversationProfile,
                  messages: list[ChatMessage], *, ctx: RunContext) -> Result[OnboardResult]:
    """由 A 提取本轮 patch，并生成等待用户确认的草稿画像。"""
    raise NotImplementedError


async def confirm_requirements(reply: ConfirmationReply, profile: ConversationProfile,
                               *, ctx: RunContext) -> Result[ConfirmationResult]:
    """由 A 处理用户确认、修订或取消，并控制是否允许交给 B。"""
    raise NotImplementedError


async def fulfill_requirements(request: RequirementRequest, *,
                               ctx: RunContext) -> Result[RequirementFulfillment]:
    """作为 B 的唯一公开入口；B 内部自行选择数据库、CLI 或其他工具。"""
    raise NotImplementedError


async def prepare_query(profile: ConversationProfile, *, ctx: RunContext) -> Result[QueryFeatures]:
    """由 B 内部把确认画像转换为实体与语义查询特征。"""
    raise NotImplementedError


async def build_search_plan(profile: ConversationProfile, query: QueryFeatures,
                            previous_attempts: list[AttemptSummary],
                            directive: SearchDirective | None, *,
                            ctx: RunContext) -> Result[SearchPlan]:
    """由 B 内部选择数据源并构建搜索计划。"""
    raise NotImplementedError


async def search(plan: SearchPlan, *, ctx: RunContext) -> Result[SearchResult]:
    """由 B 内部执行搜索计划并返回统一房源结构。"""
    raise NotImplementedError


def screen(listings: list[Listing], profile: ConversationProfile) -> ScreenResult:
    raise NotImplementedError


async def retrieve(query: QueryFeatures, eligible_listings: list[Listing], *,
                   top_k: int, ctx: RunContext) -> Result[RetrievalResult]:
    raise NotImplementedError


async def evaluate(profile: ConversationProfile, retrieval: RetrievalResult,
                   screen_result: ScreenResult, listing_snapshot: ListingSnapshot,
                   coverage: Coverage, repair_context: ReviewResult | None, *,
                   policy: RoutingPolicy, ctx: RunContext) -> Result[EvaluationResult]:
    raise NotImplementedError


async def review(profile: ConversationProfile, evaluation: EvaluationResult,
                 listing_snapshot: ListingSnapshot, *, policy: RoutingPolicy,
                 ctx: RunContext) -> Result[ReviewResult]:
    raise NotImplementedError


def decide_next(state: DecisionState, policy: RoutingPolicy) -> RouteDecision:
    raise NotImplementedError
