"""Derive runtime validation from the shared TypedDict to avoid maintaining a second set of field definitions."""
import math
import types
import typing
from datetime import date, datetime
from functools import lru_cache

from property_agent import contracts as contracts
from property_agent.contracts import ContractViolation, Listing, RunContext, SearchPlan


def fail(path: str, message: str) -> typing.NoReturn:
    raise ContractViolation("INVALID_INPUT", path, message)


@lru_cache(maxsize=None)
def _hints(schema):
    return typing.get_type_hints(schema)


def validate_type(schema, value, path="value") -> None:
    """Strictly check the field set, types, and JSON numeric values; bool is not treated as int."""
    if isinstance(schema, str):
        schema = eval(schema, vars(contracts))
    if isinstance(schema, typing.ForwardRef):
        schema = eval(schema.__forward_arg__, vars(contracts))
    origin, args = typing.get_origin(schema), typing.get_args(schema)
    if origin in (typing.Union, types.UnionType):
        for option in args:
            try:
                validate_type(option, value, path)
                return
            except ContractViolation:
                pass
        fail(path, "value does not match the agreed type or enum")
    elif origin is typing.Literal:
        if not any(type(value) is type(v) and value == v for v in args):
            fail(path, "unknown enum value")
    elif typing.is_typeddict(schema):
        fields = _hints(schema)
        if type(value) is not dict or set(value) != set(fields):
            fail(path, "field set is inconsistent with the contract")
        for key, child in fields.items():
            validate_type(child, value[key], f"{path}.{key}")
    elif origin is list:
        if type(value) is not list:
            fail(path, "must be an array")
        for index, child in enumerate(value):
            validate_type(args[0], child, f"{path}[{index}]")
    elif origin is dict:
        if type(value) is not dict:
            fail(path, "must be an object")
        for key, child in value.items():
            validate_type(args[0], key, f"{path}.key")
            validate_type(args[1], child, f"{path}.{key}")
    elif schema is float:
        if type(value) not in (int, float) or not math.isfinite(value):
            fail(path, "must be a finite numeric value")
    elif type(value) is not schema:
        fail(path, f"must be {schema.__name__}")


def timestamp(value: str, path: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed
    except (ValueError, TypeError):
        fail(path, "must be an ISO datetime with a time zone")


def validate_context(ctx: RunContext) -> None:
    validate_type(RunContext, ctx, "ctx")
    for key in ("user_id", "run_id", "conversation_id", "trace_id", "call_id"):
        if not ctx[key].strip():
            fail(f"ctx.{key}", "must not be empty")
    timestamp(ctx["deadline_at"], "ctx.deadline_at")
    if ctx['attempt_id'] is not None and not ctx['attempt_id'].strip():
        fail('ctx.attempt_id', 'must not be an empty string')


def validate_planner_input(profile, query, previous_attempts, directive, ctx) -> None:
    """The shared internal contract accepts a real ConversationProfile and preserves confirmation and session validation."""
    from property_agent.domain.requirements import normalize_requirements, validate_conversation_profile
    validate_conversation_profile(profile, ctx)
    validate_planning_input(normalize_requirements(profile), query, previous_attempts, directive, ctx)


def planned_attempt_id(requirements, ctx) -> str:
    """A does not need to provide attempt_id; B binds the plan ID for this round without modifying ctx."""
    from property_agent.search.execution.history import fingerprint
    return ctx['attempt_id'] or 'attempt-' + fingerprint([
        ctx['conversation_id'], ctx['run_id'], ctx['call_id'], requirements['version']])


def validate_planning_input(requirements, query, previous_attempts, directive, ctx) -> None:
    """Validate B's explicit projection; the outer layer enters plan generation only after core clarification has been resolved."""
    validate_context(ctx)
    for schema, value, path in (
        (contracts.QueryFeatures, query, 'query'),
        (list[contracts.AttemptSummary], previous_attempts, 'previous_attempts'),
        (contracts.SearchDirective | None, directive, 'directive'),
    ):
        validate_type(schema, value, path)
    if query['profile_version'] != requirements['version']:
        raise ContractViolation('STATE_CONFLICT', 'query.profile_version', 'query and requirement versions are inconsistent')
    if directive is not None and directive['base_profile_version'] != requirements['version']:
        raise ContractViolation('STATE_CONFLICT', 'directive.base_profile_version', 'supplementary search directive and requirement versions are inconsistent')
    if query['unresolved'] or requirements['clarification_questions']:
        fail('query.unresolved', 'core search conditions still require clarification; please return to A via RequirementFulfillment')
    if not query['semantic_query'].strip():
        fail('query.semantic_query', 'must not be empty')
    filters = requirements['required_filters']
    validate_type(contracts.HardConstraints, filters, 'required_filters')
    if not filters['currency'].strip():
        fail('required_filters.currency', 'must not be empty')
    for key in ('max_price', 'min_bedrooms'):
        if filters[key] is not None and filters[key] < 0:
            fail('required_filters.' + key, 'must not be negative')
    if any(not loc.strip() for loc in filters['locations']) or len(set(filters['locations'])) != len(filters['locations']):
        fail('required_filters.locations', 'location IDs must not be empty or duplicated')
    attempts = set()
    current_attempt = planned_attempt_id(requirements, ctx)
    for index, attempt in enumerate(previous_attempts):
        path = f'previous_attempts[{index}]'
        if not attempt['attempt_id'].strip() or attempt['attempt_id'] in attempts:
            fail(path + '.attempt_id', 'historical round IDs must not be empty or duplicated')
        if attempt['attempt_id'] == current_attempt:
            raise ContractViolation('STATE_CONFLICT', 'ctx.attempt_id', 'this round must use a new attempt_id or call_id')
        attempts.add(attempt['attempt_id'])
        if attempt['eligible_count'] < 0 or any(not fp.strip() for fp in attempt['query_fingerprints']):
            fail(path, 'historical counts must not be negative, and the query fingerprint must not be empty')
    if directive is not None and not directive['strategy_changes']:
        fail('directive.strategy_changes', 'supplementary search requires at least one explicit strategy')


def validate_plan_result(result, profile, ctx) -> None:
    """Full result validation for 1 to 2, and ensure the model cannot change A's hard conditions."""
    from property_agent.domain.requirements import normalize_requirements
    requirements = profile if 'required_filters' in profile else normalize_requirements(profile)
    try:
        validate_result_envelope(result, contracts.SearchPlan, ctx)
        plan = result['data']
        if plan is None:
            return
        validate_plan(plan, ctx)
        if plan['profile_version'] != requirements['version']:
            fail('result.data.profile_version', 'plan and profile versions are inconsistent')
        if plan['required_filters'] != requirements['required_filters'] or plan['intent'] != requirements['intent']:
            raise ContractViolation('CONSTRAINT_CHANGE_NOT_ALLOWED', 'result.data.required_filters', "the plan cannot change A's hard conditions or transaction intent")
        if not plan['queries'] or not plan['reason'].strip():
            fail('result.data', 'the plan must include an executable query and a reason')
    except ContractViolation as exc:
        if exc.code == 'CONSTRAINT_CHANGE_NOT_ALLOWED':
            raise
        raise ContractViolation('INVALID_OUTPUT', exc.field_path, str(exc)) from exc


def validate_result_envelope(result, data_schema, ctx) -> None:
    if type(result) is not dict or set(result) != {'status', 'data', 'issues', 'meta'}:
        fail('result', 'field set is inconsistent with the Result contract')
    validate_type(typing.Literal['success', 'partial', 'error'], result['status'], 'result.status')
    validate_type(data_schema | None, result['data'], 'result.data')
    validate_type(list[contracts.Issue], result['issues'], 'result.issues')
    validate_type(contracts.ResultMeta, result['meta'], 'result.meta')
    if any(result['meta'][k] != ctx[k] for k in ('trace_id', 'call_id')):
        fail('result.meta', 'tracking identifiers are inconsistent with this call')
    if result['meta']['duration_ms'] < 0:
        fail('result.meta.duration_ms', 'must not be negative')
    if (result['status'] == 'error') != (result['data'] is None):
        fail('result.data', 'error carries no data; success/partial must carry data')
    if (result['status'] == 'success') != (not result['issues']):
        fail('result.issues', 'success carries no issues; partial/error must explain the reason')


def validate_plan(plan: SearchPlan, ctx: RunContext) -> None:
    validate_context(ctx)
    validate_type(SearchPlan, plan, "plan")
    for key in ("plan_id", "attempt_id"):
        if not plan[key].strip():
            fail(f"plan.{key}", "must not be empty")
    if plan["source_mode"] != ctx["source_mode"] or (
            ctx["attempt_id"] is not None and plan["attempt_id"] != ctx["attempt_id"]):
        fail("plan", "the plan is inconsistent with the context's source_mode / attempt_id")
    if plan["profile_version"] < 0:
        fail("plan.profile_version", "must not be negative")
    for key in ("page_limit", "candidate_limit"):
        if plan[key] <= 0:
            fail(f"plan.{key}", "must be greater than zero")
    filters = plan["required_filters"]
    if not filters["currency"].strip():
        fail("plan.required_filters.currency", "must not be empty")
    for key in ("max_price", "min_bedrooms"):
        if filters[key] is not None and filters[key] < 0:
            fail(f"plan.required_filters.{key}", "must not be negative")
    ids = set()
    for query in plan["queries"]:
        if not all(query[key].strip() for key in ("query_id", "source", "text")):
            fail("plan.queries", "query ID, source, and text must not be empty")
        if query["query_id"] in ids:
            fail("plan.queries", "query_id must be unique")
        ids.add(query["query_id"])


def validate_listing(item: Listing) -> None:
    validate_type(Listing, item, "listing")
    for key in ("listing_key", "source"):
        if not item[key].strip():
            fail(f"listing.{key}", "must not be empty")
    for path, number in [("price.amount", item["price"]["amount"]),
                         ("bedrooms", item["bedrooms"]),
                         *[(f"attributes.{key}", item["attributes"][key]) for key in
                           ("area_sqft", "bathrooms", "lease_years")]]:
        if number is not None and number < 0:
            fail(f"listing.{path}", "must not be negative")
    price = item["price"]
    if not price["currency"].strip():
        fail("listing.price.currency", "must not be empty")
    if (price["status"] == "known") != (price["amount"] is not None):
        fail("listing.price", "known must have an amount; unknown/conflict must not retain a definite amount")
    ids = set()
    for evidence in item["evidence"]:
        if not evidence["evidence_id"] or evidence["evidence_id"] in ids:
            fail("listing.evidence", "evidence IDs must not be empty or duplicated")
        ids.add(evidence["evidence_id"])
        timestamp(evidence["observed_at"], "listing.evidence.observed_at")
    if not set(price["evidence_ids"]) <= ids:
        fail("listing.price.evidence_ids", "references nonexistent evidence")
    if price["status"] == "known" and not any(
        e["evidence_id"] in price["evidence_ids"] and e["field"] == "price.amount"
        and e["value"] == price["amount"] for e in item["evidence"]
    ):
        fail("listing.price.evidence_ids", "a known price must have amount evidence")
    for key in ("fetched_at", "source_updated_at", "last_verified_at"):
        if item[key] is not None:
            timestamp(item[key], f"listing.{key}")
    if item["listed_date"] is not None:
        try:
            date.fromisoformat(item["listed_date"])
        except ValueError:
            fail("listing.listed_date", "must be an ISO date")


def validate_search_result(result, plan: SearchPlan, ctx: RunContext) -> None:
    """Check the full return value given to C; output issues uniformly use INVALID_OUTPUT."""
    try:
        validate_result_envelope(result, contracts.SearchResult, ctx)
        data = result['data']
        if data is None:
            return
        if any(data[k] != plan[k] for k in ('plan_id', 'profile_version')):
            fail('result.data', 'the output plan or profile versions are inconsistent')
        if len(data['items']) > plan['candidate_limit']:
            fail('result.data.items', 'exceeds the candidate limit for this round')
        keys = set()
        for index, listing in enumerate(data['items']):
            validate_listing(listing)
            if listing['source_mode'] != ctx['source_mode'] or listing['listing_key'] in keys:
                fail(f'result.data.items[{index}]', 'listing mode is inconsistent or listing_key is duplicated')
            keys.add(listing['listing_key'])
        coverage = data['coverage']
        if not set(coverage['failed_sources']) <= set(coverage['queried_sources']):
            fail('result.data.coverage.failed_sources', 'failed sources must be sources that were actually queried')
        if set(coverage['applied_filters']) & set(coverage['unsupported_filters']):
            fail('result.data.coverage', 'the same condition cannot be declared both applied and unsupported')
        if coverage['has_more'] != bool(coverage['next_pages']):
            fail('result.data.coverage.has_more', 'must be consistent with a reliable continuation cursor')
        query_ids, next_ids = {q['query_id'] for q in plan['queries']}, set()
        for page in coverage['next_pages']:
            if page['query_id'] not in query_ids or page['query_id'] in next_ids or not page['cursor'].strip():
                fail('result.data.coverage.next_pages', 'continuation pages must correspond to a unique in-plan query and a non-empty cursor')
            next_ids.add(page['query_id'])
        if coverage['queries_completed'] and coverage['has_more']:
            fail('result.data.coverage', 'cannot still have unexecuted continuation pages after completion')
        if result['status'] == 'success' and (
                not coverage['queries_completed'] or coverage['truncated'] or coverage['failed_sources']):
            fail('result.status', 'Cannot return success when the search is incomplete, truncated, or the source fails')
    except ContractViolation as exc:
        raise ContractViolation('INVALID_OUTPUT', exc.field_path, str(exc)) from exc
