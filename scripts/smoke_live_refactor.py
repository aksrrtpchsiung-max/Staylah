"""Run a real A/B/C smoke conversation in a dedicated test database.

Requires TEST_DATABASE_URL. Model/source credentials are read using the normal
runtime configuration. Output contains statuses and counts, never credentials.
"""
import argparse
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

async def main(env_file):
    from runtime_settings import load_runtime_settings, secret_environ
    from property_agent.orchestration.postgres import postgres_conversation_runtime
    from property_agent.persistence.database import normalize_psycopg_uri
    from test_all import request_1

    database = os.environ.get("TEST_DATABASE_URL")
    if not database:
        raise SystemExit("Set TEST_DATABASE_URL to an isolated database.")
    for key, value in secret_environ(env_file).items():
        os.environ.setdefault(key, value)
    settings = load_runtime_settings(reload=True)
    settings = replace(settings, database=replace(settings.database,
        url=database, checkpoint_url=normalize_psycopg_uri(database)))
    conversation = "refactor-live-" + uuid4().hex
    async with postgres_conversation_runtime(settings=settings) as runtime:
        text = request_1["listing_constraints"][0]["source"]["text"]
        first = await runtime.handle_message(text, conversation_id=conversation,
            user_id="refactor-verification", client_message_id=conversation + ":1")
        print(json.dumps({"stage": "A", "phase": first.phase, "status": first.status,
            "issues": [item.get("code") for item in first.issues]}, ensure_ascii=False), flush=True)
        if first.status != "awaiting_confirmation":
            print(json.dumps({"complete": False, "reason": "A did not reach confirmation",
                "response": first.assistant_response}, ensure_ascii=False), flush=True)
            return 1
        final = await runtime.handle_message("确认", conversation_id=conversation,
            user_id="refactor-verification", client_message_id=conversation + ":2")
        print(json.dumps({"stage": "ABC", "phase": final.phase, "status": final.status,
            "run_id": final.run_id, "recommendations": len((final.recommendation or {}).get("ordered_items", [])),
            "issues": final.issues,
            "response": final.assistant_response}, ensure_ascii=False), flush=True)
        return 0 if final.phase == "published" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--source-root", type=Path, default=root)
    parser.add_argument("--env-file", type=Path, default=root / ".env")
    args = parser.parse_args()
    sys.path.insert(0, str(args.source_root.resolve()))
    raise SystemExit(asyncio.run(main(args.env_file)))
