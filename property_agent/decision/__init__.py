"""模块 A 的下半段：消费模块 C 的评价与审查结果，决定澄清、求让步还是交付推荐。

`decide_next` 是唯一的路由决策点；其余节点只负责取数、持久化和交付。
"""
from property_agent.decision.decide import decide_next, validate_decision_state
from property_agent.decision.deps import DecisionDeps, build_stub_deps
from property_agent.decision.graph import build_decision_graph, initial_state
from property_agent.decision.part_c_adapter import PartCEvaluationModule, confirmed_profile
from property_agent.decision.policy import DEFAULT_POLICY, validate_policy
from property_agent.decision.runtime import postgres_decision_graph, thread_config

__all__ = [
    "decide_next",
    "validate_decision_state",
    "DEFAULT_POLICY",
    "validate_policy",
    "DecisionDeps",
    "build_stub_deps",
    "build_decision_graph",
    "initial_state",
    "PartCEvaluationModule",
    "confirmed_profile",
    "postgres_decision_graph",
    "thread_config",
]
