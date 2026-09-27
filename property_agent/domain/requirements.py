"""Requirement validation and filtered projection internal to B; does not rebuild the ConversationProfile owned by A.

RequirementRequest is the handoff credential submitted by A after confirming the specified version. B validates the conversation, version, and
confirmation time, but has no user confirmation record store, so it cannot claim to have independently verified A's confirmation operation.
The original constraints are always retained; the six filters of the SearchPlan are only expressible necessary conditions, not the full set of requirements.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import math
import re
from pathlib import Path
import sys
from typing import TypedDict, get_type_hints


from property_agent import contracts as contracts
from property_agent.contracts import ContractViolation, HardConstraints, Clarification
from property_agent.domain.validation import fail, timestamp, validate_context, validate_type


# Local search term table; only explains names, does not prove the listing is located in a certain administrative district.
_LOCATION_NAMES = {
    'TAMPINES': ('Tampines',),
    'CLEMENTI': ('Clementi',),
    'PUNGGOL': ('Punggol',),
    'BEDOK': ('Bedok',),
    'BISHAN': ('Bishan',),
    'JURONG_EAST': ('Jurong East',),
    'JURONG_WEST': ('Jurong West',),
    'SENGKANG': ('Sengkang',),
    'WOODLANDS': ('Woodlands',),
    'TOA_PAYOH': ('Toa Payoh',),
    'ANG_MO_KIO': ('Ang Mo Kio',),
    'HOUGANG': ('Hougang',),
    'SERANGOON': ('Serangoon',),
    'PASIR_RIS': ('Pasir Ris',),
    'BUKIT_BATOK': ('Bukit Batok',),
    'QUEENSTOWN': ('Queenstown',),
    'SINGAPORE': ('Singapore',),
}
_LOCATION_LOOKUP = {name.casefold().replace('_', ' '): key
                    for key, names in _LOCATION_NAMES.items() for name in (key, *names)}


class PlanningRequirements(TypedDict):
    """Exists only as an explicit projection in B, and does not contain fabricated profile state, time, or confirmation history."""
    profile_id: str
    version: int
    intent: str
    user_context: list[contracts.ProfileFact]
    listing_constraints: list[contracts.ListingConstraint]
    derived_data_requirements: list[contracts.DerivedDataRequirement]
    open_data_requirements: list[contracts.OpenDataRequirement]
    unresolved: list[str]
    required_filters: HardConstraints
    clarification_questions: list[Clarification]


def location_entity(name: str) -> contracts.Entity:
    name = ' '.join(name.split())
    canonical = _LOCATION_LOOKUP.get(name.casefold().replace('_', ' '))
    if canonical is None:
        # A may retain both names from user text, e.g. Tampines (Tampines).
        # Accept this only when both complete names identify the same known area.
        bilingual = re.fullmatch(r'(.+?)\s*\(([^()]+)\)', name)
        if bilingual:
            identities = [_LOCATION_LOOKUP.get(part.strip().casefold().replace('_', ' '))
                          for part in bilingual.groups()]
            if identities[0] is not None and identities[0] == identities[1]:
                canonical = identities[0]
    return dict(type='location', raw_text=name, canonical_id=canonical,
                aliases=list(_LOCATION_NAMES.get(canonical, ())))


def _validate_source(source, path):
    if not source['message_id'].strip() or not source['text'].strip():
        fail(path, 'The original text and source message ID cannot be empty')
    # start/end are offsets in the original message; source.text may be only an excerpt, so the excerpt length cannot be used to validate the end point.
    if source['start'] < 0 or source['end'] <= source['start']:
        fail(path, 'The original text start and end positions must be a non-negative, increasing range')


def _listing_field_schema(field):
    parent = contracts.Listing
    for component in field.split('.'):
        parent = get_type_hints(parent)[component]
    return parent


def _validate_constraint(constraint, path):
    field, operator, value = (constraint[k] for k in ('field_path', 'operator', 'value'))
    schema = _listing_field_schema(field)
    numeric = field in {'price.amount', 'bedrooms', 'attributes.area_sqft',
                        'attributes.bathrooms', 'attributes.lease_years'}
    comparable = numeric or field == 'listed_date'
    if operator in ('lt', 'lte', 'gt', 'gte', 'between') and not comparable:
        fail(path + '.operator', 'This field does not support size or range comparisons')
    if operator == 'contains' and field not in {'attributes.unit_layout', 'price.currency'}:
        fail(path + '.operator', 'This field does not support text containment comparisons')
    if operator in ('between', 'in'):
        if type(value) is not list or not value or (operator == 'between' and len(value) != 2):
            fail(path + '.value', 'in requires a non-empty array, between requires two ordered endpoints')
        values = value
    else:
        values = [value]
    for item in values:
        if numeric:
            # Listing is an integer; user thresholds still allow finite decimals, e.g. area > 999.5.
            if type(item) not in (int, float) or not math.isfinite(item):
                fail(path + '.value', 'Numeric comparison requires a finite number')
        else:
            validate_type(schema, item, path + '.value')
        if item is None or (type(item) is str and not item.strip()):
            fail(path + '.value', 'The expected value cannot be empty; unknown requirements should be clarified by A')
        if numeric and item < 0:
            fail(path + '.value', 'Quantity and amount cannot be negative')
        if field == 'listed_date':
            try:
                date.fromisoformat(item)
            except ValueError:
                fail(path + '.value', 'Date conditions must use ISO dates')
    if operator == 'between' and value[0] > value[1]:
        fail(path + '.value', 'The lower bound of the range cannot be greater than the upper bound')


def _validate_requirements(value):
    ids = set()
    for field, id_key in (('listing_constraints', 'constraint_id'),
                          ('derived_data_requirements', 'requirement_id'),
                          ('open_data_requirements', 'requirement_id')):
        for index, requirement in enumerate(value[field]):
            path = f'{field}[{index}]'
            key = requirement[id_key]
            if not key.strip() or key in ids:
                fail(path + '.' + id_key, 'The requirement ID cannot be empty and must be unique within this request')
            ids.add(key)
            _validate_source(requirement['source'], path + '.source')
            if field == 'listing_constraints':
                _validate_constraint(requirement, path)
            elif field == 'derived_data_requirements':
                if not requirement['metric'].strip():
                    fail(path + '.metric', 'The data metric cannot be empty')
                for name in ('target', 'unit'):
                    if requirement[name] is not None and not requirement[name].strip():
                        fail(path + '.' + name, 'Cannot be an empty string')
                operator, target = requirement['operator'], requirement['value']
                if operator == 'between' and (
                    type(target) is not list or len(target) != 2
                    or any(type(x) not in (int, float) for x in target)
                    or target[0] > target[1]
                ):
                    fail(path + '.value', 'A derived data range must contain two increasing numeric values')
                if operator in ('lte', 'gte') and type(target) not in (int, float):
                    fail(path + '.value', 'Derived data size comparison requires a numeric value')
            elif not requirement['description'].strip():
                fail(path + '.description', 'The open requirement description cannot be empty')
    for index, fact in enumerate(value['user_context']):
        _validate_source(fact['source'], f'user_context[{index}].source')


def validate_requirement_request(request: contracts.RequirementRequest, ctx: contracts.RunContext) -> None:
    validate_context(ctx)
    validate_type(contracts.RequirementRequest, request, 'request')
    for key in ('request_id', 'conversation_id', 'profile_id'):
        if not request[key].strip():
            fail('request.' + key, 'Cannot be empty')
    if request['conversation_id'] != ctx['conversation_id']:
        raise ContractViolation('STATE_CONFLICT', 'request.conversation_id', 'The request and execution conversation are inconsistent')
    if request['profile_version'] < 0:
        fail('request.profile_version', 'The version cannot be negative')
    if request['profile_version'] == 0:
        raise ContractViolation('INVALID_STATE', 'request.profile_version', 'The initial draft version cannot be handed to B for execution')
    timestamp(request['confirmed_at'], 'request.confirmed_at')
    _validate_requirements(request)
    if any(not field.strip() for field in request['unresolved_fields']):
        fail('request.unresolved_fields', 'Fields pending clarification cannot be empty strings')


def validate_conversation_profile(profile: contracts.ConversationProfile, ctx: contracts.RunContext) -> None:
    validate_context(ctx)
    validate_type(contracts.ConversationProfile, profile, 'profile')
    if not profile['profile_id'].strip():
        fail('profile.profile_id', 'Cannot be empty')
    if profile['version'] < 0:
        fail('profile.version', 'The version cannot be negative')
    if profile['conversation_id'] != ctx['conversation_id'] or profile['user_id'] != ctx['user_id']:
        raise ContractViolation('STATE_CONFLICT', 'profile', 'The profile is inconsistent with the user or conversation of the execution context')
    if (profile['confirmed_version'] != profile['version'] or profile['version'] == 0
            or profile['status'] not in ('confirmed', 'idle') or profile['confirmed_at'] is None):
        raise ContractViolation('INVALID_STATE', 'profile.confirmed_version', 'Only the currently confirmed version of the profile may be used')
    for field in ('created_at', 'updated_at', 'last_user_message_at', 'confirmed_at'):
        timestamp(profile[field], 'profile.' + field)
    if profile['intent'] is None:
        fail('profile.intent', 'A confirmed profile must have a rent or buy intent')
    _validate_requirements(profile)


def _clarify(questions, field, text):
    if not any(question['field'] == field for question in questions):
        questions.append(dict(field=field, text=text))


def _single_value(constraints, field, questions):
    matches = [item for item in constraints if item['field_path'] == field]
    candidates = None
    for item in matches:
        if item['operator'] == 'eq':
            current = {item['value']}
        elif item['operator'] == 'in':
            current = set(item['value'])
        else:
            continue
        candidates = current if candidates is None else candidates & current
    if candidates == set():
        _clarify(questions, 'listing_constraints.' + field, f'The requirements for {field} conflict. Please confirm.')
    return next(iter(candidates)) if candidates is not None and len(candidates) == 1 else None


def _bounds(constraints, field, questions):
    lower, upper = 0, None
    for item in constraints:
        if item['field_path'] != field:
            continue
        operator, value = item['operator'], item['value']
        lo, hi = None, None
        if operator == 'eq':
            lo, hi = math.ceil(value), math.floor(value)
        elif operator == 'lte':
            hi = math.floor(value)
        elif operator == 'lt':
            hi = math.ceil(value) - 1
        elif operator == 'gte':
            lo = math.ceil(value)
        elif operator == 'gt':
            lo = math.floor(value) + 1
        elif operator == 'between':
            lo, hi = math.ceil(value[0]), math.floor(value[1])
        elif operator == 'in':
            lo, hi = math.ceil(min(value)), math.floor(max(value))
        if lo is not None:
            lower = max(lower, lo)
        if hi is not None:
            upper = hi if upper is None else min(upper, hi)
    if upper is not None and lower > upper:
        _clarify(questions, 'listing_constraints.' + field, f'The ranges for {field} do not overlap. Please confirm.')
    return lower, upper


def source_currency_candidates(text: str) -> set[str]:
    """Returns only currencies explicitly present in the original text; the caller must distinguish an empty set from multiple candidates."""
    names = set()
    for currency, pattern in (
        ('SGD', r'\bSGD\b|(?<![A-Za-z])S\$|\bSingapore\s+dollars?\b'),
        ('USD', r'\bUSD\b|(?<![A-Za-z])US\$|\bUS\s+dollars?\b'),
        ('CNY', r'\bCNY\b|\bRMB\b|\b(?:renminbi|Chinese\s+yuan|yuan)\b'),
        ('MYR', r'\bMYR\b|\b(?:Malaysian\s+ringgit|ringgit)\b'),
    ):
        if re.search(pattern, text, re.I):
            names.add(currency)
    return names


def _source_currency(text):
    values = source_currency_candidates(text)
    return next(iter(values)) if len(values) == 1 else None


def source_period_candidates(text: str) -> set[str]:
    """Identifies the pricing period in the user's original text, and does not by default interpret unknown rent as monthly rent."""
    values = {period for period, pattern in (
        ('month', r'\b(?:monthly(?:\s+rent)?|per\s+month|one\s+month|pcm)\b|\bpsf\s*/\s*month\b|/\s*month'),
        ('week', r'\b(?:weekly(?:\s+rent)?|per\s+week)\b|/\s*week'),
        ('total', r'\b(?:total\s+(?:price|amount)|purchase\s+price)\b'),
    ) if re.search(pattern, text, re.I)}
    return values


def _source_period(text):
    values = source_period_candidates(text)
    return next(iter(values)) if len(values) == 1 else None


def normalize_requirements(value: contracts.ConversationProfile | contracts.RequirementRequest) -> PlanningRequirements:
    """Project the necessary filtering that can be delegated to existing sources; the remaining full conditions continue to be executed/reported by the outer service.

    Do not impose hard retrieval filtering on soft conditions, open requirements, or commute targets.
    Date, boolean, inequality, and other constraints outside the six filter fields remain in listing_constraints unchanged.
    """
    questions = []
    hard = [item for item in value['listing_constraints'] if item['strength'] == 'hard']
    intent = value['intent']
    transaction = _single_value(hard, 'transaction_type', questions)
    expected = 'sale' if intent == 'buy' else 'rent'
    if transaction is not None and transaction != expected:
        _clarify(questions, 'intent', 'The transaction types conflict. Are you renting or buying?')
    currency = _single_value(hard, 'price.currency', questions)
    period = _single_value(hard, 'price.period', questions)
    price_conditions = [item for item in hard if item['field_path'] == 'price.amount']
    amount_text = '\n'.join(dict.fromkeys(item['source']['text'] for item in price_conditions))
    if currency is None:
        currency = _source_currency(amount_text)
    if period is None:
        period = _source_period(amount_text)
    if currency is not None:
        currency = currency.strip().upper()
    if price_conditions and currency is None:
        _clarify(questions, 'listing_constraints.price.currency', 'Which currency is your budget in?')
    if price_conditions and period is None:
        if intent == 'buy':
            # The purchase amount corresponds to a one-time total price; for rentals, do not arbitrarily assume monthly or weekly rent.
            period = 'total'
        else:
            _clarify(questions, 'listing_constraints.price.period', 'Is your rental budget per month or per week?')
    _, maximum = _bounds(hard, 'price.amount', questions)
    minimum, _ = _bounds(hard, 'bedrooms', questions)
    scope = _single_value(hard, 'attributes.listing_scope', questions)
    if scope == 'bedspace':
        # The old SearchPlan filter structure only has whole-unit/single-room; keep the original constraints for subsequent processing, do not covertly change to room.
        scope = None
    locations = []
    for index, requirement in enumerate(value['derived_data_requirements']):
        if (requirement['strength'] != 'hard' or requirement['category'] != 'accessibility'
                or requirement['metric'] != 'residential_area' or requirement['operator'] != 'eq'):
            continue
        target = requirement['target']
        if target is None and type(requirement['value']) is str:
            target = requirement['value']
        if requirement['value'] is False:
            continue
        if target is None:
            _clarify(questions, f'derived_data_requirements[{index}].target', 'Which area would you like to live in?')
            continue
        if target.strip().casefold() == 'jurong':
            _clarify(questions, f'derived_data_requirements[{index}].target', 'Do you mean Jurong East, Jurong West, or either?')
            continue
        entity = location_entity(target)
        key = entity['canonical_id'] or entity['raw_text']
        if key not in locations:
            locations.append(key)
    unresolved = value.get('unresolved_fields', value.get('unresolved', []))
    # Unresolved open/derived preferences will not block core retrieval. Unknown fields are not self-upgraded to hard conditions.
    for field in unresolved:
        root = field.split(':', 1)[0]
        if root == 'intent' or root.startswith(('listing_constraints.price.', 'listing_constraints.transaction_type')):
            _clarify(questions, root, f'Please clarify this required detail: {field}.')
        elif root.startswith('derived_data_requirements.location') and any(
                r['strength'] == 'hard' and r['metric'] == 'residential_area'
                for r in value['derived_data_requirements']):
            _clarify(questions, root, f'Please confirm your preferred residential area: {field}.')
    return dict(profile_id=value['profile_id'], version=value.get('profile_version', value.get('version')),
        intent=intent, user_context=deepcopy(value['user_context']),
        listing_constraints=deepcopy(value['listing_constraints']),
        derived_data_requirements=deepcopy(value['derived_data_requirements']),
        open_data_requirements=deepcopy(value['open_data_requirements']), unresolved=list(unresolved),
        # When there is no budget, SGD is merely the internal source amount denomination; a missing budget unit has already returned a clarification above.
        required_filters=dict(currency=currency or 'SGD', max_price=max(0, maximum) if maximum is not None else None,
            price_period=period, rental_scope=scope, locations=locations,
            min_bedrooms=minimum if any(item['field_path'] == 'bedrooms' for item in hard) else None),
        clarification_questions=questions)


if __name__ == '__main__':
    # Use exactly the same three sets of A→B business inputs as the real end-to-end retrieval, verifying only the actual parsing return.
    # Do not import fictional listings from the interface documentation, and do not construct preset outputs of the Provider or model.
    from datetime import datetime, timedelta, timezone
    import json
    from scripts.live_requirements import INPUTS

    for index, request in enumerate(INPUTS):
        ctx = dict(user_id='live-check', run_id=f'parse-{index}', conversation_id=request['conversation_id'],
            attempt_id=None, trace_id=f'parse-{index}', call_id=f'parse-{index}',
            deadline_at=(datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(), source_mode='live')
        original = deepcopy(request)
        validate_requirement_request(request, ctx)
        actual = normalize_requirements(request)
        assert request == original, "Parsing must not modify A's request"
        assert not actual['clarification_questions'], actual['clarification_questions']
        assert actual['listing_constraints'] == request['listing_constraints']
        assert actual['derived_data_requirements'] == request['derived_data_requirements']
        assert actual['open_data_requirements'] == request['open_data_requirements']
        print(json.dumps(dict(input=request, actual_output=actual), ensure_ascii=False))
    assert len(INPUTS) >= 3
    print(f'Requirement parsing actual input/output verification passed {len(INPUTS)}/{len(INPUTS)}')
