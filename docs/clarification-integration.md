# Follow-up integration

The clarification and decision packages handle questions that arise after an initial search. They interpret the user's reply and either resume the decision run or return the message to requirement collection.

## Application entry point

Use the conversation orchestrator for the complete workflow:

```python
from property_agent.orchestration import postgres_conversation_runtime

async with postgres_conversation_runtime() as orchestrator:
    result = await orchestrator.handle_message(
        "Whole-unit rental in Tampines, at most 3500 per month, with two bedrooms.",
        conversation_id="conversation-001",
        user_id="user-001",
        client_message_id="client-001",
    )
```

Configure the environment and initialize PostgreSQL first, as described in the [README](../README.md). The orchestrator handles requirement confirmation, searches, clarification requests from search, and interrupted decision runs.

## Component interfaces

- `RunContext` identifies the user, conversation, and run.
- `ConversationProfile` provides the confirmed requirement version for the run.
- `BSearchRunner` converts search fulfillment results into `AttemptOutcome` values.
- `EvaluationModule` and `SearchRunner` are protocols in [decision/boundaries.py](../property_agent/decision/boundaries.py).
- `PartCEvaluationModule` calls the functions exposed by `property_agent.evaluation.service`.

The decision graph uses evaluation's route suggestion after checking cancellation, profile versions, deadlines, and review state. Candidate counts describe the search handoff; they are not independent proof of hard-condition compliance.

## Interrupt and resume

For direct graph integrations, a result containing `__interrupt__` carries a pending question to present to the user. Resume on the same run/thread with the user's text and a stable client message ID:

```python
from langgraph.types import Command

resume = Command(resume={
    "client_message_id": "reply-001",
    "text": "Keep the original requirements.",
})
```

Pass this command to the existing graph with the same thread configuration. The server retrieves the current question and version from the checkpoint. A long-lived conversation can contain several runs, so `conversation_id` and `run_id` are not interchangeable.

## Requirement changes

Ordinary changes to housing requirements return to the requirements workflow. The decision subgraph emits `next_run_request`; the orchestrator forwards the original user text for parsing and confirmation before starting another search.

A proposal can be accepted directly only when there is one proposal, the user explicitly agrees, and the interpreted proposal ID matches. Ambiguous responses, multiple proposals, and other requirement changes return to requirement collection.

## Persistence and replay

Profiles, messages, runs, questions, and recommendations use the repositories in `property_agent/persistence/`. Nested contract data is stored in JSONB. Graph state uses PostgreSQL checkpoints.

Replaying the same client message ID with the same content is idempotent; reusing it with different content is rejected. Checkpoint and business writes are separate transactions, so business records use stable idempotency keys.

Keep the checkpoint connection open throughout graph execution and resumption. Database connections, credentials, and model clients belong in runtime dependencies, not checkpoint state.

## Model failures

Model settings come from [runtime.toml](../runtime.toml), with credentials in `.env`. Evaluation and review can fall back to deterministic checks, returning partial results with `MODEL_UNAVAILABLE`. Callers should preserve that status when presenting results.

See [Recommendation evaluation](evaluation.md) for review and routing behavior.
