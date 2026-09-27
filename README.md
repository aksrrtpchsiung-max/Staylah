# Falcon — Singapore Property Agent

Falcon is a multi-agent workflow for Singapore rental and home-buying scenarios. The system strings requirement understanding, real listing search, candidate evaluation, and follow-up decision-making into a persistable A → B → C session, and uses PostgreSQL to store profiles, messages, run states, and LangGraph checkpoints.

## Current Capabilities

- **A · Requirement Understanding**: Extract structured housing requirements from multi-turn conversations, handling conflicts, clarifications, and user confirmation.
- **B · Search and Investigation**: Call PropertyGuru, OneMap, and OpenStreetMap to complete search, detail retrieval, address geolocation, nearby amenities, and commute investigation.
- **C · Evaluation and Decision**: Perform semantic retrieval scoring, recommendation evaluation, and evidence review on the candidates returned by B, and decide whether to publish, search further, ask follow-up questions, or end; no longer re-filtering hard conditions.
- **Orchestration**: Connect A, B, and C; support B → A clarification, C → B supplementary search, and Decision interrupt/resume.
- **Persistence**: Use PostgreSQL to store business data, and use `AsyncPostgresSaver` to store A/C graph states.

```mermaid
flowchart LR
    U[User] --> A[A · Requirements]
    A -->|confirmed RequirementRequest| B[B · Live Search]
    B -->|needs clarification| A
    B -->|AttemptOutcome| C[C · Evaluate and Review]
    C -->|research directive| B
    C -->|question| U
    C -->|publish| R[Recommendation]
    A -. profile/checkpoint .-> P[(PostgreSQL)]
    C -. run/checkpoint .-> P
```

The shared business contract is defined solely in `property_agent/contracts.py`; the original root-directory compatibility entry points have been removed after the real pipeline passed.
For the sole mapping list between the old numbering and the new implementation, see [Module Mapping Table](MODULE_MAPPING.md). The official product entry point is:

```bash
.venv/bin/python -m property_agent.orchestration --conversation demo-001
```

## Code Organization

The official business implementation is concentrated in `property_agent/`: `requirements` handles requirement understanding, `search` handles search investigation,
`evaluation` handles retrieval, evaluation, and review, while `decision` and `orchestration` handle decision-making and the entire session.
Cross-module rules are in `domain`, the database is in `persistence`, and configuration and model clients are in `runtime`.
The web entry point is in `web/`, and the PropertyGuru website tool remains in `guru_search/`.

For the complete directory, see [Project Directory](PROJECT_STRUCTURE.md); for business flows, see [Architecture Documentation](docs/architecture.md).
The old numbered directories and root-directory compatibility files have been removed, and code and scripts uniformly use the new paths; the correspondence is maintained only in
[Module Mapping Table](MODULE_MAPPING.md).

## Local Startup

### 1. Python Environment

The project requires Python 3.11 or higher:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

### 2. Configure Keys

Copy the template and fill in local values:

```bash
cp .env.example .env
```

Core variables:

```dotenv
# Shared by A, B, and C: requirement understanding, search planning, semantic ranking, and review
DEEPSEEK_API_KEY=

# OneMap: choose one, or configure both
ONEMAP_TOKEN=
ONEMAP_EMAIL=
ONEMAP_PASSWORD=

# PostgreSQL
DATABASE_URL=postgresql+psycopg://property_agent:property_agent_dev@127.0.0.1:5432/property_agent
LANGGRAPH_CHECKPOINT_DB_URI=postgresql://property_agent:property_agent_dev@127.0.0.1:5432/property_agent
```

Model IDs, gateway URLs, timeouts, search page counts, and candidate quotas are uniformly placed in [`runtime.toml`](runtime.toml). Process environment variables can override the configuration file; real keys should only be stored in `.env`.

### 3. PropertyGuru / OpenCLI

Real listing search requires Node.js 20+, OpenCLI, and a connected Browser Bridge:

```bash
npm install -g @jackwener/opencli@1.8.7
mkdir -p ~/.opencli/clis/propertyguru
cp guru_search/cli/propertyguru/{search,detail,contract-listing}.js ~/.opencli/clis/propertyguru/
opencli doctor
```

`opencli doctor` should show that both the daemon and the browser extension are connected. The adapter uses a persistent PropertyGuru session, and B will read search pages and listing details in sequence; only after detail retrieval succeeds will the listing status be recorded as active.

### 4. PostgreSQL

Start the project's bundled PostgreSQL and initialize the business tables and checkpoint tables:

```bash
docker compose up -d --wait
.venv/bin/python scripts/init_postgres.py
```

If the local `5432` port is already occupied by an existing PostgreSQL container, reuse that database and modify the two database URIs in `.env`; do not start a second container occupying the same port.

### 5. Run the Full CLI

```bash
.venv/bin/python -m property_agent.orchestration --conversation demo-001
```

Each `conversation` has an independent checkpoint. When you need to retest from a blank session, use a different ID, for example `demo-002`. Enter `/quit` to exit.

The web page connects to the same complete pipeline:

```bash
.venv/bin/python -m web.server --live
```

Omitting `--live` previews fixed examples; see [Web Documentation](web/README.md) for details.

## Model Degradation Behavior

- A depends on DeepSeek; without a key, real requirement parsing cannot be completed.
- When B's model planning or supervision call fails, it will continue execution using deterministic scheduling within the quota and deadline, and retain the issue.
- C.retrieve prioritizes using DeepSeek to complete requirement satisfaction scoring; when the model is unavailable, output is truncated, or scoring is incomplete, it uses local structured constraint scoring to continue the flow, and discloses the degradation as `partial + MODEL_UNAVAILABLE`. evaluate/review can also degrade according to their respective deterministic boundaries.
- Listing facts come only from the Provider and corresponding evidence; the model cannot create listings, modify hard conditions, or allow facts without evidence.

## Testing

The complete offline test suite does not require external keys:

```bash
.venv/bin/python -m unittest discover -s tests
```

PostgreSQL integration tests (configure an isolated test database first; using the daily database is prohibited):

```bash
TEST_DATABASE_URL=postgresql+psycopg://refactor@127.0.0.1:55438/postgres \
  .venv/bin/python -m unittest tests.test_postgres_integration
```

Common single-module checks:

```bash
.venv/bin/python -m unittest tests.test_a_b_integration
.venv/bin/python -m unittest tests.test_search_integration
.venv/bin/python -m unittest tests.test_part_c_integration
.venv/bin/python -m unittest tests.test_orchestration
```

Real-source checks will access DeepSeek, PropertyGuru, OneMap, or OpenStreetMap, and cannot be used to replace offline regression tests; for specific commands, see [Development and Diagnostics](docs/development.md).

## Documentation

- [Module Mapping Table](MODULE_MAPPING.md): the sole list of original divisions of work, numbering, and new code locations.
- [Directory and Architecture](docs/architecture.md): current business boundaries and flows.
- [Development and Diagnostics](docs/development.md): model configuration, B integration, and real invocation commands.
- [C Function Documentation](docs/evaluation.md) and [Clarification Integration](docs/clarification-integration.md).
- [Web HTTP API](web/README.md) and [Evaluation Tool](evaluation_suite/README.md).
- [Acceptance and Rollback](docs/refactoring/acceptance.md).
