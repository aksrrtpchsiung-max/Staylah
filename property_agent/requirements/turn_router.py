"""Use controlled intent classification to route user messages to requirements, confirmation, housing Q&A, or out-of-scope branches."""

from __future__ import annotations

import os
from typing import Any, Literal, Protocol

import httpx
from pydantic import Field

from .deepseek_parser import DeepSeekAPIError, DeepSeekConfigurationError, DeepSeekParserConfig
from .models import StrictModel


TurnIntent = Literal[
    "requirement_update",
    "confirmation",
    "cancellation",
    "housing_question",
    "listing_request",
    "out_of_scope",
]


class TurnIntentDecision(StrictModel):
    """Store the primary intent of a single-turn message and whether profile modification is allowed."""

    intent: TurnIntent = Field(description="Primary intent of the current user message.")
    requires_fresh_data: bool = Field(
        description="Whether answering the message requires current external information."
    )
    mutates_profile: bool = Field(
        description="Whether the message explicitly adds, removes, or changes housing requirements."
    )


class TurnIntentClassifier(Protocol):
    """Define a conversation-aware single-turn intent classification interface."""

    def classify(self, text: str, *, workflow_status: str) -> TurnIntentDecision:
        """Return the routing intent without parsing or modifying any profile fields."""


TURN_CLASSIFIER_PROMPT = """Classify the current user message for Falcon, a Singapore housing assistant.

Return JSON only and follow the supplied schema.

Use requirement_update when the user explicitly adds, removes, corrects, or states desired housing conditions.
Use confirmation or cancellation only when the message responds to the active housing workflow.
Use housing_question for Singapore housing knowledge, market, policy, affordability, commute, or neighbourhood questions whose answer can help the user understand or refine requirements. Questions such as whether a budget is realistic are housing_question.
Use listing_request when the user asks to find, show, rank, compare, contact, shortlist, or check availability of specific current property listings. A listing_request may later update the profile, but it must not use Falcon A's housing web-search answer path.
Use out_of_scope for messages unrelated to Singapore housing or the active housing conversation.

requires_fresh_data is true for prices, market conditions, laws, policies, availability, schedules, or other time-sensitive facts.
mutates_profile is true only for requirement_update or listing_request when the user explicitly states usable requirements. Questions do not mutate the profile unless the user explicitly asks to adopt a condition.
"""


class DeepSeekTurnIntentClassifier:
    """Use DeepSeek JSON Output for controlled single-turn routing classification."""

    def __init__(
        self,
        config: DeepSeekParserConfig | None = None,
        *,
        api_key: str | None = None,
        client: Any | None = None,
    ) -> None:
        """Inject model configuration, optional in-process key, and test HTTP client."""

        self._config = config or DeepSeekParserConfig.from_runtime()
        self._api_key = api_key
        self._client = client

    def classify(self, text: str, *, workflow_status: str) -> TurnIntentDecision:
        """Return a strict routing result combined with the current workflow state."""

        normalized = text.strip().lower().rstrip(".!")
        if normalized in {"yes", "y", "confirm", "confirmed", "yes indeed", "correct", "no problem"}:
            return TurnIntentDecision(
                intent="confirmation",
                requires_fresh_data=False,
                mutates_profile=False,
            )
        if normalized in {"cancel", "not looking anymore", "end", "stop"}:
            return TurnIntentDecision(
                intent="cancellation",
                requires_fresh_data=False,
                mutates_profile=False,
            )

        api_key = self._api_key or os.getenv(self._config.api_key_env)
        if not api_key:
            raise DeepSeekConfigurationError(
                f"Environment variable {self._config.api_key_env} is missing; "
                "the API key must not be stored in source code or Git-tracked files"
            )
        schema = TurnIntentDecision.model_json_schema()
        client = self._client or httpx.Client(timeout=self._config.timeout_seconds)
        should_close = self._client is None
        try:
            response = client.post(
                f"{self._config.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": self._config.model,
                    "messages": [
                        {"role": "system", "content": TURN_CLASSIFIER_PROMPT},
                        {
                            "role": "user",
                            "content": (
                                f"Workflow status: {workflow_status}\n"
                                f"User input: {text}\n"
                                f"Required JSON Schema: {schema}"
                            ),
                        },
                    ],
                    "response_format": {"type": "json_object"},
                    "thinking": {"type": "disabled"},
                    "temperature": 0,
                    "max_tokens": 180,
                },
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            decision = TurnIntentDecision.model_validate_json(content)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise DeepSeekAPIError(f"Turn intent classification failed: {type(exc).__name__}") from None
        finally:
            if should_close:
                client.close()
        return decision
