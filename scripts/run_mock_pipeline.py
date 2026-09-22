"""用 mock SearchResult 联调 decision、追问和 PostgreSQL。

从项目根目录运行：

    .venv-agent/bin/python scripts/run_mock_pipeline.py --all
    .venv-agent/bin/python scripts/run_mock_pipeline.py --scenario relax --answer decline

默认不调用 DeepSeek。加 --deepseek 且本地 .env 有密钥时才会润色/解析。
每条场景使用独立 run_id，可在 Navicat 按 run_id / conversation_id 查看写入。
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
from langgraph.types import Command
from sqlalchemy import func, select

from property_agent.decision import initial_state, postgres_decision_graph, thread_config
from property_agent.decision.stubs import ScriptedModuleC
from property_agent.mock_search import MockSearchRunner, first_attempt_from_fixture
from property_agent.persistence.database import build_engine, build_session_factory
from property_agent.persistence.models import (
    AgentRunRow,
    MessageRow,
    RecommendationRow,
    RunQuestionRow,
)
from property_agent.persistence.repositories import SqlRunRepository
from property_agent.persistence.wiring import build_postgres_deps
from tests.support import load_profile

SCENARIOS = {
    "publish": "search-success 有足够合格房源，直接发布推荐",
    "research": "page-1 合格不足但有下一页，补搜 page-2",
    "relax": "只取前 3 条搜索结果，合格不足，向用户提出预算放宽",
    "empty": "search-empty，没有候选也没有下一页",
    "timeout": "search-timeout，搜索失败不能伪装成无房源",
}


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def build_case(name: str, stamp: str) -> dict:
    run_id = f"mock-{name}-{stamp}"
    profile = copy.deepcopy(load_profile())
    profile["profile_id"] = f"profile-{run_id}"
    ctx = {
        "user_id": "mock-user-001",
        "run_id": run_id,
        "conversation_id": f"conversation-{run_id}",
        "attempt_id": "attempt-001",
        "trace_id": f"trace-{run_id}",
        "call_id": f"call-{run_id}",
        "deadline_at": "2026-09-19T12:02:00+08:00",
        "source_mode": "mock",
    }
    profile["user_id"] = ctx["user_id"]
    profile["conversation_id"] = ctx["conversation_id"]
    if name == "publish":
        outcome = first_attempt_from_fixture("search-success", profile)
    elif name == "research":
        outcome = first_attempt_from_fixture("page-1", profile)
    elif name == "relax":
        outcome = first_attempt_from_fixture(
            "search-success", profile, item_limit=3, has_more=False
        )
    elif name == "empty":
        outcome = first_attempt_from_fixture("search-empty", profile)
    elif name == "timeout":
        outcome = first_attempt_from_fixture("search-timeout", profile)
    else:
        raise ValueError(name)
    return {"ctx": ctx, "profile": profile, "outcome": outcome}


async def run_case(
    name: str,
    *,
    stamp: str,
    answer: str,
    use_deepseek: bool,
) -> dict:
    case = build_case(name, stamp)
    engine = build_engine()
    sessions = build_session_factory(engine)
    deps = build_postgres_deps(
        module_c=ScriptedModuleC(),
        search_runner=MockSearchRunner(),
        sessions=sessions,
        use_deepseek=use_deepseek,
    )
    SqlRunRepository(sessions).prepare_run(ctx=case["ctx"], profile=case["profile"])
    config = thread_config(case["ctx"]["run_id"])
    async with postgres_decision_graph(deps) as graph:
        result = await graph.ainvoke(
            initial_state(
                ctx=case["ctx"],
                profile=case["profile"],
                outcome=case["outcome"],
            ),
            config,
        )
        if "__interrupt__" in result:
            question = result["__interrupt__"][0].value["pending_question"]
            print(f"\n[{name}] 等待用户：{question['text']}")
            result = await graph.ainvoke(
                Command(
                    resume={
                        "client_message_id": f"answer-{case['ctx']['run_id']}",
                        "text": answer,
                    }
                ),
                config,
            )
    engine.dispose()
    return {"name": name, "ctx": case["ctx"], "result": result, "sessions": sessions}


def summarize(name: str, ctx: dict, result: dict, sessions) -> None:
    run_id = ctx["run_id"]
    with sessions() as session:
        questions = session.scalar(
            select(func.count())
            .select_from(RunQuestionRow)
            .where(RunQuestionRow.run_id == run_id)
        )
        messages = session.scalar(
            select(func.count())
            .select_from(MessageRow)
            .where(MessageRow.conversation_id == ctx["conversation_id"])
        )
        recs = session.scalar(
            select(func.count())
            .select_from(RecommendationRow)
            .where(RecommendationRow.run_id == run_id)
        )
        run = session.get(AgentRunRow, run_id)
    print(
        f"\n[{name}] status={result.get('status')} "
        f"reason={result.get('completion_reason')} "
        f"eligible={result.get('eligible_count')} "
        f"attempts={result.get('search_attempts_used')}"
    )
    if result.get("next_run_request"):
        print(f"[{name}] next_run_request={result['next_run_request']}")
    if result.get("final_result_id"):
        print(f"[{name}] recommendation={result['final_result_id']}")
    print(
        f"[{name}] Navicat 可查 run_id={run_id} conversation_id={ctx['conversation_id']}"
    )
    print(
        f"[{name}] 表行数 questions={questions} messages={messages} "
        f"recommendations={recs} agent_runs.status={getattr(run, 'status', None)}"
    )


async def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="用 mock 搜索结果联调房产追问模块")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), help="只跑一个场景")
    parser.add_argument("--all", action="store_true", help="按顺序跑全部场景")
    parser.add_argument(
        "--answer",
        default="不接受调整，保持原样",
        help="遇到 interrupt 时的自然语言回答",
    )
    parser.add_argument(
        "--deepseek",
        action="store_true",
        help="使用 DeepSeek 润色/解析；默认走确定性 fallback",
    )
    args = parser.parse_args()
    names = sorted(SCENARIOS) if args.all or not args.scenario else [args.scenario]
    stamp = _stamp()
    use_deepseek = bool(args.deepseek and os.getenv("DEEPSEEK_API_KEY"))
    print("场景：", ", ".join(f"{name}（{SCENARIOS[name]}）" for name in names))
    print("DeepSeek：", "开启" if use_deepseek else "关闭（确定性解析）")
    for name in names:
        packed = await run_case(
            name, stamp=stamp, answer=args.answer, use_deepseek=use_deepseek
        )
        summarize(name, packed["ctx"], packed["result"], packed["sessions"])


if __name__ == "__main__":
    asyncio.run(main())
