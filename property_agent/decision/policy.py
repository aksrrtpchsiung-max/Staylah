"""RoutingPolicy 的默认值与校验。

数值来自接口评审稿的建议值，标注为待团队确认，不是实测吞吐或质量结论。
"""
from __future__ import annotations

from property_agent.contracts import ContractViolation, RoutingPolicy

# 会议建议值：达到 3 条合格候选即可发布，展示上限 10；首次搜索加两次补搜；模型内容修复共 1 次。
DEFAULT_POLICY: RoutingPolicy = {
    "min_matches": 3,
    "display_limit": 10,
    "max_search_attempts": 3,
    "max_repairs": 1,
}


def _require_count(policy: RoutingPolicy, key: str, minimum: int) -> int:
    value = policy.get(key)  # type: ignore[arg-type]
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ContractViolation(
            "INVALID_INPUT", f"policy.{key}", f"policy.{key} 必须是不小于 {minimum} 的整数"
        )
    return value


def validate_policy(policy: RoutingPolicy) -> None:
    """非法策略先拒绝，不进入路由判断。"""
    if not isinstance(policy, dict):
        raise ContractViolation("INVALID_INPUT", "policy", "policy 必须是对象")
    min_matches = _require_count(policy, "min_matches", 1)
    display_limit = _require_count(policy, "display_limit", 1)
    _require_count(policy, "max_search_attempts", 1)
    _require_count(policy, "max_repairs", 0)
    if display_limit < min_matches:
        raise ContractViolation(
            "INVALID_INPUT", "policy.display_limit", "display_limit 不能小于 min_matches"
        )
