"""逐节点打印决策图的状态变化，用于理解 LangGraph 的执行模型。

LangGraph 的核心约定是：节点读取完整状态，只返回**需要改动的那几个键**，框架负责合并。
`stream_mode="updates"` 正好按这个粒度产出事件，因此这个脚本的输出可以直接对照
nodes.py 里每个节点的 return 语句。

从项目根目录运行：

    .venv-agent/bin/python scripts/trace_decision.py --scenario relax --answer decline
    .venv-agent/bin/python scripts/trace_decision.py --scenario repair

不调用模型、不访问网络：模块 C 与模块 B 都用 property_agent/decision/stubs.py 的替身。
房源夹具复用 tests/support.py，所以需要在根目录执行。
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
    "publish": "3 条合格候选，直接交付",
    "relax": "只有 2 条合格候选，向用户提出预算让步提案",
    "research": "候选不足但还有下一页，先补搜再交付",
    "repair": "审查发现阻断问题，用掉唯一一次修复额度",
    "source-failure": "所有来源失败，明确终止而不是报告无房源",
}

ANSWERS = {
    "accept": "accept_proposal",
    "decline": "decline",
    "cancel": "cancel",
    "text": "answer",
}


def brief(value: object) -> str:
    """把大对象压成一行，避免刷屏盖住状态流动本身。"""
    if isinstance(value, dict):
        if "snapshot_id" in value and "items" in value:
            return f"ListingSnapshot(<{len(value['items'])} 条房源>)"
        if "action" in value and "reason_code" in value:
            return f"RouteDecision(action={value['action']!r}, reason={value['reason_code']!r})"
        if "passed" in value and "issues" in value:
            blocking = sum(1 for i in value["issues"] if i.get("severity") == "blocking")
            return f"ReviewResult(passed={value['passed']}, 阻断 {blocking} 条)"
        if "recommendation" in value and "assessment" in value:
            items = value["recommendation"]["ordered_items"]
            proposals = value["assessment"]["relaxation_proposals"]
            return f"EvaluationResult(<{len(items)} 条推荐>, <{len(proposals)} 个提案>)"
        if "ordered_items" in value:
            return f"Recommendation(<{len(value['ordered_items'])} 条>)"
        if "eligible" in value and "rejected" in value:
            return (
                f"ScreenResult(合格 {len(value['eligible'])}"
                f" / 不符 {len(value['rejected'])}"
                f" / 待核实 {len(value['needs_verification'])})"
            )
        if "question_id" in value and "proposals" in value:
            return f"PendingQuestion(id={value['question_id']!r}, {len(value['proposals'])} 个提案)"
        if "question_id" in value and "action" in value:
            return f"PendingAnswer(action={value['action']!r}, 针对 {value['question_id']!r})"
        if "candidates" in value:
            return f"RetrievalResult(<{len(value['candidates'])} 个候选>)"
        if "queried_sources" in value:
            return f"Coverage(has_more={value['has_more']})"
    if isinstance(value, list):
        if not value:
            return "[]"
        if isinstance(value[0], dict) and "code" in value[0]:
            return "[" + ", ".join(str(i["code"]) for i in value) + "]"
        return f"<{len(value)} 项>"
    text = repr(value)
    return text if len(text) <= 70 else text[:67] + "..."


def print_update(step: int, node: str, update: dict) -> None:
    print(f"\n[{step}] 节点 {node} 返回了 {len(update)} 个键：")
    for key, value in update.items():
        print(f"      {key} = {brief(value)}")


def build_scenario(scenario: str, deps) -> dict:
    """按场景脚本化替身的行为，再拼出图的初始状态。"""
    profile = load_profile()
    kwargs: dict = {"eligible": 3}

    if scenario == "relax":
        kwargs = {"eligible": 2, "include_over_budget": True}
    elif scenario == "research":
        kwargs = {"eligible": 2, "include_over_budget": True, "has_more": True}
        # 让补搜这一次拿到足够候选，否则替身会明确报错而不是造数据。
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
                            "message": "步行 5 分钟没有来源证据。",
                            "severity": "blocking",
                            "suggested_fix": "删除该事实或补充证据。",
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
        help="停在等待用户时用哪种动作恢复",
    )
    args = parser.parse_args()

    deps = build_stub_deps(load_profile())
    graph = build_decision_graph(deps).compile(checkpointer=InMemorySaver())
    state = build_scenario(args.scenario, deps)
    config = {"configurable": {"thread_id": state["run_id"]}}

    print(f"场景：{args.scenario} —— {SCENARIOS[args.scenario]}")
    print(f"thread_id = {config['configurable']['thread_id']}（thread_id=run_id，conversation_id 是长期会话）")
    print(f"初始状态：{len(state)} 个键，其中 search_status={state['search_status']!r}")

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
        print("\n--- 图在 wait_for_user 暂停，worker 已释放 ---")
        print(f"问题：{question['text']}")
        print(f"question_id = {question['question_id']}")
        print(f"state_version = {question['state_version']}（恢复时必须带上这个值）")
        print(f"\n用 action={ANSWERS[args.answer]!r} 恢复……")
        payload = Command(
            resume={
                "client_message_id": "msg-trace-1",
                "question_id": question["question_id"],
                "expected_state_version": question["state_version"],
                "action": ANSWERS[args.answer],
                "proposal_id": question["proposals"][0]["proposal_id"],
                "answer": "换个地区看看吧。",
            }
        )

    final = (await graph.aget_state(config)).values
    print("\n=== 终态 ===")
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
    print(f"  档案当前版本 = {deps.profiles.current_version(final['profile_id'])}")
    print(f"  已保存推荐数 = {len(deps.recommendations.saved)}")


if __name__ == "__main__":
    asyncio.run(main())
