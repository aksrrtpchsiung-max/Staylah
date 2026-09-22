"""开发期替身：模块 C 未完成，上游 B 与档案服务也在并行开发。

替身只用于联调和测试。它们实现与真实实现相同的 Protocol，因此接手时直接换注入对象，
不需要改节点代码。默认行为刻意保守：结构合法、事实必须有证据引用，但不声称具备
真实的推荐质量。
"""
from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass, field
from typing import Any

from property_agent.contracts import (
    AttemptSummary,
    Coverage,
    EvaluationResult,
    ListingSnapshot,
    Recommendation,
    RelaxationProposal,
    Result,
    RetrievalResult,
    ReviewResult,
    ReviewIssue,
    RoutingPolicy,
    RunContext,
    ScreenResult,
    SearchDirective,
    ConversationProfile,
)
from property_agent.decision.boundaries import ProfileVersionConflict
from property_agent.profiles import apply_relaxation, read_relaxable_value
from property_agent.results import CallTimer, make_issue


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def _stamp(result: Result, timer: CallTimer) -> Result:
    """测试里写的 Result 通常省略 meta，这里补齐，保持契约形状完整。"""
    if not result.get("meta"):
        result = {**result, "meta": timer.meta()}
    result.setdefault("issues", [])
    return result


@dataclass
class ScriptedModuleC:
    """模块 C 的替身。

    `evaluations` / `reviews` 按顺序弹出；用完后回落到默认实现，便于测试只脚本化
    自己关心的那一次调用。`*_calls` 记录入参，供断言修复上下文确实被传下去。
    """

    evaluations: list[Result] = field(default_factory=list)
    reviews: list[Result] = field(default_factory=list)
    evaluate_calls: list[dict] = field(default_factory=list)
    review_calls: list[dict] = field(default_factory=list)

    async def evaluate(
        self,
        profile: ConversationProfile,
        retrieval: RetrievalResult,
        screen_result: ScreenResult,
        listing_snapshot: ListingSnapshot,
        coverage: Coverage,
        repair_context: ReviewResult | None,
        *,
        policy: RoutingPolicy,
        ctx: RunContext,
    ) -> Result:
        timer = CallTimer(ctx)
        self.evaluate_calls.append(
            {
                "profile_version": profile["version"],
                "repair_context": repair_context,
                "eligible": [item["listing_key"] for item in screen_result["eligible"]],
            }
        )
        if self.evaluations:
            return _stamp(self.evaluations.pop(0), timer)
        return timer.ok(
            _default_evaluation(profile, screen_result, listing_snapshot, coverage, policy)
        )

    async def review(
        self,
        profile: ConversationProfile,
        evaluation: EvaluationResult,
        listing_snapshot: ListingSnapshot,
        *,
        policy: RoutingPolicy,
        ctx: RunContext,
    ) -> Result:
        timer = CallTimer(ctx)
        self.review_calls.append({"snapshot_id": evaluation["snapshot_id"]})
        if self.reviews:
            return _stamp(self.reviews.pop(0), timer)
        return timer.ok(_default_review(evaluation, listing_snapshot, policy))


def _price_of(listing: dict) -> int | None:
    price = listing.get("price") or {}
    return price.get("amount") if price.get("status") == "known" else None


def _default_evaluation(
    profile: ConversationProfile,
    screen_result: ScreenResult,
    listing_snapshot: ListingSnapshot,
    coverage: Coverage,
    policy: RoutingPolicy,
) -> EvaluationResult:
    """按 eligible 的原顺序成列，每条只给一个有证据的价格事实。"""
    by_key = {item["listing_key"]: item for item in listing_snapshot["items"]}
    ordered = []
    for rank, screened in enumerate(screen_result["eligible"], start=1):
        key = screened["listing_key"]
        listing = by_key.get(key, {})
        price = listing.get("price") or {}
        ordered.append(
            {
                "listing_key": key,
                "rank": rank,
                "reasons": [
                    {
                        "kind": "fact",
                        "text": f"来源显示租金 {price.get('currency')} {price.get('amount')}，未超预算。",
                        "evidence_ids": list(price.get("evidence_ids") or []),
                    }
                ],
                "tradeoffs": [],
                "unknowns": ["当前可租状态尚未向经纪人核实"],
            }
        )

    recommendation: Recommendation = {
        "ordered_items": ordered,
        "summary": f"本次共 {len(ordered)} 条合格候选，顺序由评价模块给出。",
        "limitations": ["开发替身生成；仅覆盖本次查询；来源状态不等于独立核实"],
    }

    findings = [
        f"{item['listing_key']}：{check['field']} {check['status']}"
        for item in screen_result.get("rejected", [])
        for check in item.get("checks", [])
        if check.get("status") == "fail"
    ]

    short = len(ordered) < policy["min_matches"]
    directive = _suggest_directive(profile, coverage) if short else None
    proposals = (
        _suggest_price_relaxation(profile, screen_result, listing_snapshot) if short else []
    )
    if not short:
        next_action, next_reason = "publish", "enough_matches"
    elif directive is not None:
        next_action, next_reason = "research", directive["reason_code"]
    elif proposals:
        next_action, next_reason = "ask_user", "relaxation_available"
    else:
        next_action, next_reason = "finish", "insufficient_candidates"
    return {
        "profile_version": profile["version"],
        "snapshot_id": listing_snapshot["snapshot_id"],
        "recommendation": recommendation,
        "assessment": {
            "constraint_findings": findings,
            "search_directive": directive,
            "relaxation_proposals": proposals,
            "next_action": next_action,
            "next_reason_code": next_reason,
        },
    }


def _suggest_directive(profile: ConversationProfile, coverage: Coverage) -> SearchDirective | None:
    """只建议改变查找方法：还有下一页就翻页，硬条件保持不变。"""
    next_pages = coverage.get("next_pages") or []
    if not next_pages:
        return None
    return {
        "reason_code": "insufficient_candidates",
        "strategy_changes": [dict(page) for page in next_pages[:1]],
        "base_profile_version": profile["version"],
        "evidence_listing_keys": [],
    }


def _suggest_price_relaxation(
    profile: ConversationProfile, screen_result: ScreenResult, listing_snapshot: ListingSnapshot
) -> list[RelaxationProposal]:
    """依据本轮被价格筛除的记录提出提案；仍须用户明确确认才生效。"""
    try:
        current = read_relaxable_value(profile, "listing_constraints.price.amount")
    except KeyError:
        return []
    if not isinstance(current, int):
        return []
    by_key = {item["listing_key"]: item for item in listing_snapshot["items"]}
    over_budget = []
    for screened in screen_result.get("rejected", []):
        if not any(
            check.get("field") == "price.amount" and check.get("status") == "fail"
            for check in screened.get("checks", [])
        ):
            continue
        amount = _price_of(by_key.get(screened["listing_key"], {}))
        if isinstance(amount, int) and amount > current:
            over_budget.append((amount, screened["listing_key"]))
    if not over_budget:
        return []
    amount, key = min(over_budget)
    return [
        {
            "proposal_id": f"relax-price-{amount}",
            "field": "listing_constraints.price.amount",
            "old_value": current,
            "proposed_value": amount,
            "reason": f"本轮被排除的 {key} 租金为 {amount}，提高到该值可能扩大匹配，仍需重新查询。",
            "evidence_listing_keys": [key],
            "requires_user_confirmation": True,
        }
    ]


def _default_review(
    evaluation: EvaluationResult, listing_snapshot: ListingSnapshot, policy: RoutingPolicy
) -> ReviewResult:
    """替身只做确定性结构与引用校验，不做语义 Reflection。"""
    known_keys = {item["listing_key"] for item in listing_snapshot["items"]}
    known_evidence = {
        evidence["evidence_id"]
        for item in listing_snapshot["items"]
        for evidence in item.get("evidence", [])
    }
    items = evaluation["recommendation"]["ordered_items"]
    issues: list[ReviewIssue] = []

    for index, item in enumerate(items):
        base = f"recommendation.ordered_items[{index}]"
        if item["listing_key"] not in known_keys:
            issues.append(
                {
                    "code": "UNKNOWN_LISTING",
                    "listing_key": item["listing_key"],
                    "field_path": f"{base}.listing_key",
                    "message": "推荐引用了本轮快照之外的房源。",
                    "severity": "blocking",
                    "suggested_fix": "只引用本次运行的候选证据。",
                }
            )
        for claim_field in ("reasons", "tradeoffs"):
            for claim_index, claim in enumerate(item.get(claim_field, [])):
                if claim.get("kind") != "fact":
                    continue
                ids = claim.get("evidence_ids") or []
                if not ids or any(evidence_id not in known_evidence for evidence_id in ids):
                    issues.append(
                        {
                            "code": "UNSUPPORTED_CLAIM",
                            "listing_key": item["listing_key"],
                            "field_path": f"{base}.{claim_field}[{claim_index}]",
                            "message": "事实型陈述缺少可解析的证据引用。",
                            "severity": "blocking",
                            "suggested_fix": "删除该事实或补充真实证据后重新审查。",
                        }
                    )

    if [item.get("rank") for item in items] != list(range(1, len(items) + 1)):
        issues.append(
            {
                "code": "INVALID_RANK",
                "listing_key": None,
                "field_path": "recommendation.ordered_items",
                "message": "名次不是从 1 开始的连续整数。",
                "severity": "blocking",
                "suggested_fix": "重排名次，保持模型给出的相对顺序。",
            }
        )

    if len(items) > policy["display_limit"]:
        issues.append(
            {
                "code": "TOO_MANY_ITEMS",
                "listing_key": None,
                "field_path": "recommendation.ordered_items",
                "message": "推荐条数超过展示上限。",
                "severity": "warning",
                "suggested_fix": "由发布侧按展示上限截断。",
            }
        )

    blocking = [issue for issue in issues if issue["severity"] == "blocking"]
    return {"passed": not blocking, "issues": issues}


@dataclass
class ScriptedSearchRunner:
    """模块 B 一侧的替身。未脚本化的调用返回明确错误，而不是静默造数据。"""

    outcomes: list[Result] = field(default_factory=list)
    calls: list[SearchDirective] = field(default_factory=list)
    histories: list[list[AttemptSummary]] = field(default_factory=list)

    async def run_attempt(
        self,
        directive: SearchDirective,
        profile: ConversationProfile,
        *,
        previous_attempts: list[AttemptSummary],
        ctx: RunContext,
    ) -> Result:
        timer = CallTimer(ctx)
        self.calls.append(directive)
        self.histories.append(copy.deepcopy(previous_attempts))
        if not self.outcomes:
            return timer.error(
                make_issue(
                    "SOURCE_UNAVAILABLE",
                    "搜索替身没有为本次补搜准备结果；不回落到模拟数据。",
                    source="stub",
                )
            )
        return _stamp(self.outcomes.pop(0), timer)


@dataclass
class InMemoryProfileWriter:
    """档案服务的替身：乐观锁 + 按 op_key 幂等。"""

    profiles: dict[str, ConversationProfile] = field(default_factory=dict)
    _applied: dict[str, ConversationProfile] = field(default_factory=dict)

    def put(self, profile: ConversationProfile) -> None:
        self.profiles[profile["profile_id"]] = profile

    def current_version(self, profile_id: str) -> int:
        return self.profiles[profile_id]["version"]

    def apply_relaxation(
        self,
        profile_id: str,
        *,
        base_version: int,
        proposal: RelaxationProposal,
        source_message_id: str,
        op_key: str,
    ) -> ConversationProfile:
        if op_key in self._applied:
            return self._applied[op_key]
        current = self.profiles[profile_id]
        if current["version"] != base_version:
            raise ProfileVersionConflict(f"档案已是 v{current['version']}，提案基于 v{base_version}")

        updated = apply_relaxation(
            current, proposal=proposal, source_message_id=source_message_id
        )
        self.profiles[profile_id] = updated
        self._applied[op_key] = updated
        return updated


@dataclass
class InMemoryRecommendationRepository:
    """推荐交付的替身。同一 op_key 重复提交返回原结果，不重复插入。"""

    saved: dict[str, Recommendation] = field(default_factory=dict)
    _by_op: dict[str, str] = field(default_factory=dict)

    def save(
        self, run_id: str, recommendation: Recommendation, *, op_key: str
    ) -> tuple[str, bool]:
        if op_key in self._by_op:
            return self._by_op[op_key], True
        final_result_id = f"rec-{run_id}-{_short_hash(op_key)}"
        self.saved[final_result_id] = recommendation
        self._by_op[op_key] = final_result_id
        return final_result_id, False


@dataclass
class InMemoryRunRepository:
    runs: dict[str, dict[str, Any]] = field(default_factory=dict)

    def update(
        self,
        run_id: str,
        *,
        status: str,
        state_version: int | None = None,
        completion_reason: str | None = None,
        final_result_id: str | None = None,
    ) -> None:
        current = self.runs.setdefault(run_id, {})
        current.update(
            {
                "status": status,
                "completion_reason": completion_reason,
            }
        )
        if state_version is not None:
            current["state_version"] = state_version
        if final_result_id is not None:
            current["final_result_id"] = final_result_id


@dataclass
class InMemoryQuestionRepository:
    """提问与回答消费的替身。按 question_id 幂等；旧回答只能被拒绝一次以外的重复消费。"""

    questions: dict[str, dict] = field(default_factory=dict)
    answered: set[str] = field(default_factory=set)
    answers: dict[str, dict[str, str | None]] = field(default_factory=dict)

    def save_question(self, run_id: str, question: dict) -> dict:
        key = f"{run_id}:{question['question_id']}"
        return self.questions.setdefault(key, question)

    def mark_answered(
        self,
        run_id: str,
        question_id: str,
        *,
        client_message_id: str | None = None,
        answer_text: str | None = None,
    ) -> bool:
        key = f"{run_id}:{question_id}"
        if key not in self.questions:
            return False
        if key in self.answered:
            return bool(
                client_message_id
                and self.answers.get(key, {}).get("client_message_id")
                == client_message_id
                and self.answers.get(key, {}).get("answer_text") == answer_text
            )
        self.answered.add(key)
        self.answers[key] = {
            "client_message_id": client_message_id,
            "answer_text": answer_text,
        }
        return True
