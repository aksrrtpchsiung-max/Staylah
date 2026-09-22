"""LangGraph 搜索状态：只存可序列化业务数据，不存模型、Provider 或锁。"""
from typing import TypedDict

from contracts_v0 import Issue, Listing, Result, RunContext, SearchPlan, SearchResult
from execution.tasks import ExecutionTask, LocationResult


class QueryProgress(TypedDict):
    cursor: str | None
    done: bool
    blocked: bool
    seen_cursors: list[str | None]


class SearchState(TypedDict):
    plan: SearchPlan
    ctx: RunContext
    queries: dict[str, QueryProgress]
    listings: dict[str, Listing]
    locations: dict[str, LocationResult]
    completed_tasks: list[str]
    attempts: dict[str, int]
    history: list[dict]
    pages: list[dict]
    task_issues: dict[str, list[Issue]]
    issues: list[Issue]
    selected_task: ExecutionTask | None
    decision_reason: str
    pages_used: int
    candidates_used: int
    stop_reason: str | None
    result: Result[SearchResult] | None
    requirement_request: dict | None
    investigations: dict[str, dict]
    screened_out: dict[str, list[dict]]


def initial_state(plan, ctx, requirement_request=None) -> SearchState:
    from copy import deepcopy
    return dict(plan=deepcopy(plan), ctx=deepcopy(ctx),
                queries={q['query_id']: dict(cursor=q['cursor'], done=False, blocked=False, seen_cursors=[]) for q in plan['queries']},
                listings={}, locations={}, completed_tasks=[], attempts={}, history=[], pages=[], task_issues={}, issues=[],
                selected_task=None, decision_reason='', pages_used=0, candidates_used=0, stop_reason=None, result=None,
                requirement_request=deepcopy(requirement_request), investigations={}, screened_out={})
