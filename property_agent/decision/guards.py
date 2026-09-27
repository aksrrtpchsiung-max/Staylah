"""Program-side guardrail: the output of module C must pass through this layer before entering routing.

The model may suggest "turn one more page" or "raise the budget to 3600", but it cannot use these two kinds of output to bypass hard conditions:
- A supplementary search instruction may only change the lookup method, and must be attached to the requirement version fixed for this run;
- A concession proposal must be a genuine relaxation, must target whitelisted fields, and must take effect only after the user gives an explicit answer.

Illegal output is discarded here and an issue is recorded, and is not passed to decide_next -- otherwise a single model jitter
would turn into INTERNAL_ERROR.
"""
from __future__ import annotations

from typing import Any, Sequence

from property_agent.contracts import (
    ConversationProfile,
    Issue,
    Recommendation,
    RelaxationProposal,
    SearchDirective,
)
from property_agent.profiles import (
    RELAXABLE_FIELDS,
    is_relaxation,
    read_relaxable_value,
)
from property_agent.results import make_issue

DIRECTIVE_REASONS = frozenset({"insufficient_candidates", "incomplete_coverage"})

# The fields each strategy adjustment must carry. The union type itself cannot express "change budget", so the whitelist confirms once more.
STRATEGY_REQUIRED_KEYS = {
    "next_page": ("query_id", "cursor"),
    "alias_query": ("entity_id", "alias"),
    "alternate_source": ("source",),
}

def sanitize_proposals(
    proposals: Sequence[Any] | None, profile: ConversationProfile
) -> tuple[list[RelaxationProposal], list[Issue]]:
    """Return the proposal that can be taken to ask the user, along with the rejection reasons. The proposal itself never takes effect here."""
    issues: list[Issue] = []
    accepted: list[RelaxationProposal] = []
    seen_ids: set[str] = set()

    for index, proposal in enumerate(proposals or []):
        path = f"assessment.relaxation_proposals[{index}]"
        if not isinstance(proposal, dict):
            issues.append(make_issue("INVALID_OUTPUT", "concession proposal must be an object", field_path=path))
            continue

        proposal_id = proposal.get("proposal_id")
        if not isinstance(proposal_id, str) or not proposal_id.strip():
            issues.append(
                make_issue("INVALID_OUTPUT", "proposal is missing proposal_id", field_path=f"{path}.proposal_id")
            )
            continue
        if proposal_id in seen_ids:
            issues.append(
                make_issue("INVALID_OUTPUT", "proposal ID is duplicated", field_path=f"{path}.proposal_id")
            )
            continue

        if proposal.get("requires_user_confirmation") is not True:
            issues.append(
                make_issue(
                    "CONSTRAINT_CHANGE_NOT_ALLOWED",
                    "relaxing a hard condition must be marked as requiring user confirmation",
                    field_path=f"{path}.requires_user_confirmation",
                )
            )
            continue

        field = proposal.get("field")
        if field not in RELAXABLE_FIELDS:
            issues.append(
                make_issue(
                    "CONSTRAINT_CHANGE_NOT_ALLOWED",
                    f"field {field!r} is not in the relaxable whitelist",
                    field_path=f"{path}.field",
                )
            )
            continue

        try:
            current = read_relaxable_value(profile, field)
        except KeyError:
            issues.append(
                make_issue("INVALID_OUTPUT", "proposal field does not exist in the profile", field_path=f"{path}.field")
            )
            continue

        # The proposal must be constructed based on the value the user currently confirmed, otherwise an old conclusion may be applied to a new requirement.
        if proposal.get("old_value") != current:
            issues.append(
                make_issue(
                    "STATE_CONFLICT",
                    "the proposal's old_value is inconsistent with the current profile",
                    field_path=f"{path}.old_value",
                )
            )
            continue

        if not is_relaxation(field, current, proposal.get("proposed_value")):
            issues.append(
                make_issue(
                    "CONSTRAINT_CHANGE_NOT_ALLOWED",
                    "the proposal is not a recognizable relaxation direction",
                    field_path=f"{path}.proposed_value",
                )
            )
            continue

        seen_ids.add(proposal_id)
        accepted.append(proposal)  # type: ignore[arg-type]

    return accepted, issues


def sanitize_directive(
    directive: Any,
    *,
    profile_version: int,
    allowed_sources: Sequence[str],
) -> tuple[SearchDirective | None, list[Issue]]:
    """If the supplementary search instruction passes, it is returned as is; if any item is invalid, the whole thing is discarded."""
    path = "assessment.search_directive"
    if directive is None:
        return None, []
    if not isinstance(directive, dict):
        return None, [make_issue("INVALID_OUTPUT", "supplementary search instruction must be an object", field_path=path)]

    if directive.get("reason_code") not in DIRECTIVE_REASONS:
        return None, [
            make_issue(
                "INVALID_OUTPUT", "the supplementary search instruction's reason_code is invalid", field_path=f"{path}.reason_code"
            )
        ]

    if directive.get("base_profile_version") != profile_version:
        return None, [
            make_issue(
                "STATE_CONFLICT",
                "the supplementary search instruction is attached to a different requirement version",
                field_path=f"{path}.base_profile_version",
            )
        ]

    changes = directive.get("strategy_changes")
    if not isinstance(changes, list) or not changes:
        return None, [
            make_issue(
                "INVALID_OUTPUT",
                "the supplementary search instruction must provide at least one strategy adjustment",
                field_path=f"{path}.strategy_changes",
            )
        ]

    for index, change in enumerate(changes):
        change_path = f"{path}.strategy_changes[{index}]"
        kind = change.get("kind") if isinstance(change, dict) else None
        required = STRATEGY_REQUIRED_KEYS.get(kind)  # type: ignore[arg-type]
        if required is None:
            return None, [
                make_issue(
                    "CONSTRAINT_CHANGE_NOT_ALLOWED",
                    f"strategy adjustment {kind!r} is not among the allowed lookup methods",
                    field_path=f"{change_path}.kind",
                )
            ]
        for key in required:
            if not isinstance(change.get(key), str) or not change[key].strip():
                return None, [
                    make_issue(
                        "INVALID_OUTPUT", f"{kind} is missing {key}", field_path=f"{change_path}.{key}"
                    )
                ]
        if kind == "alternate_source" and change["source"] not in allowed_sources:
            return None, [
                make_issue(
                    "SOURCE_UNAVAILABLE",
                    f"source {change['source']!r} is not within the range allowed by the backend",
                    field_path=f"{change_path}.source",
                )
            ]

    if not isinstance(directive.get("evidence_listing_keys"), list):
        return None, [
            make_issue(
                "INVALID_OUTPUT",
                "evidence_listing_keys must be an array",
                field_path=f"{path}.evidence_listing_keys",
            )
        ]

    return directive, []  # type: ignore[return-value]


def prepare_for_display(
    recommendation: Recommendation, *, display_limit: int, eligible_keys: set[str]
) -> tuple[Recommendation, list[Issue]]:
    """The last programmatic check before publishing: display limit, consecutive ranks, candidate ownership.

    This does not reorder the sequence given by the model, only truncates and rejects out-of-range candidates.
    """
    issues: list[Issue] = []
    items = list(recommendation.get("ordered_items") or [])

    for index, item in enumerate(items):
        if item.get("listing_key") not in eligible_keys:
            issues.append(
                make_issue(
                    "INVALID_OUTPUT",
                    f"the recommendation references {item.get('listing_key')!r} which is not among the candidates handed over by B",
                    field_path=f"recommendation.ordered_items[{index}].listing_key",
                )
            )

    ranks = [item.get("rank") for item in items]
    if ranks != list(range(1, len(items) + 1)):
        issues.append(
            make_issue("INVALID_OUTPUT", "recommendation ranks are not consecutive integers starting from 1", field_path="recommendation.ordered_items")
        )

    if issues:
        return recommendation, issues

    if len(items) > display_limit:
        trimmed = dict(recommendation)
        trimmed["ordered_items"] = items[:display_limit]
        trimmed["limitations"] = [
            *recommendation.get("limitations", []),
            f"This time only the first {display_limit} items are displayed, and B has {len(items)} candidates in total.",
        ]
        return trimmed, []  # type: ignore[return-value]

    return recommendation, []
