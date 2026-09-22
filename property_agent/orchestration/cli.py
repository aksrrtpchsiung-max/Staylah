"""交互式 A→B→C 主循环。需要 PostgreSQL，并按 runtime.toml / .env 配置模型。"""
from __future__ import annotations

import argparse
import asyncio
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv

from property_agent.orchestration.postgres import postgres_conversation_runtime
from requirement_understanding.workflow_constants import DEFAULT_USER_ID
from runtime_settings import load_runtime_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Falcon A→B→C conversation runtime")
    parser.add_argument("--conversation", default="falcon-local", help="conversation / A thread id")
    parser.add_argument("--user", default=DEFAULT_USER_ID, help="user id")
    return parser.parse_args()


def print_turn(result: Any) -> None:
    print(f"\nFalcon> {result.assistant_response}")
    extras = [f"phase={result.phase}"]
    if result.status:
        extras.append(f"status={result.status}")
    if result.run_id:
        extras.append(f"run_id={result.run_id}")
    print("[" + " ".join(extras) + "]")


async def main() -> int:
    load_dotenv()
    args = parse_args()
    settings = load_runtime_settings()
    print(
        "Falcon A→B→C runtime | "
        f"conversation={args.conversation} | source_mode={settings.run.source_mode}"
    )
    print("Enter /quit to exit. Model settings: runtime.toml ; secrets: .env")
    async with postgres_conversation_runtime(settings=settings) as orchestrator:
        cli_session_id = uuid4().hex
        index = 0
        while True:
            try:
                text = input("\nYou> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nExited.")
                return 0
            if not text:
                continue
            if text in {"/quit", "/exit"}:
                print("Exited.")
                return 0
            index += 1
            result = await orchestrator.handle_message(
                text,
                conversation_id=args.conversation,
                user_id=args.user,
                client_message_id=(
                    f"{args.conversation}:cli:{cli_session_id}:{index:04d}"
                ),
            )
            print_turn(result)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
