"""Interactively debug the Falcon multi-turn requirements workflow in a local terminal."""

from __future__ import annotations

from property_agent.runtime.paths import PROJECT_ROOT

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from .graph import build_requirement_graph
from .response_renderer import ResponseRenderer, ToneName
from .workflow import InMemoryProfileRepository
from .workflow_constants import DEFAULT_USER_ID


@dataclass
class DebugRuntime:
    """Store the graph, checkpoint, repository, and display settings shared by a single CLI session."""

    graph: Any
    repository: InMemoryProfileRepository
    renderer: ResponseRenderer
    config: dict[str, Any]
    has_state: bool = False
    trace: bool = False
    message_index: int = 0


def load_local_env(path: Path | None = None) -> Path:
    """Load local variables from the Git-ignored .env.local without overriding existing terminal values."""

    env_path = path or PROJECT_ROOT / ".env.local"
    if not env_path.exists():
        return env_path
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return env_path


def build_debug_runtime(thread_id: str, tone: ToneName) -> DebugRuntime:
    """Create a local debug runtime that stores state only within the current CLI process."""

    repository = InMemoryProfileRepository()
    renderer = ResponseRenderer(tone=tone)
    graph = build_requirement_graph(
        profile_repository=repository,
        response_renderer=renderer,
        checkpointer=InMemorySaver(),
    )
    return DebugRuntime(
        graph=graph,
        repository=repository,
        renderer=renderer,
        config={"configurable": {"thread_id": thread_id}},
    )


def invoke_turn(
    runtime: DebugRuntime,
    *,
    text: str,
    conversation_id: str,
) -> dict[str, Any]:
    """Execute one turn in the same thread, and print the nodes traversed as needed."""

    runtime.message_index += 1
    payload: dict[str, Any] = {
        "message_id": f"local-{runtime.message_index:04d}",
        "current_input": text,
        "user_id": DEFAULT_USER_ID,
        "conversation_id": conversation_id,
    }
    if not runtime.has_state:
        payload["status"] = "new"
    if runtime.trace:
        visited: list[str] = []
        for update in runtime.graph.stream(payload, runtime.config, stream_mode="updates"):
            visited.extend(update.keys())
        print(f"[nodes] {' -> '.join(visited)}")
        state = dict(runtime.graph.get_state(runtime.config).values)
    else:
        state = dict(runtime.graph.invoke(payload, runtime.config))
    runtime.has_state = True
    return state


def print_turn(state: dict[str, Any]) -> None:
    """Print the user-visible reply and minimal debug state, without outputting secrets or Authorization."""

    print(f"\nFalcon> {state.get('assistant_response', '[no assistant_response]')}")
    profile = state.get("profile") or {}
    print(
        f"[status={state.get('status', 'unknown')} "
        f"profile_version={profile.get('version', '-')} "
        f"confirmed_version={profile.get('confirmed_version', '-')}]"
    )


def print_json(value: Any) -> None:
    """Display the inspectable state sub-object as UTF-8 JSON."""

    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def current_state(runtime: DebugRuntime) -> dict[str, Any]:
    """Read the current checkpoint; return an empty object when no input has been executed yet."""

    if not runtime.has_state:
        return {}
    return dict(runtime.graph.get_state(runtime.config).values)


def handle_command(runtime: DebugRuntime, command: str) -> bool:
    """Execute a CLI debug command; returning False indicates exiting the loop."""

    parts = command.strip().split()
    name = parts[0].lower()
    state = current_state(runtime)
    if name in {"/quit", "/exit"}:
        return False
    if name == "/help":
        print("/state /profile /patch /answer /request /trace on|off /tone warm|direct|concise /reset /quit")
    elif name == "/state":
        print_json(state)
    elif name == "/profile":
        print_json(state.get("profile"))
    elif name == "/patch":
        print_json({
            "proposed_patch": state.get("proposed_patch"),
            "validated_patch": state.get("validated_patch"),
        })
    elif name == "/request":
        print_json(state.get("requirement_request"))
    elif name == "/answer":
        print_json(state.get("housing_question_answer"))
    elif name == "/trace" and len(parts) == 2 and parts[1] in {"on", "off"}:
        runtime.trace = parts[1] == "on"
        print(f"trace={parts[1]}")
    elif name == "/tone" and len(parts) == 2 and parts[1] in {"warm", "direct", "concise"}:
        runtime.renderer.tone = parts[1]
        print(f"tone={parts[1]}")
    elif name == "/reset":
        return True
    else:
        print("Unknown command. Enter /help to see the available commands.")
    return True


def parse_args() -> argparse.Namespace:
    """Parse local debug CLI arguments."""

    parser = argparse.ArgumentParser(description="Falcon A-side multi-turn workflow debugger")
    parser.add_argument("--thread", default="falcon-local-debug", help="LangGraph thread/conversation ID")
    parser.add_argument("--tone", choices=["warm", "direct", "concise"], default="warm")
    parser.add_argument("--trace", action="store_true", help="print visited nodes for every turn")
    return parser.parse_args()


def main() -> int:
    """Start the interactive loop, preserving multi-turn checkpoints in the same thread."""

    args = parse_args()
    env_path = load_local_env()
    if not os.getenv("DEEPSEEK_API_KEY"):
        print(f"DEEPSEEK_API_KEY is missing. Add it to {env_path} or set it in your shell.")
        return 2
    runtime = build_debug_runtime(args.thread, args.tone)
    runtime.trace = args.trace
    print(f"Falcon local debugger | thread={args.thread} | tone={args.tone}")
    print("Enter /help for debugging commands or /quit to exit.")
    while True:
        try:
            text = input("\nYou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExited.")
            return 0
        if not text:
            continue
        if text.startswith("/"):
            if text.strip().lower() == "/reset":
                runtime = build_debug_runtime(args.thread, runtime.renderer.tone)
                print("The in-process checkpoint and profile repository have been reset.")
                continue
            if not handle_command(runtime, text):
                print("Exited.")
                return 0
            continue
        state = invoke_turn(
            runtime,
            text=text,
            conversation_id=args.thread,
        )
        print_turn(state)


if __name__ == "__main__":
    raise SystemExit(main())
