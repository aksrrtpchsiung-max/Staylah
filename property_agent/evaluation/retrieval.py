"""Retrieval responsibilities extracted without changing behavior."""
from __future__ import annotations
import json
from time import perf_counter
from typing import Any
from property_agent.contracts import ContractViolation, Listing, Result, RetrievalCandidate, RetrievalResult, RunContext, QueryFeatures
from property_agent.evaluation.configuration import _resolve_keyword_matcher
from property_agent.evaluation.results import _error, _partial, _success
from property_agent.evaluation.types import DETERMINISTIC_METHOD_VERSION, KeywordMatcherError, LLM_METHOD_VERSION, MAX_EVALUATION_CANDIDATES
from property_agent.evaluation.validation import _constraint_matches, _listing_field_value, _validate_listing


def _retrieval_result(
    query: QueryFeatures,
    rows: list[tuple[Listing, list[str], float]],
    top_k: int,
    method_version: str,
) -> RetrievalResult:
    def sort_key(row: tuple[Listing, list[str], float]) -> tuple[float, int, int, str]:
        listing, _matches, score = row
        price = listing["price"]
        amount = price.get("amount")
        price_known = (
            price.get("status") == "known"
            and isinstance(amount, int)
            and not isinstance(amount, bool)
        )
        return (
            -score,
            0 if price_known else 1,
            amount if price_known else 0,
            listing["listing_key"],
        )

    rows.sort(key=sort_key)
    selected = rows[:top_k]
    candidates: list[RetrievalCandidate] = []
    for rank, (listing, exact_matches, score) in enumerate(selected, start=1):
        candidates.append(
            {
                "listing_key": listing["listing_key"],
                "exact_matches": exact_matches,
                "vector_score": None,
                "keyword_score": score,
                "retrieval_rank": rank,
                "retrieval_score": score,
            }
        )
    return {
        "profile_version": query["profile_version"],
        "candidates": candidates,
        "input_count": len(rows),
        "returned_count": len(candidates),
        "truncated": len(rows) > len(candidates),
        "method_version": method_version,
    }


def _requirement_weight(requirement: dict[str, Any]) -> float:
    """Return the same hard/soft and priority weight used by the LLM rubric."""
    strength_weight = {"hard": 3.0, "soft": 1.0}.get(requirement.get("strength"))
    priority_weight = {"high": 3.0, "medium": 2.0, "low": 1.0}.get(
        requirement.get("priority")
    )
    if strength_weight is None or priority_weight is None:
        return 0.0
    return strength_weight * priority_weight


def _deterministic_requirement_rows(
    query: QueryFeatures, listings: list[Listing]
) -> list[tuple[Listing, list[str], float]]:
    """Score structured requirements locally without filtering any candidate.

    Listing constraints can be checked against normalized Listing fields. Derived/open-data
    requirements remain in the denominator but earn no points because C has no independent
    evidence for them. Unknown or conflicting listing values likewise earn zero points.
    """
    try:
        parsed = json.loads(query.get("semantic_query", ""))
    except (TypeError, json.JSONDecodeError):
        parsed = {}
    requirements = parsed if isinstance(parsed, dict) else {}

    constraints: list[dict[str, Any]] = []
    for item in requirements.get("listing_constraints", []):
        if (
            isinstance(item, dict)
            and isinstance(item.get("field_path"), str)
            and item.get("operator")
            in {"eq", "neq", "lt", "lte", "gt", "gte", "between", "in", "contains"}
            and _requirement_weight(item) > 0
        ):
            constraints.append(item)

    unverifiable_weight = 0.0
    for group in ("derived_data_requirements", "open_data_requirements"):
        values = requirements.get(group, [])
        if not isinstance(values, list):
            continue
        unverifiable_weight += sum(
            _requirement_weight(item) for item in values if isinstance(item, dict)
        )

    intent = requirements.get("intent")
    expected_transaction = {"rent": "rent", "buy": "sale"}.get(intent)
    intent_weight = 9.0 if expected_transaction is not None else 0.0
    total_weight = (
        intent_weight
        + unverifiable_weight
        + sum(_requirement_weight(item) for item in constraints)
    )

    rows: list[tuple[Listing, list[str], float]] = []
    for listing in listings:
        earned_weight = 0.0
        matched_ids: list[str] = []
        if (
            expected_transaction is not None
            and listing.get("transaction_type") == expected_transaction
        ):
            earned_weight += intent_weight
            matched_ids.append("intent")
        for constraint in constraints:
            actual = _listing_field_value(listing, constraint["field_path"])
            if _constraint_matches(actual, constraint) is True:
                earned_weight += _requirement_weight(constraint)
                constraint_id = constraint.get("constraint_id")
                if isinstance(constraint_id, str) and constraint_id:
                    matched_ids.append(constraint_id)
        score = round(100.0 * earned_weight / total_weight, 2) if total_weight else 0.0
        rows.append((listing, matched_ids, score))
    return rows


def _deterministic_retrieval_fallback(
    query: QueryFeatures,
    listings: list[Listing],
    top_k: int,
    ctx: RunContext,
    started: float,
    reason: str,
    *,
    retryable: bool,
) -> Result[RetrievalResult]:
    result = _retrieval_result(
        query,
        _deterministic_requirement_rows(query, listings),
        top_k,
        DETERMINISTIC_METHOD_VERSION,
    )
    return _partial(
        result,
        ctx,
        started,
        f"{reason}; used deterministic structured-requirement scoring fallback",
        retryable=retryable,
        code="MODEL_UNAVAILABLE",
        source="deepseek",
    )


async def retrieve(
    query: QueryFeatures, eligible_listings: list[Listing], *, top_k: int, ctx: RunContext
) -> Result[RetrievalResult]:
    """让 DeepSeek 对 B 返回的最多 12 套候选逐套打分，再由 Python 排序。

    C 不在这里判断房源合格与否，也不因字段未知而删除候选。分数降序排列；同分时
    月租已知且更低的房源优先，价格未知的排在已知价格之后。模型不可用或没有返回
    完整评分时，改用可披露的本地结构化约束评分，并返回 partial 而不是中断流程。
    """
    started = perf_counter()
    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k <= 0:
        return _error(ctx, started, "INVALID_INPUT", "top_k must be a positive integer", "top_k")
    top_k = min(top_k, MAX_EVALUATION_CANDIDATES)

    try:
        unique_listings: list[Listing] = []
        seen: set[str] = set()
        for index, listing in enumerate(eligible_listings):
            _validate_listing(listing, f"eligible_listings[{index}]")
            if listing["listing_key"] in seen:
                continue
            seen.add(listing["listing_key"])
            unique_listings.append(listing)

        if len(unique_listings) > MAX_EVALUATION_CANDIDATES:
            return _error(
                ctx,
                started,
                "INVALID_INPUT",
                f"B may hand off at most {MAX_EVALUATION_CANDIDATES} unique candidates",
                "eligible_listings",
            )
        if not unique_listings:
            return _success(
                _retrieval_result(query, [], top_k, LLM_METHOD_VERSION),
                ctx,
                started,
            )

        matcher, unavailable_reason = _resolve_keyword_matcher()
        if matcher is None:
            return _deterministic_retrieval_fallback(
                query,
                unique_listings,
                top_k,
                ctx,
                started,
                f"REQUEST_FAILED: {unavailable_reason or 'DeepSeek keyword matcher is unavailable'}",
                retryable=False,
            )
        try:
            llm_matches = await matcher.match(query, unique_listings)
            llm_rows = [
                (listing, [], llm_matches[listing["listing_key"]].score)
                for listing in unique_listings
            ]
        except KeywordMatcherError as exc:
            return _deterministic_retrieval_fallback(
                query,
                unique_listings,
                top_k,
                ctx,
                started,
                f"{exc.failure_kind}: {exc}",
                retryable=exc.failure_kind == "REQUEST_FAILED",
            )
        except (KeyError, TypeError):
            return _deterministic_retrieval_fallback(
                query,
                unique_listings,
                top_k,
                ctx,
                started,
                "INVALID_SCORE_SCHEMA: DeepSeek score mapping was incomplete",
                retryable=False,
            )
        return _success(
            _retrieval_result(query, llm_rows, top_k, LLM_METHOD_VERSION),
            ctx,
            started,
        )
    except ContractViolation as exc:
        return _error(ctx, started, exc.code, str(exc), exc.field_path)
    except (KeyError, TypeError) as exc:
        return _error(ctx, started, "INVALID_INPUT", str(exc), "query")
