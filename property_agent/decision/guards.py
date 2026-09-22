"""程序侧护栏：模块 C 的输出在进入路由之前必须先过这一层。

模型可以建议"再翻一页"或"把预算提到 3600"，但不能借这两种输出绕过硬条件：
- 补搜指令只允许改变查找方法，必须挂在本 run 固定的需求版本上；
- 让步提案必须是真正的放宽、必须针对白名单字段、必须等用户明确答复才生效。

非法输出在这里被丢弃并记录 issue，不向 decide_next 传递——否则一次模型抖动
就会变成 INTERNAL_ERROR。
"""
from __future__ import annotations

from typing import Any, Sequence

from property_agent.contracts import (
    ConversationProfile,
    Issue,
    Recommendation,
    RelaxationProposal,
    SearchDirective,
)
from property_agent.profiles import (
    RELAXABLE_FIELDS,
    is_relaxation,
    read_relaxable_value,
)
from property_agent.results import make_issue

DIRECTIVE_REASONS = frozenset({"insufficient_candidates", "incomplete_coverage"})

# 每种策略调整必须带的字段。联合类型本身不能表达"改预算"，白名单再确认一次。
STRATEGY_REQUIRED_KEYS = {
    "next_page": ("query_id", "cursor"),
    "alias_query": ("entity_id", "alias"),
    "alternate_source": ("source",),
}

def sanitize_proposals(
    proposals: Sequence[Any] | None, profile: ConversationProfile
) -> tuple[list[RelaxationProposal], list[Issue]]:
    """返回可以拿去问用户的提案，以及被拒绝原因。提案本身绝不在这里生效。"""
    issues: list[Issue] = []
    accepted: list[RelaxationProposal] = []
    seen_ids: set[str] = set()

    for index, proposal in enumerate(proposals or []):
        path = f"assessment.relaxation_proposals[{index}]"
        if not isinstance(proposal, dict):
            issues.append(make_issue("INVALID_OUTPUT", "让步提案必须是对象", field_path=path))
            continue

        proposal_id = proposal.get("proposal_id")
        if not isinstance(proposal_id, str) or not proposal_id.strip():
            issues.append(
                make_issue("INVALID_OUTPUT", "提案缺少 proposal_id", field_path=f"{path}.proposal_id")
            )
            continue
        if proposal_id in seen_ids:
            issues.append(
                make_issue("INVALID_OUTPUT", "提案 ID 重复", field_path=f"{path}.proposal_id")
            )
            continue

        if proposal.get("requires_user_confirmation") is not True:
            issues.append(
                make_issue(
                    "CONSTRAINT_CHANGE_NOT_ALLOWED",
                    "放宽硬条件必须标记为需要用户确认",
                    field_path=f"{path}.requires_user_confirmation",
                )
            )
            continue

        field = proposal.get("field")
        if field not in RELAXABLE_FIELDS:
            issues.append(
                make_issue(
                    "CONSTRAINT_CHANGE_NOT_ALLOWED",
                    f"字段 {field!r} 不在可放宽白名单内",
                    field_path=f"{path}.field",
                )
            )
            continue

        try:
            current = read_relaxable_value(profile, field)
        except KeyError:
            issues.append(
                make_issue("INVALID_OUTPUT", "提案字段在档案中不存在", field_path=f"{path}.field")
            )
            continue

        # 提案必须基于用户当前确认的值构造，否则可能把旧结论套到新需求上。
        if proposal.get("old_value") != current:
            issues.append(
                make_issue(
                    "STATE_CONFLICT",
                    "提案的 old_value 与当前档案不一致",
                    field_path=f"{path}.old_value",
                )
            )
            continue

        if not is_relaxation(field, current, proposal.get("proposed_value")):
            issues.append(
                make_issue(
                    "CONSTRAINT_CHANGE_NOT_ALLOWED",
                    "提案不是可识别的放宽方向",
                    field_path=f"{path}.proposed_value",
                )
            )
            continue

        seen_ids.add(proposal_id)
        accepted.append(proposal)  # type: ignore[arg-type]

    return accepted, issues


def sanitize_directive(
    directive: Any,
    *,
    profile_version: int,
    allowed_sources: Sequence[str],
) -> tuple[SearchDirective | None, list[Issue]]:
    """补搜指令通过则原样返回；任何一项不合法就整条丢弃。"""
    path = "assessment.search_directive"
    if directive is None:
        return None, []
    if not isinstance(directive, dict):
        return None, [make_issue("INVALID_OUTPUT", "补搜指令必须是对象", field_path=path)]

    if directive.get("reason_code") not in DIRECTIVE_REASONS:
        return None, [
            make_issue(
                "INVALID_OUTPUT", "补搜指令的 reason_code 非法", field_path=f"{path}.reason_code"
            )
        ]

    if directive.get("base_profile_version") != profile_version:
        return None, [
            make_issue(
                "STATE_CONFLICT",
                "补搜指令挂在别的需求版本上",
                field_path=f"{path}.base_profile_version",
            )
        ]

    changes = directive.get("strategy_changes")
    if not isinstance(changes, list) or not changes:
        return None, [
            make_issue(
                "INVALID_OUTPUT",
                "补搜指令必须给出至少一项策略调整",
                field_path=f"{path}.strategy_changes",
            )
        ]

    for index, change in enumerate(changes):
        change_path = f"{path}.strategy_changes[{index}]"
        kind = change.get("kind") if isinstance(change, dict) else None
        required = STRATEGY_REQUIRED_KEYS.get(kind)  # type: ignore[arg-type]
        if required is None:
            return None, [
                make_issue(
                    "CONSTRAINT_CHANGE_NOT_ALLOWED",
                    f"策略调整 {kind!r} 不在允许的查找方法内",
                    field_path=f"{change_path}.kind",
                )
            ]
        for key in required:
            if not isinstance(change.get(key), str) or not change[key].strip():
                return None, [
                    make_issue(
                        "INVALID_OUTPUT", f"{kind} 缺少 {key}", field_path=f"{change_path}.{key}"
                    )
                ]
        if kind == "alternate_source" and change["source"] not in allowed_sources:
            return None, [
                make_issue(
                    "SOURCE_UNAVAILABLE",
                    f"来源 {change['source']!r} 不在后端允许范围内",
                    field_path=f"{change_path}.source",
                )
            ]

    if not isinstance(directive.get("evidence_listing_keys"), list):
        return None, [
            make_issue(
                "INVALID_OUTPUT",
                "evidence_listing_keys 必须是数组",
                field_path=f"{path}.evidence_listing_keys",
            )
        ]

    return directive, []  # type: ignore[return-value]


def prepare_for_display(
    recommendation: Recommendation, *, display_limit: int, eligible_keys: set[str]
) -> tuple[Recommendation, list[Issue]]:
    """发布前的最后一道程序检查：展示上限、名次连续、候选归属。

    这里不重排模型给出的顺序，只截断和拒绝越界候选。
    """
    issues: list[Issue] = []
    items = list(recommendation.get("ordered_items") or [])

    for index, item in enumerate(items):
        if item.get("listing_key") not in eligible_keys:
            issues.append(
                make_issue(
                    "INVALID_OUTPUT",
                    f"推荐引用了不在 B 移交候选内的 {item.get('listing_key')!r}",
                    field_path=f"recommendation.ordered_items[{index}].listing_key",
                )
            )

    ranks = [item.get("rank") for item in items]
    if ranks != list(range(1, len(items) + 1)):
        issues.append(
            make_issue("INVALID_OUTPUT", "推荐名次不是从 1 开始的连续整数", field_path="recommendation.ordered_items")
        )

    if issues:
        return recommendation, issues

    if len(items) > display_limit:
        trimmed = dict(recommendation)
        trimmed["ordered_items"] = items[:display_limit]
        trimmed["limitations"] = [
            *recommendation.get("limitations", []),
            f"本次仅展示前 {display_limit} 条，B 候选共 {len(items)} 条。",
        ]
        return trimmed, []  # type: ignore[return-value]

    return recommendation, []
