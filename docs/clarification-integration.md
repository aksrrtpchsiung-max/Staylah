# Follow-up Subgraph Integration Interface

This module does not implement onboarding, search, evaluate, or review. It consumes the structured
results provided by other modules, and is only responsible for generating follow-up questions, waiting for the user, understanding the answer, and handing control back to the run control layer.

The product run control layer is `property_agent.orchestration.ConversationOrchestrator`.
It is responsible for: user messages entering A, calling B after confirmation, `needs_clarification` returning to A,
resuming C's interrupt, and `next_run_request` starting a new round of search.

## Upstream Inputs

Callers follow the frozen contract:

- `RunContext`: must provide stable `user_id`, `run_id`, `conversation_id`.
- `ConversationProfile`: the frozen requirement version for this run, must be `status="confirmed"`.
- `AttemptOutcome`: converted by `BSearchRunner.run_initial` / `run_attempt` from B's fulfillment results.
- `EvaluationModule`, `SearchRunner`: implement
  the Protocol in `property_agent.decision.boundaries`. The default evaluation module is
  `PartCEvaluationModule`, which directly calls `part_c.evaluate` / `part_c.review`;
  the default search is `BSearchRunner`.

The decision graph and module C use the same `ConversationProfile`. Legacy fixtures are
converted into confirmed profiles when loaded by `tests.support.load_profile()`, and no longer go through a separate adapter.

Business routing follows C's `part_c.decide_next` as much as possible. `prepare_decision` writes
`assessment.next_action` into `evaluation_next_action`, then hands it to C for execution.
Safety gates such as cancellation, profile expiration, source failure, deadlines, and review/repair are still handled by C first;
the preparation stage before any search has been performed is handled locally by A, because C assumes a round has already been evaluated.

The `ask_user` copy is differentiated by the number of qualifying units in this round: when there are no candidates, it states that there are no listings meeting the hard constraints;
when there are already several units, it states that the number is somewhat low, rather than that there are none.

Do not write database connections, DeepSeek keys, or model objects into `RunContext`/`DState`.
For model stack configuration, see `runtime.toml` in the repository root.

If upstream needs to read or write chat history, use `property_agent.persistence.boundaries.ChatRepository`
(implemented as `SqlChatRepository`). The follow-up subgraph will not call `onboard` / `prepare_query` /
`build_search_plan`.

## Assembly and Running

Recommended entry point:

```python
from property_agent.orchestration import postgres_conversation_runtime

async with postgres_conversation_runtime() as orchestrator:
    result = await orchestrator.handle_message(
        "Whole-unit rental, monthly rent at most 3500, Tampines, at least two bedrooms.",
        conversation_id="conversation-001",
        user_id="user-001",
        client_message_id="client-001",
    )
```

When assembling only the decision graph:

```python
engine = build_engine()
sessions = build_session_factory(engine)
deps = build_postgres_deps(sessions=sessions)

SqlRunRepository(sessions).prepare_run(ctx=ctx, profile=profile)
config = thread_config(ctx["run_id"])

async with postgres_decision_graph(deps) as graph:
    result = await graph.ainvoke(
        initial_state(ctx=ctx, profile=profile, outcome=outcome),
        config,
    )
```

`build_postgres_deps` by default connects to `part_c.evaluate` and `part_c.review` in the repository root,
as well as `BSearchRunner`. When real model review is needed, set in `.env`
`DEEPSEEK_API_KEY`; the API URL and model ID follow `[deepseek]` in `runtime.toml`.
When there is no DeepSeek Key, C uses the deterministic evaluate/review fallback by default, and retains it with `partial` status
with the `MODEL_UNAVAILABLE` explanation; tests can still override the default module via `module_c=`.

## Persistence Fields

PostgreSQL uses `conversation_profiles` to store profiles. The top-level fields of `ConversationProfile`
are placed in columns with the same names, including `conversation_id`, `confirmed_version`, `status`,
`listing_constraints`, the three requirement lists, and the confirmation time; the database no longer stores the legacy
`user_profiles.body`. Other business tables also use contract field names: `conversations.conversation_id`,
`messages.message_id/text`, `agent_runs.run_id`, `run_questions.question`, and
`recommendations.recommendation`. Nested structures continue to use JSONB.

If the result contains `__interrupt__`, send the `pending_question` within it to the user. To resume, only submit
natural language and the client idempotency ID; the server will obtain the current question and version from the checkpoint:

```python
Command(resume={
    "client_message_id": "stable-client-message-id",
    "text": "Do not accept adjustments, keep the original conditions",
})
```

Resumption must use the same `run_id`/thread. Long-term chat still uses a separate `conversation_id`.

## onboarding Handoff

Ordinary additions or modifications to requirements will not be parsed as hard constraints in the decision subgraph. The subgraph returns:

```python
next_run_request = {
    "reason_code": "user_message",
    "profile_id": "...",
    "profile_version": 1,
    "superseded_run_id": "...",
    "accepted_proposal_id": None,
    "source_message_id": "...",
}
```

The run control layer (`ConversationOrchestrator`) calls the A graph again with the same original user text.
After the user confirms, a new B→C run will be started. If a different envelope is needed, `OnboardingHandoff` can be replaced,
without modifying the decision nodes.

## Safety and Recovery Constraints

- DeepSeek only polishes `PendingQuestion.text`; other fields are generated by the program and remain unchanged.
- Auto-accept only when there is a single proposal, the user explicitly agrees, and the model returns the current proposal ID.
- Multi-proposal "accept all", ambiguous expressions, and requirement modifications are all handed off to onboarding.
- The same `client_message_id` and the same content can be safely replayed; the same ID with different content will be rejected.
- The checkpoint and business writes are not in the same transaction. Questions, answers, profile mutations, and recommendations all have stable
  idempotency keys, and node replay cannot produce duplicate business records.
- The `AsyncPostgresSaver` connection must cover the full lifecycle of the graph call, and cannot be taken from a context manager
  After returning the graph, close the connection before continuing to use it.
