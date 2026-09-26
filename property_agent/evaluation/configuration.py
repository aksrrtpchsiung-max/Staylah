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
    """由应用启动代码注入匹配器；传入 None 可关闭已注入的匹配器。"""
    global _keyword_matcher
    _keyword_matcher = matcher


def configure_deepseek_keyword_matcher(
    *, base_url: str | None = None, model_id: str | None = None, client: Any | None = None
) -> DeepSeekKeywordMatcher:
    """创建并注入 DeepSeek 匹配器；密钥从环境变量读取。"""
    matcher = DeepSeekKeywordMatcher(base_url=base_url, model_id=model_id, client=client)
    configure_keyword_matcher(matcher)
    return matcher


def configure_evaluation_review_model(model: EvaluationReviewModel | None) -> None:
    """由应用启动代码注入 evaluate/review 使用的模型；传入 None 清除注入值。"""
    global _evaluation_review_model
    _evaluation_review_model = model


def configure_deepseek_evaluation_review_model(
    *, base_url: str | None = None, model_id: str | None = None, client: Any | None = None
) -> DeepSeekEvaluationReviewModel:
    """创建并注入 DeepSeek 评估／审查模型；密钥从环境变量读取。"""
    model = DeepSeekEvaluationReviewModel(base_url=base_url, model_id=model_id, client=client)
    configure_evaluation_review_model(model)
    return model


def _resolve_keyword_matcher() -> tuple[KeywordMatcher | None, str | None]:
    """优先使用应用注入的匹配器；DeepSeek 环境变量齐全时自动创建。"""
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
    """优先使用应用注入的模型；DeepSeek 环境变量齐全时自动创建。"""
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
