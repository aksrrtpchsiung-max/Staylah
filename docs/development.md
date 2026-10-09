# Development and diagnostics

Start with the [installation instructions](../README.md). Run the commands below from the repository root using the project's virtual environment.

## Configuration

Copy `.env.example` to `.env` for the main application and live checks. Set `DEEPSEEK_API_KEY`, OneMap credentials, and the two PostgreSQL connection URLs as described in the README.

[runtime.toml](../runtime.toml) contains model IDs, API URLs, timeouts, search quotas, and the run deadline. Prefer changing these settings in the file; environment overrides are defined in [runtime/settings.py](../property_agent/runtime/settings.py). Keep credentials out of source code and test fixtures.

## Offline tests

```bash
.venv/bin/python -m unittest discover -s tests
```

To focus on a particular area:

```bash
.venv/bin/python -m unittest tests.test_requirement_understanding
.venv/bin/python -m unittest tests.test_search_integration
.venv/bin/python -m unittest tests.test_part_c_integration
.venv/bin/python -m unittest tests.test_orchestration
.venv/bin/python -m unittest tests.test_web tests.test_web_assets
```

PostgreSQL integration tests require `TEST_DATABASE_URL` pointing to a separate test database. Do not point them at a database containing conversations you want to keep.

```bash
.venv/bin/python -m unittest tests.test_postgres_integration tests.test_postgres_favorites
```

## Requirements debugger

The requirements CLI runs only the requirements graph, with in-memory checkpoints and profiles:

```bash
.venv/bin/python -m property_agent.requirements.cli --thread demo-001 --tone warm --trace
```

This debugger loads `.env.local` if present, without overriding existing process variables. Set `DEEPSEEK_API_KEY` there or in the environment. Its state is lost when the process exits.

| Command | Purpose |
| --- | --- |
| `/state` | Inspect the checkpoint state |
| `/profile` | Inspect the current profile |
| `/patch` | Inspect proposed and validated changes |
| `/answer` | Inspect the last housing question answer |
| `/request` | Inspect the confirmed search request |
| `/trace on` | Show graph traversal |
| `/tone concise` | Change the reply style; `warm` and `direct` are also supported |
| `/reset` | Clear this debugger's state |
| `/quit` | Exit |

Use `python -m property_agent.orchestration --conversation demo-001` through the virtual environment for a persistent, full conversation instead.

## Live checks

These commands call external services and may consume API credits. Configure the relevant credentials first. PropertyGuru checks also need OpenCLI and a connected Browser Bridge; see [the setup instructions](../README.md#set-up-the-propertyguru-adapter).

| Command, after `.venv/bin/python` | Checks |
| --- | --- |
| `-m property_agent.runtime.model_client` | Shared model client with real prompts |
| `-m property_agent.search.capabilities.listings --output /tmp/listings-live.json` | PropertyGuru search and detail retrieval |
| `-m property_agent.search.api` | Requirement fulfillment through the search API |
| `-m scripts.live_search` | Search using the example plans in the script |
| `-m scripts.live_requirements` | Search using the example requirement requests in the script |
| `-m scripts.smoke_live_evaluation` | Live model evaluation of recorded listings; no new listing search |

Read a script's inputs before running it. These checks exercise specific scenarios; a successful run is not a measurement of overall recommendation quality. Use the [evaluation runner](../evaluation_suite/README.md) for recorded cases and manual assessment.

For map investigations, provide JSON inputs matching each command's schema:

```bash
.venv/bin/python -m property_agent.search.capabilities.location --help
.venv/bin/python -m property_agent.search.capabilities.amenities --help
.venv/bin/python -m property_agent.search.capabilities.travel --help
```

## Debugging a search

Start with the returned `status`, `issues`, requirement coverage, and listing `field_issues`. A `partial` result can contain useful candidates while still reporting a timeout, missing evidence, or an exhausted quota.

- **No PropertyGuru results:** run `opencli doctor`, check the browser connection, and confirm the three adapter files were copied from the current checkout.
- **OneMap authentication failures:** check the token or configured email and password.
- **Unexpectedly short searches:** inspect page and candidate limits, the run deadline, and source failures before increasing budgets.
- **Missing commute or amenity evidence:** inspect the address precision, destination, and supported requirement parameters. Missing evidence is not a failed condition or a satisfied one.
- **Model fallback:** `MODEL_UNAVAILABLE` indicates that a model step could not complete. Evaluation can continue with local rules, but the result remains partial.
- **Database startup failures:** check both database URLs and whether another service is using port 5432.

Source adapters live in `property_agent/search/providers/`; planning, execution, and aggregation are separate packages under `property_agent/search/`. The shared model client is in `property_agent/runtime/model_client.py`.

## Working with traces

Live outputs may contain user requests, property descriptions, and other personal data. Review logs before sharing them. Keep generated evaluation runs outside version control; the default `evaluation_runs/` directory is Git-ignored.

Historical refactoring checks are documented in [refactoring/](refactoring/README.md). They describe a particular migration and are not prerequisites for running the app.
