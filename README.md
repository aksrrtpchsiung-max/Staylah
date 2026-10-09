# StayLah

A conversational assistant for finding homes in Singapore. Describe what you need, confirm your requirements, and get property recommendations with listing details, nearby amenities, and commute information.

StayLah uses DeepSeek and LangGraph to coordinate requirement collection, property search, and recommendation review. Listings come from PropertyGuru; location and travel data come from OneMap and OpenStreetMap. The backend workflow is called Falcon in the code.

## What it does

- Collects budget, location, housing preferences, and other requirements through conversation.
- Searches listings and checks details against the confirmed requirements.
- Compares candidates, asks follow-up questions, and searches again when more information is needed.
- Saves conversations, requirements, recommendations, and favorites in PostgreSQL.

This is a hackathon project. Live searches depend on external services and a connected browser session. The included web server is intended for local development; production authentication and deployment are not included.

## Quick start

Requires Python 3.11 or later. Run these commands from the repository root:

```bash
git clone https://github.com/aksrrtpchsiung-max/Staylah.git
cd Staylah
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m web.server
```

Open [localhost:8080](http://localhost:8080). This starts the UI with sample conversations and listings. It does not run live searches.

## Run with live data

In addition to Python, you need Docker Compose, Node.js 20+, a DeepSeek API key, OneMap credentials, and OpenCLI with its Browser Bridge connected.

### Configure the environment

```bash
cp .env.example .env
```

Edit `.env` with your own values:

| Variable | Purpose |
| --- | --- |
| `DEEPSEEK_API_KEY` | Requirement parsing, search planning, and recommendation review |
| `ONEMAP_TOKEN` | OneMap access token |
| `ONEMAP_EMAIL`, `ONEMAP_PASSWORD` | OneMap login credentials, as an alternative to a token |
| `DATABASE_URL` | PostgreSQL connection for application data |
| `LANGGRAPH_CHECKPOINT_DB_URI` | PostgreSQL connection for workflow checkpoints |

The database URLs in the template match the bundled local Docker configuration. Keep credentials in your local `.env`. Model settings, timeouts, and search limits are in [runtime.toml](runtime.toml).

### Set up the PropertyGuru adapter

```bash
npm install -g @jackwener/opencli@1.8.7
mkdir -p ~/.opencli/clis/propertyguru
cp guru_search/cli/propertyguru/{search,detail,contract-listing}.js ~/.opencli/clis/propertyguru/
opencli doctor
```

Connect the OpenCLI Browser Bridge extension and keep a PropertyGuru browser session available. Before continuing, check that `opencli doctor` reports a connected daemon and browser extension.

### Start the database and app

```bash
docker compose up -d --wait
.venv/bin/python scripts/init_postgres.py
.venv/bin/python -m web.server --live
```

Open [localhost:8080](http://localhost:8080). If you use an existing PostgreSQL instance, update both database URLs in `.env` instead of starting the bundled container.

To use the same workflow from the terminal:

```bash
.venv/bin/python -m property_agent.orchestration --conversation demo-001
```

Use a new conversation ID to start a separate session. Enter `/quit` to exit.

## Project structure

| Directory | Contents |
| --- | --- |
| `property_agent/requirements/` | Conversation parsing and requirement confirmation |
| `property_agent/search/` | Listing search, location lookup, amenities, and travel checks |
| `property_agent/evaluation/` | Candidate scoring and evidence review |
| `property_agent/decision/` | Follow-up questions, additional searches, and recommendation decisions |
| `property_agent/orchestration/` | Conversation workflow connecting the modules |
| `property_agent/persistence/` | Database models and repositories |
| `web/` | StayLah frontend and HTTP server |
| `guru_search/` | PropertyGuru adapters for OpenCLI |
| `evaluation_suite/` | Evaluation datasets and runner |
| `tests/` | Automated tests and fixtures |

Shared data types are in [property_agent/contracts.py](property_agent/contracts.py).

## Tests

Run the offline suite:

```bash
.venv/bin/python -m unittest discover -s tests
```

Database integration tests require a separate test database configured through `TEST_DATABASE_URL`. Live checks also require the external services above. See [Development and diagnostics](docs/development.md) for the available commands.

## Documentation

- [Architecture](docs/architecture.md)
- [Web interface and API](web/README.md)
- [Development and diagnostics](docs/development.md)
- [Recommendation evaluation](docs/evaluation.md)
- [Evaluation runner](evaluation_suite/README.md)
