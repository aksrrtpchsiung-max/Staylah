# Project Directory

This list only includes content that currently actually exists and is used for application operation, testing, or maintenance. Development-time division of labor and old numbering are only maintained in
[Module Mapping Table](MODULE_MAPPING.md). For installation and startup, see [README](README.md).

## Application Code

```text
property_agent/
├── contracts.py          # Data types, return structures, and exceptions shared by A/B/C
├── domain/               # Requirement rules and contract validation shared across modules
├── requirements/         # Understand user requirements, clarify, maintain profiles, confirm and construct search requests
├── search/               # Search and investigation
│   ├── api.py            # External search, fulfillment entry point, and real service assembly
│   ├── fulfillment.py    # Fulfillment flow from confirmed requirements to search results
│   ├── planning/         # Query generation and search planning
│   ├── execution/        # Search management, task scheduling, caching, deadlines, and quotas
│   ├── capabilities/     # Property search, address geocoding, nearby amenities, and commute investigation
│   ├── providers/        # PropertyGuru, OneMap, OpenStreetMap integrations
│   ├── aggregation/      # Property deduplication, evidence merging, and requirement coverage summarization
│   ├── graph.py          # Search LangGraph
│   └── state.py          # Search graph state
├── evaluation/           # Candidate ranking, recommendation evaluation, evidence review, and model fallback
├── decision/             # Publishing, supplementary search, repair, follow-up questions, interruption, and recovery
├── clarification/        # Follow-up questions during the recommendation stage, answer interpretation, and requirement change handoff
├── integration/          # Search attempts and result adaptation between A/B/C
├── orchestration/        # Complete sessions, message idempotency, and run lifecycle
├── persistence/          # Database models, connections, and dependency assembly
│   └── repositories/     # Profile, message, run, question, recommendation, and favorite repositories
├── runtime/              # Configuration, project paths, and shared model clients
├── profiles.py           # Profile conversion and confirmed condition adjustments
├── favorites.py          # Favorite data conversion and validation
├── results.py            # Result, Issue, and call timing utilities
└── evaluation_trace.py   # Evaluation events and per-stage timing records

web/
├── server.py             # Web service startup entry point
├── http.py               # HTTP routing and static asset responses
├── bridge.py             # Bridge between web sessions and business orchestration
├── cards.py              # Convert recommendation results to web property cards
├── public/
│   ├── index.html        # Page structure
│   ├── style.css         # Page styles
│   ├── app.js            # Event binding and page startup
│   ├── js/               # Sessions, requests, forms, cards, favorites, sidebar, and preview
│   ├── shortcuts.js      # Quick condition interactions
│   └── mrt-stations.json # Singapore MRT station quick option data
└── README.md             # Web startup and HTTP API documentation

guru_search/cli/propertyguru/
├── search.js             # PropertyGuru search page reading
├── detail.js             # PropertyGuru property detail reading
└── contract-listing.js   # Convert website data to unified property fields
```

Initial requirement clarification is in `requirements/`, and follow-up questions after recommendations are in `clarification/`. The two are at different session stages.
Formal search uses the three adapters of `guru_search`; page favorites are saved through the application's own database.

## Testing and Maintenance

```text
tests/
├── test_*.py             # Unit, contract, regression, web, and database integration tests
├── support.py            # Shared test inputs and state construction
├── refactor_scenarios.py # Scenario replay comparing behavior before refactoring
├── mock_search/          # Search doubles used only for testing
└── fixtures/             # Data actually read by tests
    ├── search/           # 6 pagination, search, timeout, and profile fixtures
    ├── decision_routes.json # 9 decision route contract test cases
    └── refactor/         # Original outputs, contract snapshots, source summaries, and real search records

scripts/
├── init_postgres.py      # Initialize business tables and LangGraph checkpoint
├── live_requirements.py  # Call B's fulfillment entry point with fixed real requirements
├── live_search.py        # Call B directly with a fixed search plan
├── smoke_live_refactor.py # Run a complete A→B→C session in an isolated test database
├── smoke_live_evaluation.py # Validate online C model with historical real properties
├── verify_checkpoint_compatibility.py # Verify cross-version interrupted state recovery
└── trace_decision.py     # View per-node state changes in the decision graph

evaluation_suite/
├── runner.py             # Run requirement clarification or full-pipeline evaluation by test case
├── data/                 # Test cases actually used by the evaluation tool
└── README.md             # Evaluation parameters, recovery methods, and output description

migrations/
├── env.py                # Alembic database migration configuration
├── script.py.mako        # Template used when creating a new migration
└── versions/             # Existing migration history for business tables, favorites, etc.
```

Database tests must use `TEST_DATABASE_URL` to specify an isolated test database. Test data is not used to generate production recommendations.

## Configuration and Documentation

| File or Directory | Actual Purpose |
| --- | --- |
| `pyproject.toml` | Python dependencies, package discovery, and web static asset bundling |
| `requirements.txt` | Installation entry point that redirects to the same project dependencies (`-e .`) |
| `runtime.toml` | Model parameters, addresses, timeouts, and search quotas |
| `.env.example` | Configuration template for local secrets and database connections |
| `compose.yaml` | Local PostgreSQL service |
| `alembic.ini` | Database migration entry point configuration |
| `.gitignore` | Exclude secrets, local environment, caches, and runtime artifacts |
| `README.md` | Installation, startup, testing, and documentation entry point |
| `project-directory.md` | Currently effective directories and their purposes |
| `module-mapping.md` | The single authoritative list mapping old modules to new implementations |
| `docs/architecture.md` | Current business processes and module boundaries |
| `docs/development.md` | Model configuration, search integration, and real diagnostic commands |
| `docs/evaluation.md` | Retrieval, evaluation, review, and routing description for C |
| `docs/clarification-integration.md` | Recommended follow-up questions and persistence handoff |
| `docs/refactoring/` | Baseline, acceptance evidence, reproduction, and rollback methods |

The local `.env` and `.venv/` store configuration and runtime environment respectively, and are not committed to the code repository. Caches automatically generated at runtime,
build metadata, and evaluation logs are not listed as project source directories. Conventional files such as each package's `__init__.py` are omitted from the display.
