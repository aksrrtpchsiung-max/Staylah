"""Print the state changes of the decision graph node by node, to help understand LangGraph's execution model.

LangGraph's core convention is: a node reads the full state and returns only **the keys that need to change**; the framework handles merging.
`stream_mode="updates"` produces events at exactly this granularity, so this script's output can be compared directly against
the return statement of each node in nodes.py.

Run from the project root:

    .venv/bin/python scripts/trace_decision.py --scenario relax --answer decline
    .venv/bin/python scripts/trace_decision.py --scenario repair

No model calls, no network access: both module C and module B use the stand-ins from property_agent/decision/stubs.py.
The listing fixtures reuse tests/support.py, so it must be run from the root directory.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from property_agent.decision import (
    DEFAULT_POLICY,
    build_decision_graph,
    build_stub_deps,
    initial_state,
)
from tests.support import build_ctx, build_outcome, load_profile, load_snapshot

SCENARIOS = {
    "publish": "3 qualifying candidates, delivered directly",
    "relax": "Only 2 qualifying candidates, propose a budget concession to the user",
    "research": "Not enough candidates but there is a next page, search more before delivering",
    "repair": "Review found a blocking issue, use up the single repair allowance",
    "source-failure": "All sources failed, terminate explicitly instead of reporting no listings",
}

ANSWERS = {
    "accept": "accept_proposal",
    "decline": "decline",
    "cancel": "cancel",
    "text": "answer",
}


def brief(value: object) -> str:
    """Compress large objects onto one line to avoid flooding the screen and hiding the state flow itself."""
    if isinstance(value, dict):
        if "snapshot_id" in value and "items" in value:
            return f"ListingSnapshot(<{len(value['items'])} listings>)"
        if "action" in value and "reason_code" in value:
            return f"RouteDecision(action={value['action']!r}, reason={value['reason_code']!r})"
        if "passed" in value and "issues" in value:
            blocking = sum(1 for i in value["issues"] if i.get("severity") == "blocking")
            return f"ReviewResult(passed={value['passed']}, {blocking} blocking)"
        if "recommendation" in value and "assessment" in value:
            items = value["recommendation"]["ordered_items"]
            proposals = value["assessment"]["relaxation_proposals"]
            return f"EvaluationResult(<{len(items)} recommendations>, <{len(proposals)} proposals>)"
        if "ordered_items" in value:
            return f"Recommendation(<{len(value['ordered_items'])} items>)"
        if "eligible" in value and "rejected" in value:
            return (
                f"ScreenResult(eligible {len(value['eligible'])}"
                f" / rejected {len(value['rejected'])}"
                f" / needs verification {len(value['needs_verification'])})"
            )
        if "question_id" in value and "proposals" in value:
            return f"PendingQuestion(id={value['question_id']!r}, {len(value['proposals'])} proposals)"
        if "question_id" in value and "action" in value:
            return f"PendingAnswer(action={value['action']!r}, for {value['question_id']!r})"
        if "candidates" in value:
            return f"RetrievalResult(<{len(value['candidates'])} candidates>)"
        if "queried_sources" in value:
            return f"Coverage(has_more={value['has_more']})"
    if isinstance(value, list):
        if not value:
            return "[]"
        if isinstance(value[0], dict) and "code" in value[0]:
            return "[" + ", ".join(str(i["code"]) for i in value) + "]"
        return f"<{len(value)} items>"
    text = repr(value)
    return text if len(text) <= 70 else text[:67] + "..."


def print_update(step: int, node: str, update: dict) -> None:
    print(f"\n[{step}] node {node} returned {len(update)} keys:")
    for key, value in update.items():
        print(f"      {key} = {brief(value)}")


def build_scenario(scenario: str, deps) -> dict:
    """Script the stand-in's behavior per scenario, then assemble the graph's initial state."""
    profile = load_profile()
    kwargs: dict = {"eligible": 3}

    if scenario == "relax":
        kwargs = {"eligible": 2, "include_over_budget": True}
    elif scenario == "research":
        kwargs = {"eligible": 2, "include_over_budget": True, "has_more": True}
        # Make this extra search return enough candidates, otherwise the stand-in will explicitly error instead of fabricating data.
        deps.search_runner.outcomes.append(
            {
                "status": "success",
                "data": build_outcome(eligible=3, attempt_id="attempt-002"),
                "issues": [],
            }
        )
    elif scenario == "repair":
        deps.module_c.reviews.append(
            {
                "status": "success",
                "data": {
                    "passed": False,
                    "issues": [
                        {
                            "code": "UNSUPPORTED_CLAIM",
                            "listing_key": load_snapshot()["items"][0]["listing_key"],
                            "field_path": "recommendation.ordered_items[0].reasons[0]",
                            "message": "A 5-minute walk has no source evidence.",
                            "severity": "blocking",
                            "suggested_fix": "Remove this fact or add supporting evidence.",
                        }
                    ],
                },
                "issues": [],
            }
        )
    elif scenario == "source-failure":
        kwargs = {"search_status": "error", "failure_code": "TIMEOUT"}

    return initial_state(
        ctx=build_ctx("run-trace"),
        profile=profile,
        outcome=build_outcome(**kwargs),
        policy=DEFAULT_POLICY,
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="publish")
    parser.add_argument(
        "--answer",
        choices=sorted(ANSWERS),
        default="decline",
        help="Which action to use to resume when stopped waiting for the user",
    )
    args = parser.parse_args()

    deps = build_stub_deps(load_profile())
    graph = build_decision_graph(deps).compile(checkpointer=InMemorySaver())
    state = build_scenario(args.scenario, deps)
    config = {"configurable": {"thread_id": state["run_id"]}}

    print(f"Scenario: {args.scenario} -- {SCENARIOS[args.scenario]}")
    print(f"thread_id = {config['configurable']['thread_id']} (thread_id=run_id, conversation_id is the long-lived session)")
    print(f"Initial state: {len(state)} keys, of which search_status={state['search_status']!r}")

    step = 0
    payload: object = state
    while True:
        interrupted = False
        async for event in graph.astream(payload, config, stream_mode="updates"):
            for node, update in event.items():
                if node == "__interrupt__":
                    interrupted = True
                    continue
                step += 1
                print_update(step, node, update)

        if not interrupted:
            break

        snapshot = await graph.aget_state(config)
        question = snapshot.values["pending_question"]
        print("\n--- Graph paused at wait_for_user, worker released ---")
        print(f"Question: {question['text']}")
        print(f"question_id = {question['question_id']}")
        print(f"state_version = {question['state_version']} (this value must be included when resuming)")
        print(f"\nResuming with action={ANSWERS[args.answer]!r}...")
        payload = Command(
            resume={
                "client_message_id": "msg-trace-1",
                "question_id": question["question_id"],
                "expected_state_version": question["state_version"],
                "action": ANSWERS[args.answer],
                "proposal_id": question["proposals"][0]["proposal_id"],
                "answer": "Let's try a different area.",
            }
        )

    final = (await graph.aget_state(config)).values
    print("\n=== Final state ===")
    for key in (
        "status",
        "completion_reason",
        "eligible_count",
        "search_attempts_used",
        "repairs_used",
        "final_result_id",
        "delivery_is_partial",
    ):
        print(f"  {key} = {final.get(key)!r}")
    print(f"  Current profile version = {deps.profiles.current_version(final['profile_id'])}")
    print(f"  Number of saved recommendations = {len(deps.recommendations.saved)}")


if __name__ == "__main__":
    asyncio.run(main())
