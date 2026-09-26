"""Exercise real C models against recorded live listings, without writing a database.

Run from the repository with its existing DeepSeek credentials. This validates
model integration, not current listing availability or a new PropertyGuru search.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from property_agent.evaluation import service as evaluation
from property_agent.search.api import prepare_query
from property_agent.decision.policy import DEFAULT_POLICY


def report(stage, result):
    print(json.dumps({"stage": stage, "status": result["status"],
        "issues": result["issues"]}, ensure_ascii=False), flush=True)
    if result["status"] != "success" or result["data"] is None:
        raise RuntimeError(f"{stage} did not succeed without fallback")


async def main():
    load_dotenv(ROOT / ".env")
    case = json.loads((ROOT / "tests/fixtures/refactor/live_search.json").read_text())
    request, ctx = case["request"], case["ctx"]
    ctx["deadline_at"] = (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat()
    profile = {key: request[key] for key in (
        "profile_id", "conversation_id", "intent", "user_context", "listing_constraints",
        "derived_data_requirements", "open_data_requirements")}
    profile.update(user_id=ctx["user_id"], version=request["profile_version"],
        confirmed_version=request["profile_version"], status="confirmed",
        unresolved=request["unresolved_fields"], field_sources={},
        created_at=request["confirmed_at"], updated_at=request["confirmed_at"],
        last_user_message_at=request["confirmed_at"], confirmed_at=request["confirmed_at"])
    query = await prepare_query(profile, ctx=ctx)
    report("prepare-query", query)
    listings = case["search_result"]["data"]["items"]
    screen = evaluation.screen(listings, profile)
    evaluation.configure_deepseek_keyword_matcher()
    evaluation.configure_deepseek_evaluation_review_model()
    try:
        retrieved = await evaluation.retrieve(query["data"], listings, top_k=3, ctx=ctx)
        report("live-retrieve", retrieved)
        snapshot = {"snapshot_id": "refactor-live-recording",
            "profile_version": profile["version"], "items": listings}
        evaluated = await evaluation.evaluate(profile, retrieved["data"], screen, snapshot,
            case["search_result"]["data"]["coverage"], None, policy=DEFAULT_POLICY, ctx=ctx)
        report("live-evaluate", evaluated)
        reviewed = await evaluation.review(profile, evaluated["data"], snapshot,
            policy=DEFAULT_POLICY, ctx=ctx)
        report("live-review", reviewed)
        if not reviewed["data"]["passed"]:
            raise RuntimeError("live-review did not pass")
    finally:
        evaluation.configure_keyword_matcher(None)
        evaluation.configure_evaluation_review_model(None)


if __name__ == "__main__":
    asyncio.run(main())
