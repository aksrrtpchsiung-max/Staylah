"""Directly generate normalized requirements via DeepSeek JSON Output."""

import json
import os
from typing import Any, Literal, Protocol

import httpx
from pydantic import Field

from .models import CommuteRequirement, ConstraintStrength, DestinationType, Intent, IntentConstraint, IssueCode, LocationRelation, LocationRequirement, MoneyConstraint, NormalizedRequirement, NumericConstraint, NumericOperator, ParserMetadata, PreferencePriority, PreferenceRequirement, PreferenceTopic, ProfileFactField, ProfileFactRequirement, PricePeriod, PropertyType, PropertyTypeConstraint, RentalScope, RentalScopeConstraint, RequirementIssue, RequirementResult, SourceSpan, StrictModel, TravelMode


SYSTEM_PROMPT = """You are a Singapore housing requirement-understanding agent. Convert the user's natural-language input directly into normalized JSON.

Return one JSON object only. Do not return Markdown, explanations, SQL, a SearchPlan, or tool arguments.
Use null or an empty array for anything the user did not explicitly state. Do not fill gaps with assumptions.
Every source_text must be copied verbatim from the user input. Never paraphrase, translate, or fabricate evidence.
user_context may contain only housing-relevant occupant count, child-planning facts, workplaces, or schools. Do not store names or relationship labels.
Normalize monetary amounts to positive integer SGD. Interpret wording such as "under", "below", "at most", or "maximum" as an upper bound. Set approximate=true for wording such as "around" or "approximately".
Use hard for explicit requirements, prohibitions, upper bounds, or lower bounds. Use soft for preferences or negotiable wording such as "prefer", "ideally", or "around".
For a location, return only the entity named by the user and its in/near relation. resolution_status must be unresolved. Never invent a location ID or coordinates.
For a commute destination without a stated time limit, set max_minutes to null. Never use 0 as a missing value. Treat general wording such as "convenient commute" as a soft requirement.
Create a commute item only when its destination is explicitly present in that item's source_text. Never use "unknown" or another placeholder as a destination.
Preference topics must use only schema enum values. Normalize a private attached bathroom to ensuite_bathroom and proximity to a bus stop to near_bus_stop.
Preference values must use the topic's controlled vocabulary: furnishing is exactly one of "fully", "partially", or "unfurnished" and must never be a boolean; the boolean topics (ensuite_bathroom, owner_not_staying, cooking_allowed, utilities_included, wifi_included, visitors_allowed, pets_allowed) use true; every other topic keeps a short string or the user's own wording.
"Furnished" without a degree means the home must not be unfurnished; only use "fully" when the user says fully furnished, and "partially" when the user asks for partial furnishing.
When a housing preference cannot map to a known topic, use topic=other instead of dropping it. Preserve the complete preference in source_text.
unresolved_fields is advisory for the current message only. Do not use it to request optional fields. The workflow determines conversation-level completeness from the accumulated profile.
The output must strictly follow the JSON Schema included with the user message.
"""


class RequirementInputError(ValueError):
    """Indicates that the user input is empty or the message identifier is missing."""


class DeepSeekConfigurationError(RuntimeError):
    """Indicates that the DeepSeek model or API Key configuration is missing."""


class DeepSeekAPIError(RuntimeError):
    """Indicates that the DeepSeek request failed or the response does not conform to the strict output contract."""


class RequirementInterpreter(Protocol):
    """Defines a unified interface for any LLM to directly generate normalized requirements."""

    def understand(self, text: str, *, message_id: str) -> RequirementResult:
        """Directly converts a single natural language message into a normalized requirement."""


class LLMSource(StrictModel):
    """Defines the verbatim source evidence shared by all LLM output fields."""

    source_text: str = Field(description="Evidence copied verbatim from the user input.", min_length=1)


class LLMMoneyConstraint(LLMSource):
    """Defines the standard monetary amount constraint directly output by the LLM."""

    currency: Literal["SGD"] = Field(description="Currency, fixed to SGD for this project.")
    max_price: int = Field(description="Normalized integer budget ceiling.", gt=0)
    period: PricePeriod | None = Field(default=None, description="Price period.")
    approximate: bool = Field(description="Whether the amount is approximate.")
    strength: ConstraintStrength = Field(description="Whether the budget is hard or soft.")


class LLMIntentConstraint(LLMSource):
    """Defines the standard transaction intent directly output by the LLM."""

    value: Intent = Field(description="Rent or buy intent.")
    strength: ConstraintStrength = Field(description="Whether the intent is hard or soft.")


class LLMRentalScopeConstraint(LLMSource):
    """Defines the standard lease scope directly output by the LLM."""

    value: RentalScope = Field(description="Whole unit, private room, or bedspace.")
    strength: ConstraintStrength = Field(description="Whether the rental scope is hard or soft.")


class LLMPropertyTypeConstraint(LLMSource):
    """Defines the standard residential type directly output by the LLM."""

    value: PropertyType = Field(description="Normalized residential property type.")
    strength: ConstraintStrength = Field(description="Whether the property type is hard or soft.")


class LLMNumericConstraint(LLMSource):
    """Defines the standard numeric constraint directly output by the LLM."""

    operator: NumericOperator = Field(description="Equality, minimum, or maximum operator.")
    value: int = Field(description="Normalized non-negative integer.", ge=0)
    strength: ConstraintStrength = Field(description="Whether the numeric condition is hard or soft.")


class LLMLocationRequirement(LLMSource):
    """Defines the location entities and spatial relations output by the LLM, excluding location IDs."""

    raw_name: str = Field(description="Location entity referred to by the user.", min_length=1)
    relation: LocationRelation = Field(description="Whether the home should be in or near the location.")
    resolution_status: Literal["unresolved"] = Field(description="Must be unresolved.")
    strength: ConstraintStrength = Field(description="Whether the location condition is hard or soft.")


class LLMCommuteRequirement(LLMSource):
    """Defines the standard commute requirement directly output by the LLM."""

    destination: str = Field(description="Commute destination.", min_length=1)
    destination_type: DestinationType = Field(description="Business type of the destination.")
    travel_mode: TravelMode = Field(description="Normalized travel mode.")
    max_minutes: int | None = Field(
        default=None,
        description="Maximum commute time in minutes, or null when the user gives no limit.",
        gt=0,
    )
    strength: ConstraintStrength = Field(description="Whether the commute condition is hard or soft.")


class LLMPreferenceRequirement(LLMSource):
    """Defines additional requirements that the LLM directly maps to stable topic enums."""

    topic: PreferenceTopic = Field(description="Normalized preference topic.")
    value: Any = Field(
        description=(
            "Value for the topic, using that topic's controlled vocabulary. "
            "furnishing: one of 'fully', 'partially', 'unfurnished' (never a boolean). "
            "ensuite_bathroom, owner_not_staying, cooking_allowed, utilities_included, "
            "wifi_included, visitors_allowed, pets_allowed: true. "
            "Other topics: a short string or the user's own wording."
        )
    )
    priority: PreferencePriority = Field(description="Relative priority.")
    strength: ConstraintStrength = Field(description="Whether the preference is negotiable.")


class LLMProfileFact(LLMSource):
    """Defines the controlled conversation user background fields output by the LLM."""

    field: ProfileFactField = Field(description="Occupant count, child planning, workplace, or school field.")
    value: Any = Field(description="JSON value for the selected field.")


class LLMNormalizedOutput(StrictModel):
    """Defines the final normalized JSON Schema sent to DeepSeek."""

    intent: LLMIntentConstraint | None = None
    user_context: list[LLMProfileFact] = Field(default_factory=list)
    budget: LLMMoneyConstraint | None = None
    rental_scope: LLMRentalScopeConstraint | None = None
    locations: list[LLMLocationRequirement] = Field(default_factory=list)
    bedrooms: LLMNumericConstraint | None = None
    property_types: list[LLMPropertyTypeConstraint] = Field(default_factory=list)
    commute: list[LLMCommuteRequirement] = Field(default_factory=list)
    preferences: list[LLMPreferenceRequirement] = Field(default_factory=list)
    unresolved_fields: list[str] = Field(default_factory=list)


class DeepSeekParserConfig(StrictModel):
    """Stores the DeepSeek structured generation configuration without the key."""

    base_url: str = Field(default="https://api.deepseek.com", description="DeepSeek API base URL.")
    model: str = Field(default="deepseek-v4-flash", description="Requested model ID.")
    api_key_env: str = Field(default="DEEPSEEK_API_KEY", description="API-key environment variable.")
    timeout_seconds: float = Field(default=45.0, description="Request timeout in seconds.", gt=0)
    max_tokens: int = Field(default=2600, description="Maximum structured-output tokens.", gt=0)

    @classmethod
    def from_runtime(cls, settings: Any | None = None) -> "DeepSeekParserConfig":
        """Builds the configuration from the DeepSeek section of runtime.toml; the key is still read only from environment variables."""

        from property_agent.runtime.settings import load_runtime_settings

        settings = settings or load_runtime_settings().deepseek
        return cls(
            base_url=settings.base_url,
            model=settings.model,
            api_key_env=settings.api_key_env,
            timeout_seconds=settings.timeout_seconds,
            max_tokens=settings.max_tokens,
        )


class DeepSeekRequirementInterpreter:
    """Calls DeepSeek and directly returns the validated NormalizedRequirement."""

    def __init__(
        self,
        config: DeepSeekParserConfig | None = None,
        *,
        api_key: str | None = None,
        client: Any | None = None,
    ) -> None:
        """Injects the keyless configuration, an optional in-process key, and a test HTTP client."""

        self._config = config or DeepSeekParserConfig.from_runtime()
        self._api_key = api_key
        self._client = client

    def understand(self, text: str, *, message_id: str) -> RequirementResult:
        """Calls the model once, validates its normalized JSON, and supplements verifiable source positions."""

        if not message_id.strip():
            raise RequirementInputError("message_id must not be empty")
        if not text.strip():
            raise RequirementInputError("User input must not be empty")
        api_key = self._api_key or os.getenv(self._config.api_key_env)
        if not api_key:
            raise DeepSeekConfigurationError(
                f"Environment variable {self._config.api_key_env} is missing; "
                "the API key must not be stored in source code or Git-tracked files"
            )

        response_data = self._request_json(text, api_key)
        llm_output = self._validate_output(response_data)
        requirement, issues = self._attach_sources(llm_output, text, message_id)
        metadata = ParserMetadata(
            provider="deepseek",
            model=self._config.model,
            reported_model=self._optional_string(response_data.get("model")),
            request_id=self._optional_string(response_data.get("id")),
            usage=self._safe_usage(response_data.get("usage")),
        )
        return RequirementResult(requirement=requirement, issues=issues, metadata=metadata)

    def _request_json(self, text: str, api_key: str) -> dict[str, Any]:
        """Sends a JSON request containing the strict schema and does not log Authorization."""

        schema = json.dumps(LLMNormalizedOutput.model_json_schema(), ensure_ascii=False)
        client = self._client or httpx.Client(timeout=self._config.timeout_seconds)
        should_close = self._client is None
        try:
            response = client.post(
                f"{self._config.base_url.rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._config.model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": f"User input: {text}\n\nRequired JSON Schema: {schema}",
                        },
                    ],
                    "response_format": {"type": "json_object"},
                    "thinking": {"type": "disabled"},
                    "temperature": 0,
                    "max_tokens": self._config.max_tokens,
                },
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            raise DeepSeekAPIError(f"DeepSeek API returned HTTP {exc.response.status_code}") from None
        except (httpx.HTTPError, ValueError) as exc:
            raise DeepSeekAPIError(f"DeepSeek request or JSON decoding failed: {type(exc).__name__}") from None
        finally:
            if should_close:
                client.close()
        if not isinstance(data, dict):
            raise DeepSeekAPIError("The top-level DeepSeek API response is not a JSON object")
        return data

    @staticmethod
    def _validate_output(response_data: dict[str, Any]) -> LLMNormalizedOutput:
        """Reads the assistant JSON and rejects unknown or illegal fields using the strict schema."""

        try:
            content = response_data["choices"][0]["message"]["content"]
            if not content:
                raise ValueError("empty content")
            return LLMNormalizedOutput.model_validate_json(content)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise DeepSeekAPIError(
                f"DeepSeek output does not match the requirement contract: {type(exc).__name__}"
            ) from None

    def _attach_sources(
        self,
        output: LLMNormalizedOutput,
        text: str,
        message_id: str,
    ) -> tuple[NormalizedRequirement, list[RequirementIssue]]:
        """Verifies source_text and constructs the final normalized requirement with SourceSpan."""

        issues: list[RequirementIssue] = []
        requirement = NormalizedRequirement(
            message_id=message_id,
            original_text=text,
            intent=self._intent_with_source(output.intent, text, message_id, issues),
            user_context=self._user_context_with_source(output.user_context, text, message_id, issues),
            budget=self._money_with_source(output.budget, text, message_id, issues),
            rental_scope=self._scope_with_source(output.rental_scope, text, message_id, issues),
            locations=self._locations_with_source(output.locations, text, message_id, issues),
            bedrooms=self._number_with_source(output.bedrooms, text, message_id, issues),
            property_types=self._property_types_with_source(
                output.property_types,
                text,
                message_id,
                issues,
            ),
            commute=self._commute_with_source(output.commute, text, message_id, issues),
            preferences=self._preferences_with_source(output.preferences, text, message_id, issues),
            unresolved_fields=output.unresolved_fields,
        )
        return requirement, issues

    def _user_context_with_source(
        self,
        values: list[LLMProfileFact],
        text: str,
        message_id: str,
        issues: list[RequirementIssue],
    ) -> list[ProfileFactRequirement]:
        """Verifies the user background source text and constructs controlled conversation profile facts."""

        result: list[ProfileFactRequirement] = []
        for value in values:
            source = self._source_span(value.source_text, text, message_id, "user_context", issues)
            if source is None:
                continue
            result.append(ProfileFactRequirement(field=value.field, value=value.value, source=source))
        return result

    def _intent_with_source(
        self,
        value: LLMIntentConstraint | None,
        text: str,
        message_id: str,
        issues: list[RequirementIssue],
    ) -> IntentConstraint | None:
        """Verifies the transaction intent source text and then constructs the final standard intent."""

        if value is None:
            return None
        source = self._source_span(value.source_text, text, message_id, "intent", issues)
        if source is None:
            return None
        return IntentConstraint(value=value.value, strength=value.strength, source=source)

    def _scope_with_source(
        self,
        value: LLMRentalScopeConstraint | None,
        text: str,
        message_id: str,
        issues: list[RequirementIssue],
    ) -> RentalScopeConstraint | None:
        """Verifies the lease scope source text and then constructs the final standard scope."""

        if value is None:
            return None
        source = self._source_span(value.source_text, text, message_id, "rental_scope", issues)
        if source is None:
            return None
        return RentalScopeConstraint(value=value.value, strength=value.strength, source=source)

    def _property_types_with_source(
        self,
        values: list[LLMPropertyTypeConstraint],
        text: str,
        message_id: str,
        issues: list[RequirementIssue],
    ) -> list[PropertyTypeConstraint]:
        """Verifies the source text of each residential type and preserves the user's order of expression."""

        result: list[PropertyTypeConstraint] = []
        for value in values:
            source = self._source_span(value.source_text, text, message_id, "property_types", issues)
            if source is None:
                continue
            result.append(
                PropertyTypeConstraint(
                    value=value.value,
                    strength=value.strength,
                    source=source,
                )
            )
        return result

    def _money_with_source(
        self,
        value: LLMMoneyConstraint | None,
        text: str,
        message_id: str,
        issues: list[RequirementIssue],
    ) -> MoneyConstraint | None:
        """Verifies the budget source text and then constructs the final standard monetary amount."""

        if value is None:
            return None
        source = self._source_span(value.source_text, text, message_id, "budget", issues)
        if source is None:
            return None
        return MoneyConstraint(
            currency=value.currency,
            max_price=value.max_price,
            period=value.period,
            approximate=value.approximate,
            strength=value.strength,
            source=source,
        )

    def _number_with_source(
        self,
        value: LLMNumericConstraint | None,
        text: str,
        message_id: str,
        issues: list[RequirementIssue],
    ) -> NumericConstraint | None:
        """Verifies the numeric source text and then constructs the final comparison constraint."""

        if value is None:
            return None
        source = self._source_span(value.source_text, text, message_id, "bedrooms", issues)
        if source is None:
            return None
        return NumericConstraint(
            operator=value.operator,
            value=value.value,
            strength=value.strength,
            source=source,
        )

    def _locations_with_source(
        self,
        values: list[LLMLocationRequirement],
        text: str,
        message_id: str,
        issues: list[RequirementIssue],
    ) -> list[LocationRequirement]:
        """Verifies the location source text and constructs a location requirement fixed to unresolved."""

        result: list[LocationRequirement] = []
        for value in values:
            source = self._source_span(value.source_text, text, message_id, "locations", issues)
            if source is None:
                continue
            result.append(
                LocationRequirement(
                    raw_name=value.raw_name,
                    relation=value.relation,
                    strength=value.strength,
                    source=source,
                )
            )
        return result

    def _commute_with_source(
        self,
        values: list[LLMCommuteRequirement],
        text: str,
        message_id: str,
        issues: list[RequirementIssue],
    ) -> list[CommuteRequirement]:
        """Verifies the commute source text and constructs the final commute requirement."""

        result: list[CommuteRequirement] = []
        for value in values:
            source = self._source_span(value.source_text, text, message_id, "commute", issues)
            if source is None:
                continue
            destination = value.destination.strip()
            if (
                destination.casefold() in {"unknown", "unspecified", "n/a", "none"}
                or destination.casefold() not in value.source_text.casefold()
            ):
                issues.append(
                    RequirementIssue(
                        code=IssueCode.UNSUPPORTED_SOURCE,
                        field="commute.destination",
                        message=(
                            "The commute destination is not explicitly supported by source_text; "
                            "the commute item was discarded."
                        ),
                    )
                )
                continue
            result.append(
                CommuteRequirement(
                    destination=destination,
                    destination_type=value.destination_type,
                    travel_mode=value.travel_mode,
                    max_minutes=value.max_minutes,
                    strength=value.strength,
                    source=source,
                )
            )
        return result

    def _preferences_with_source(
        self,
        values: list[LLMPreferenceRequirement],
        text: str,
        message_id: str,
        issues: list[RequirementIssue],
    ) -> list[PreferenceRequirement]:
        """Verifies the preference source text and constructs the final stable topic requirement."""

        result: list[PreferenceRequirement] = []
        for value in values:
            source = self._source_span(value.source_text, text, message_id, "preferences", issues)
            if source is None:
                continue
            result.append(
                PreferenceRequirement(
                    topic=value.topic,
                    value=value.value,
                    priority=value.priority,
                    strength=value.strength,
                    source=source,
                )
            )
        return result

    @staticmethod
    def _source_span(
        source_text: str,
        text: str,
        message_id: str,
        field: str,
        issues: list[RequirementIssue],
    ) -> SourceSpan | None:
        """Converts a verbatim source_text into a reliable index; otherwise the field is discarded."""

        start = text.find(source_text)
        if start < 0:
            issues.append(
                RequirementIssue(
                    code=IssueCode.UNSUPPORTED_SOURCE,
                    field=field,
                    message=(
                        "The model returned source_text that does not occur verbatim in the user input; "
                        "the field was discarded."
                    ),
                )
            )
            return None
        return SourceSpan(
            message_id=message_id,
            text=source_text,
            start=start,
            end=start + len(source_text),
        )

    @staticmethod
    def _safe_usage(raw_usage: Any) -> dict[str, int]:
        """Keeps only integer token usage to avoid writing unknown fields into the checkpoint."""

        if not isinstance(raw_usage, dict):
            return {}
        allowed = ("prompt_tokens", "completion_tokens", "total_tokens")
        return {
            key: value
            for key in allowed
            if isinstance((value := raw_usage.get(key)), int)
        }

    @staticmethod
    def _optional_string(value: Any) -> str | None:
        """Accepts only non-empty string metadata from the response."""

        return value if isinstance(value, str) and value else None
