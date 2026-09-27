# StayLah Web

StayLah Web is the same-origin web entry point for the A/B/C listing workflow. The static frontend and JSON API are provided by
`web.server`; the code does not depend on a personal directory; all commands are run from the repository root.

## Environment Setup

1. Create a Python environment and install the project dependencies:

   ```bash
   python3.11 -m venv .venv
   .venv/bin/python -m pip install -e .
   ```

2. Create the local configuration from the template:

   ```bash
   cp .env.example .env
   ```

3. Configure DeepSeek, OneMap, PostgreSQL, and
   PropertyGuru OpenCLI as described in the root [README](../README.md). Keys, database files, and the OpenCLI installation directory all remain local and are not committed to Git.

## Startup Methods

Preview the page and sample conversation only:

```bash
.venv/bin/python -m web.server
```

Connect to the real A/B/C orchestration:

```bash
docker compose up -d --wait
.venv/bin/python scripts/init_postgres.py
.venv/bin/python -m web.server --live
```

After the service starts, it prints the listening address for this run in the terminal. The default binding is for local development only; the port and bind address can be configured via
CLI arguments or environment variables:

```bash
.venv/bin/python -m web.server --live --host <bind-address> --port <port>

# Equivalent environment variables
STAYLAH_HOST=<bind-address> STAYLAH_PORT=<port> .venv/bin/python -m web.server --live
```

When the team is doing joint debugging on the same network, you can bind to `0.0.0.0` and then access it via the development machine's LAN IP and the chosen port.
Do not expose the built-in `ThreadingHTTPServer` directly to the public internet. Production deployment should be placed behind an HTTPS reverse proxy,
and should add identity authentication, persistent sessions, access logs, and process management.

## PropertyGuru Adapter

Each developer needs to install OpenCLI on their own machine and copy the adapter from the current branch to the OpenCLI user directory:

```bash
npm install -g @jackwener/opencli@1.8.7
mkdir -p ~/.opencli/clis/propertyguru
cp guru_search/cli/propertyguru/{search,detail,contract-listing}.js ~/.opencli/clis/propertyguru/
opencli doctor
```

`opencli doctor` should confirm that both the daemon and Browser Bridge are connected. After the branch is updated, if the PropertyGuru parameters have
changed, the copy command should be run again to avoid inconsistency between the local old adapter and the backend calls.

## Interaction Behavior

- User bubbles adapt to content width and are constrained by the page's maximum width.
- Additional requirements support filling in one to three items at a time and then submitting them together; selecting a room type does not send immediately.
- Budget uses minimum and maximum input fields.
- Condition tooltips are expanded and closed by clicking, without relying on mouse hover.
- An in-progress search can be actively stopped by the user.
- The thinking state rotates through prompts related to living, family, and community in Singapore.
- Listing cards use the `media.search_card_photos` Evidence returned by B to display the cover image; if loading fails, the original placeholder image is kept.
- The card heart button saves/unsaves per conversation; the sidebar `SAVED HOMES` shows the current session's saved items, and the saved state is restored when a historical conversation is reopened.
- A solid heart on the right side of a historical conversation entry indicates that the conversation contains saved listings.
- On desktop, the right edge of the sidebar can be dragged to adjust its width; the range is 260-520px, and double-clicking restores the default width.
- User-visible text in the frontend and backend is unified in English.

## API and Security Boundaries

- `POST /api/session` creates an in-process web session.
- `POST /api/turn` submits a message using `X-Session-ID`.
- `POST /api/cancel` stops the active search in the current web session.
- `POST /api/favorites/add` saves a listing already returned in the current session.
- `POST /api/favorites/remove` removes a saved listing.
- `POST /api/favorites/list` queries the current session's saved listings.
- The frontend and API are same-origin; the server accepts HTTP or HTTPS Origin from the same Host.
- The UI uses `textContent` to render model and listing text, and source links only allow HTTP(S).
- The browser only submits listing identifiers already returned in the current session, and the server restores card data from a trusted cache.

The browser session token is still kept within the service process; conversations, messages, favorites, and LangGraph state are stored in
PostgreSQL. Production authentication and web sessions shared across multiple instances still need to be implemented before formal deployment.

## File Structure

- `public/index.html`: page structure.
- `public/style.css`: responsive visual styles.
- `public/js/`: scripts for sessions, requirement forms, search status, cards, favorites, and the sidebar.
- `public/app.js`: event binding and startup; all scripts are loaded in the defer order in index.html.
- `public/shortcuts.js`: commute, MRT, and school condition input.
- `public/mrt-stations.json`: static snapshot of MRT stations with source dates.
- `server.py`: original startup entry point; HTTP routing is in `http.py`, session bridging is in `bridge.py`, and card conversion is in `cards.py`.
- `../tests/test_web.py`: tests for sessions, confirmation, cancellation, selection validation, and idempotent behavior.

## Verification

```bash
for script in web/public/*.js web/public/js/*.js; do node --check "$script"; done
.venv/bin/python -m unittest tests.test_web tests.test_web_assets tests.test_orchestration -q
```

Real-source checks access DeepSeek, PropertyGuru, and OneMap, and should be run separately from offline regression tests.

For the original module correspondence, see [Module Correspondence Table](../MODULE_MAPPING.md); for screenshot comparison and restoration checks, see [Refactoring Acceptance](../docs/refactoring/acceptance.md).
