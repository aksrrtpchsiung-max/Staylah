"""Input guard responsibilities extracted without changing behavior."""
from __future__ import annotations
import os
from collections.abc import Callable
from typing import Any, Protocol
import httpx
from .deepseek_parser import DeepSeekAPIError, DeepSeekConfigurationError, DeepSeekParserConfig
from .models import InputGuardDecision, IssueCode, RequirementGraphState
from .response_renderer import ResponseRenderer
from .workflow_constants import DEFAULT_USER_ID
from property_agent.requirements.constants import MAX_INPUT_CHARS
from property_agent.requirements.support import _error_update


class InputGuard(Protocol):
    """Defines the interface for determining input validity and Singapore housing relevance."""

    def check(self, text: str, *, workflow_status: str) -> InputGuardDecision:
        """Returns the input structure and business scope determination without generating any profile fields."""


class DeepSeekInputGuard:
    """Uses DeepSeek JSON Output to determine whether the input falls within the Falcon housing service scope."""

    def __init__(
        self,
        config: DeepSeekParserConfig | None = None,
        *,
        api_key: str | None = None,
        client: Any | None = None,
    ) -> None:
        """Injects a keyless configuration, an optional in-process key, and a test HTTP client."""

        self._config = config or DeepSeekParserConfig.from_runtime()
        self._api_key = api_key
        self._client = client

    def check(self, text: str, *, workflow_status: str) -> InputGuardDecision:
        """Validates the length and makes the model return only the two boolean values valid and housing_related."""

        if not text.strip() or len(text) > MAX_INPUT_CHARS:
            return InputGuardDecision(valid=False, housing_related=False)
        api_key = self._api_key or os.getenv(self._config.api_key_env)
        if not api_key:
            raise DeepSeekConfigurationError(
                f"Environment variable {self._config.api_key_env} is missing; "
                "the API key must not be stored in source code or Git-tracked files"
            )
        client = self._client or httpx.Client(timeout=self._config.timeout_seconds)
        should_close = self._client is None
        try:
            response = client.post(
                f"{self._config.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": self._config.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Determine whether the input is related to Singapore residential renting, "
                                "buying, listing requirements, locations, commuting, neighbourhoods, schools, "
                                f"or the current housing conversation. The workflow status is {workflow_status}. "
                                "Answers to confirmation or clarification questions are housing-related. "
                                "Return JSON only: "
                                '{"valid": boolean, "housing_related": boolean}.'
                            ),
                        },
                        {"role": "user", "content": text},
                    ],
                    "response_format": {"type": "json_object"},
                    "thinking": {"type": "disabled"},
                    "temperature": 0,
                    "max_tokens": 80,
                },
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return InputGuardDecision.model_validate_json(content)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise DeepSeekAPIError(f"Input scope classification failed: {type(exc).__name__}") from None
        finally:
            if should_close:
                client.close()


def make_validate_input_node(
    guard: InputGuard,
    renderer: ResponseRenderer | None = None,
) -> Callable[[RequirementGraphState], dict[str, Any]]:
    """Creates a node for checking input legality and housing relevance."""

    response_renderer = renderer or ResponseRenderer()

    def validate_input(state: RequirementGraphState) -> dict[str, Any]:
        """Rejects illegal or irrelevant input and returns the user-specified fixed Falcon prompt."""

        text = state.get("current_input", "")
        if not state.get("message_id", "").strip():
            return _error_update(IssueCode.INVALID_INPUT, "message_id", "message_id must not be empty")
        supplied_user_id = state.get("user_id")
        user_id = DEFAULT_USER_ID if supplied_user_id is None else supplied_user_id.strip()
        if not user_id:
            return _error_update(IssueCode.INVALID_INPUT, "user_id", "user_id must not be empty")
        if not state.get("conversation_id", "").strip():
            return _error_update(
                IssueCode.INVALID_INPUT,
                "conversation_id",
                "conversation_id must not be empty",
            )
        try:
            decision = guard.check(text, workflow_status=state.get("status", "new"))
        except (DeepSeekConfigurationError, DeepSeekAPIError) as exc:
            return _error_update(IssueCode.MODEL_UNAVAILABLE, "input_guard", str(exc))
        if not decision.valid or not decision.housing_related:
            return {
                "user_id": user_id,
                "input_guard": decision.model_dump(mode="json"),
                "assistant_response": response_renderer.out_of_scope(),
                "workflow_route": "end",
                "status": "out_of_scope",
            }
        return {
            "user_id": user_id,
            "input_guard": decision.model_dump(mode="json"),
            "workflow_route": "classify_turn_intent",
        }

    return validate_input
