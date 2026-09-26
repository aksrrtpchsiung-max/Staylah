"""Stable behavior snapshots captured before moving production code.

External responses are supplied at their existing boundaries. Durations are the
only normalized fields; identifiers, evidence, ordering and errors stay intact.
"""
import copy

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

import part_c
from property_agent.decision import build_decision_graph, build_stub_deps, initial_state
from property_agent.decision.policy import DEFAULT_POLICY
from tests.support import build_ctx, build_outcome, load_profile


def normalize(value):
    if isinstance(value, dict):
        return {key: (0 if key == "duration_ms" else normalize(item))
                for key, item in value.items() if key != "__interrupt__"}
    if isinstance(value, (list, tuple)):
        return [normalize(item) for item in value]
    return value


class UnavailableModel:
    async def match(self, *args, **kwargs):
        raise RuntimeError("Recorded model outage")

    async def evaluate(self, *args, **kwargs):
        raise RuntimeError("Recorded model outage")

    async def review(self, *args, **kwargs):
        raise RuntimeError("Recorded model outage")


async def capture():
    profile = load_profile()
    snapshots = {}
    for name, kwargs in {
        "publish": {"eligible": 3},
        "question": {"eligible": 2, "include_over_budget": True},
        "empty": {"eligible": 0},
        "failure": {"search_status": "error"},
    }.items():
        deps = build_stub_deps(profile)
        graph = build_decision_graph(deps).compile(checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": f"baseline-{name}"}}
        result = await graph.ainvoke(initial_state(
            ctx=build_ctx(f"baseline-{name}"), profile=profile,
            outcome=build_outcome(**kwargs)), config)
        snapshots[name] = normalize(result)
        if name == "question":
            snapshots["resume_decline"] = normalize(await graph.ainvoke(
                Command(resume={"client_message_id": "baseline-reply", "text": "不用了"}), config))

    part_c.configure_keyword_matcher(UnavailableModel())
    part_c.configure_evaluation_review_model(UnavailableModel())
    try:
        outcome = build_outcome(eligible=3)
        ctx = build_ctx("baseline-c")
        evaluation = await part_c.evaluate(profile, outcome["retrieval_result"],
            outcome["screen_result"], outcome["listing_snapshot"], outcome["coverage"],
            None, policy=DEFAULT_POLICY, ctx=ctx)
        snapshots["c_evaluation_fallback"] = normalize(evaluation)
        snapshots["c_review_fallback"] = normalize(await part_c.review(profile,
            evaluation["data"], outcome["listing_snapshot"], policy=DEFAULT_POLICY, ctx=ctx))
    finally:
        part_c.configure_keyword_matcher(None)
        part_c.configure_evaluation_review_model(None)
    return snapshots
