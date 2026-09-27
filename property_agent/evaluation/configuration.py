"""Configuration responsibilities extracted without changing behavior."""
from __future__ import annotations
from typing import Any
from property_agent.evaluation.keyword_model import DeepSeekKeywordMatcher
from property_agent.evaluation.review_model import DeepSeekEvaluationReviewModel
from property_agent.evaluation.types import EvaluationReviewModel, EvaluationReviewModelError, KeywordMatcher, KeywordMatcherError


_keyword_matcher: KeywordMatcher | None = None


_auto_deepseek_matcher: KeywordMatcher | None = None


_evaluation_review_model: EvaluationReviewModel | None = None


_auto_evaluation_review_model: EvaluationReviewModel | None = None


def configure_keyword_matcher(matcher: KeywordMatcher | None) -> None:
    """Injected by the application startup code to provide the matcher; pass None to disable an already injected matcher."""
    global _keyword_matcher
    _keyword_matcher = matcher


def configure_deepseek_keyword_matcher(
    *, base_url: str | None = None, model_id: str | None = None, client: Any | None = None
) -> DeepSeekKeywordMatcher:
    """Create and inject the DeepSeek matcher; the key is read from an environment variable."""
    matcher = DeepSeekKeywordMatcher(base_url=base_url, model_id=model_id, client=client)
    configure_keyword_matcher(matcher)
    return matcher


def configure_evaluation_review_model(model: EvaluationReviewModel | None) -> None:
    """Injected by the application startup code to provide the model used by evaluate/review; pass None to clear the injected value."""
    global _evaluation_review_model
    _evaluation_review_model = model


def configure_deepseek_evaluation_review_model(
    *, base_url: str | None = None, model_id: str | None = None, client: Any | None = None
) -> DeepSeekEvaluationReviewModel:
    """Create and inject the DeepSeek evaluation/review model; the key is read from an environment variable."""
    model = DeepSeekEvaluationReviewModel(base_url=base_url, model_id=model_id, client=client)
    configure_evaluation_review_model(model)
    return model


def _resolve_keyword_matcher() -> tuple[KeywordMatcher | None, str | None]:
    """Prefer the matcher injected by the application; create one automatically when the DeepSeek environment variables are complete."""
    global _auto_deepseek_matcher
    if _keyword_matcher is not None:
        return _keyword_matcher, None
    if _auto_deepseek_matcher is not None:
        return _auto_deepseek_matcher, None
    try:
        _auto_deepseek_matcher = DeepSeekKeywordMatcher()
        return _auto_deepseek_matcher, None
    except KeywordMatcherError as exc:
        return None, str(exc)


def _resolve_evaluation_review_model() -> tuple[EvaluationReviewModel | None, str | None]:
    """Prefer the model injected by the application; create one automatically when the DeepSeek environment variables are complete."""
    global _auto_evaluation_review_model
    if _evaluation_review_model is not None:
        return _evaluation_review_model, None
    if _auto_evaluation_review_model is not None:
        return _auto_evaluation_review_model, None
    try:
        _auto_evaluation_review_model = DeepSeekEvaluationReviewModel()
        return _auto_evaluation_review_model, None
    except EvaluationReviewModelError as exc:
        return None, str(exc)
