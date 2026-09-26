"""Run the two opt-in live evaluation suites and save reviewable JSON logs.

No suite is executed on import. The clarification suite invokes only A's graph.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from dotenv import load_dotenv

from property_agent.evaluation_trace import capture_trace, record_event, stage_span, stage_totals
from property_agent.runtime.settings import load_runtime_settings


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA_FILES = {
    "clarification": HERE / "data" / "clarification_10.json",
    "full": HERE / "data" / "full_pipeline_20.json",
}


def load_cases(suite: str) -> list[dict]:
    cases = json.loads(DATA_FILES[suite].read_text(encoding="utf-8"))
    expected = 10 if suite == "clarification" else 20
    if len(cases) != expected or len({item["id"] for item in cases}) != expected:
        raise ValueError(f"{suite} dataset must have {expected} distinct cases")
    if suite == "clarification":
        bands = [item["ambiguity"] for item in cases]
        if (bands.count("slight"), bands.count("moderate"), bands.count("severe")) != (3, 4, 3):
            raise ValueError("clarification dataset must be 3/4/3")
    else:
        bands = [item["band"] for item in cases]
        if (bands.count("3-5"), bands.count("5-10"), bands.count(">10")) != (5, 10, 5):
            raise ValueError("full dataset must be 5/10/5")
        for item in cases:
            n = len(item["filters"])
            low, high = {"3-5": (3, 5), "5-10": (5, 10), ">10": (11, 100)}[item["band"]]
            if not low <= n <= high:
                raise ValueError(f"{item['id']} has {n} filters outside {item['band']}")
    return cases


def _a_observation(state: dict) -> dict:
    return {key: state.get(key) for key in (
        "status", "assistant_response", "clarification_questions", "profile",
        "normalized_requirement", "requirement_request", "requirement_issues",
    )}


def _summarize_full(trace: dict, turns: list[dict], wall_ms: float) -> dict:
    b_events = [event for event in trace["events"] if event["kind"] == "b_search_result"]
    handoff_events = [event for event in trace["events"] if event["kind"] == "b_candidate_handoff"]
    final_events = [event for event in trace["events"] if event["kind"] == "final_recommendation"]
    attempt_ids = {
        span["attempt_id"] for span in trace["spans"]
        if span["stage"] == "B" and span["attempt_id"]
    }
    b_keys = {item["listing_key"] for event in b_events for item in event["data"]["listings"]}
    handed_off_keys = {
        item["listing_key"] for event in handoff_events
        for item in event["data"]["candidate_listings"]
    }
    final = final_events[-1]["data"] if final_events else None
    return {
        "final_phase": turns[-1]["phase"] if turns else "error",
        "full_pipeline_completed": bool(final and final["ordered_listings"] and turns[-1]["phase"] == "published"),
        "attempt_count": len(attempt_ids),
        "b_found_unique_count": len(b_keys),
        "b_found_listing_keys": sorted(b_keys),
        "b_candidate_handoff_unique_count": len(handed_off_keys),
        "b_candidate_handoff_listing_keys": sorted(handed_off_keys),
        "eligible_found_unique_count": None,
        "eligible_found_listing_keys": [],
        "recommended_count": len(final["ordered_listings"]) if final else 0,
        "recommended_listing_keys": [item["listing_key"] for item in final["ordered_listings"]] if final else [],
        "a_b_c_duration_ms": stage_totals(trace),
        "wall_duration_ms": round(wall_ms, 2),
        "reference_qualifying_total": None,
        "qualifying_recall": None,
        "note": "Recall requires an independently established reference set; B's own results cannot be its denominator.",
    }


def _full_case_markdown(result: dict) -> str:
    case = result["case"]
    lines = [f"# {case['id']} · {case['band']} 条件", "", f"用户输入：{case['prompt']}", ""]
    if result.get("error_type"):
        return "\n".join([*lines, f"运行错误：{result['error_type']}", ""])
    for number, turn in enumerate(result.get("turns") or [], 1):
        lines.extend([
            f"## 第 {number} 轮 · {turn.get('phase')} / {turn.get('status')}", "",
            f"A/系统回复：{turn.get('assistant_response') or '[无回复]'}", "",
        ])
        if turn.get("clarification_questions"):
            lines.extend(["追问：", ""])
            lines.extend(f"- {item.get('text', item)}" for item in turn["clarification_questions"])
            lines.append("")
    summary = result.get("summary") or {}
    lines.extend([
        "## 结果数量", "",
        f"B 原始房源（去重）：{summary.get('b_found_unique_count', 0)} 套", "",
        f"B 交给 C 打分的候选（去重）：{summary.get('b_candidate_handoff_unique_count', 0)} 套", "",
        "硬条件合格数：未由 C 复核，需人工核对 B 字段及证据。", "",
        f"最终推荐：{summary.get('recommended_count', 0)} 套", "",
    ])
    events = (result.get("trace") or {}).get("events") or []
    b_events = [event for event in events if event["kind"] == "b_search_result"]
    handoff_by_attempt = {
        event["attempt_id"]: event["data"] for event in events
        if event["kind"] == "b_candidate_handoff"
    }
    for event in b_events:
        attempt_id = event["attempt_id"]
        candidates = handoff_by_attempt.get(attempt_id, {}).get("candidate_listings") or []
        lines.extend([
            f"## B/C 候选 · {attempt_id}", "",
            f"B 返回 {event['data']['count']} 套，交给 C retrieve {len(candidates)} 套；C 未执行 screen。", "",
        ])
        for listing in candidates:
            price = listing.get("price") or {}
            lines.append(
                f"- {listing.get('listing_key')}｜{listing.get('title')}｜"
                f"{price.get('amount')} {price.get('currency')} / {price.get('period')}｜"
                f"{listing.get('source_url')}"
            )
        lines.append("")
    final = [event["data"] for event in events if event["kind"] == "final_recommendation"]
    if final:
        lines.extend(["## 最终推荐房源", ""])
        for item in final[-1]["ordered_listings"]:
            listing = item.get("listing") or {}
            lines.append(
                f"- #{item.get('rank')} {item.get('listing_key')}｜"
                f"{listing.get('title')}｜{listing.get('source_url')}"
            )
        lines.append("")
    timings = summary.get("a_b_c_duration_ms") or {}
    lines.extend([
        "## 用时", "",
        f"A：{timings.get('A', 0)} ms；B：{timings.get('B', 0)} ms；"
        f"C：{timings.get('C', 0)} ms；总墙钟：{summary.get('wall_duration_ms', 0)} ms。", "",
        "完整房源字段、B 待核实项、C 评分与评估审查结果见同名 JSON。", "",
    ])
    return "\n".join(lines)


async def _run_clarification(case: dict, a_graph, run_tag: str) -> dict:
    conversation_id = f"eval-a-{run_tag}-{case['id'].lower()}"
    payload = {
        "message_id": f"{conversation_id}:001",
        "current_input": case["prompt"],
        "user_id": "evaluation-user",
        "conversation_id": conversation_id,
        "status": "new",
    }
    with capture_trace(case["id"]) as trace:
        started = perf_counter()
        with stage_span("A", "requirement_turn"):
            state = await a_graph.ainvoke(
                payload, {"configurable": {"thread_id": conversation_id}, "recursion_limit": 20}
            )
        observation = _a_observation(state)
        record_event("a_state", observation)
        elapsed = (perf_counter() - started) * 1000
    questions = observation.get("clarification_questions") or []
    return {
        "case": case,
        "conversation_id": conversation_id,
        "a_only": True,
        "observation": observation,
        "automatic_checks": {
            "asked_clarification": observation["status"] == "awaiting_clarification" and bool(questions),
            "did_not_handoff_to_b": observation["status"] != "ready_for_b",
            "question_fields": [item.get("field") for item in questions],
            "human_judgment_required": True,
        },
        "duration_ms": round(elapsed, 2),
        "trace": trace,
    }


async def _run_full(case: dict, orchestrator, run_tag: str) -> dict:
    conversation_id = f"eval-full-{run_tag}-{case['id'].lower()}"
    with capture_trace(case["id"]) as trace:
        started = perf_counter()
        first = await orchestrator.handle_message(
            case["prompt"], conversation_id=conversation_id,
            user_id="evaluation-user", client_message_id=f"{conversation_id}:001",
        )
        turns = [asdict(first)]
        # Automatic confirmation does not validate A's interpretation. Its profile is saved
        # separately so the human reviewer can flag an incorrect interpretation.
        if first.status == "awaiting_confirmation":
            confirmed = await orchestrator.handle_message(
                "确认", conversation_id=conversation_id,
                user_id="evaluation-user", client_message_id=f"{conversation_id}:002",
            )
            turns.append(asdict(confirmed))
        elapsed = (perf_counter() - started) * 1000
    return {
        "case": case,
        "conversation_id": conversation_id,
        "turns": turns,
        "summary": _summarize_full(trace, turns, elapsed),
        "trace": trace,
    }


async def run(
    suite: str,
    case_id: str | None,
    output: Path,
    *,
    start_at: str | None = None,
    end_at: str | None = None,
    resume_dir: Path | None = None,
) -> Path:
    load_dotenv(ROOT / ".env", override=False)
    settings = load_runtime_settings(reload=True)
    if suite == "clarification" and not settings.deepseek.api_key():
        raise ValueError("A evaluation requires DEEPSEEK_API_KEY")
    if suite == "full" and settings.run.source_mode != "live":
        raise ValueError("Evaluation requires source_mode=live; mock listings are not allowed")
    if case_id and (start_at or end_at):
        raise ValueError("--case cannot be used with --start-at or --end-at")
    if resume_dir and suite != "full":
        raise ValueError("--resume-dir is only supported for the full suite")
    cases = load_cases(suite)
    if case_id:
        cases = [case for case in cases if case["id"] == case_id]
        if not cases:
            raise ValueError(f"Unknown case ID: {case_id}")
    if start_at or end_at:
        ids = [case["id"] for case in cases]
        if start_at and start_at not in ids:
            raise ValueError(f"Unknown starting case ID: {start_at}")
        if end_at and end_at not in ids:
            raise ValueError(f"Unknown ending case ID: {end_at}")
        start_index = ids.index(start_at) if start_at else 0
        end_index = ids.index(end_at) + 1 if end_at else len(ids)
        if start_index >= end_index:
            raise ValueError("--end-at must be the same as or later than --start-at")
        cases = cases[start_index:end_index]
    run_tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    run_dir = resume_dir.resolve() if resume_dir else output / f"{'EvalF-full' if suite == 'full' else suite}-{run_tag}"
    if resume_dir:
        if not run_dir.is_dir() or not (run_dir / "progress.md").is_file():
            raise ValueError("--resume-dir must be an existing full-suite result directory")
        already_saved = [case["id"] for case in cases if (run_dir / f"{case['id']}.json").exists()]
        if already_saved:
            raise ValueError(f"Refusing to overwrite completed cases: {', '.join(already_saved)}")
    else:
        run_dir.mkdir(parents=True, exist_ok=False)
    print(f"Logging to {run_dir}", flush=True)
    if suite == "full" and not resume_dir:
        (run_dir / "progress.md").write_text(
            "# F 完整流程评测进度\n\n每完成一个样例，立即写入同名 JSON/Markdown 并更新 summary.csv。\n\n",
            encoding="utf-8",
        )
    if resume_dir:
        with (run_dir / "progress.md").open("a", encoding="utf-8") as stream:
            stream.write(f"\n从 {cases[0]['id']} 恢复评测；本次使用新的会话 ID，避免复用中断的状态。\n")
    rows = []
    if resume_dir and (run_dir / "summary.csv").is_file():
        with (run_dir / "summary.csv").open("r", encoding="utf-8", newline="") as stream:
            rows.extend(csv.DictReader(stream))

    def write_summary() -> None:
        with (run_dir / "summary.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(dict.fromkeys(k for row in rows for k in row)))
            writer.writeheader()
            writer.writerows({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                              for key, value in row.items()} for row in rows)

    def save_case(case: dict, result: dict) -> None:
        (run_dir / f"{case['id']}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        summary = result.get("summary") or result.get("automatic_checks") or {}
        rows.append({"case_id": case["id"], "band": case.get("band") or case.get("ambiguity"),
                     "error_type": result.get("error_type", ""), **summary})
        write_summary()
        if suite == "clarification":
            observation = result.get("observation") or {}
            questions = observation.get("clarification_questions") or []
            transcript = (
                f"## {case['id']} · {case['ambiguity']}\n\n"
                f"用户：{case['prompt']}\n\n"
                f"A 状态：{observation.get('status') or result.get('error_type', 'unknown')}\n\n"
                f"A 回复：{observation.get('assistant_response') or '[无回复]'}\n\n"
                f"追问：{json.dumps(questions, ensure_ascii=False)}\n\n"
            )
            with (run_dir / "dialogue.md").open("a", encoding="utf-8") as stream:
                stream.write(transcript)
        else:
            (run_dir / f"{case['id']}.md").write_text(_full_case_markdown(result), encoding="utf-8")
            status = summary.get("final_phase") or result.get("error_type", "unknown")
            with (run_dir / "progress.md").open("a", encoding="utf-8") as stream:
                stream.write(f"- {case['id']}：已完成，状态 `{status}`；详细结果见 `{case['id']}.md` / `{case['id']}.json`。\n")

    if suite == "clarification":
        # Same A graph as the product; no PostgreSQL or B/C construction.
        from langgraph.checkpoint.memory import InMemorySaver
        from property_agent.requirements.graph import build_requirement_graph

        a_graph = build_requirement_graph(checkpointer=InMemorySaver())
        for case in cases:
            try:
                result = await _run_clarification(case, a_graph, run_tag)
            except Exception as exc:
                result = {"case": case, "error_type": type(exc).__name__}
            save_case(case, result)
            status = (result.get("observation") or {}).get("status") or result.get("error_type", "unknown")
            print(f"{case['id']}: {status}", flush=True)
    else:
        from property_agent.orchestration.postgres import postgres_conversation_runtime

        async with postgres_conversation_runtime(settings=settings) as orchestrator:
            for case in cases:
                with (run_dir / "progress.md").open("a", encoding="utf-8") as stream:
                    stream.write(f"- {case['id']}：开始运行。\n")
                print(f"{case['id']}: running", flush=True)
                try:
                    result = await _run_full(case, orchestrator, run_tag)
                except Exception as exc:
                    # Driver and HTTP exception messages may contain connection details.
                    result = {"case": case, "error_type": type(exc).__name__}
                save_case(case, result)
                status = (result.get("summary") or {}).get("final_phase") or result.get("error_type", "unknown")
                print(f"{case['id']}: {status}", flush=True)
    return run_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Falcon live evaluation suites")
    parser.add_argument("--suite", choices=DATA_FILES, required=True)
    parser.add_argument("--case", help="Run a single case ID, e.g. F01")
    parser.add_argument("--start-at", help="Run this case and every later case, e.g. F03")
    parser.add_argument("--end-at", help="Stop after this case, e.g. F04")
    parser.add_argument("--resume-dir", type=Path, help="Append to an existing full-suite result directory")
    parser.add_argument("--output", type=Path, default=ROOT / "evaluation_runs")
    args = parser.parse_args()
    path = asyncio.run(run(args.suite, args.case, args.output, start_at=args.start_at,
                           end_at=args.end_at, resume_dir=args.resume_dir))
    print(f"Evaluation logs: {path}")


if __name__ == "__main__":
    main()
