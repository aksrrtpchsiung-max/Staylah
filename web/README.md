# StayLah web interface

The frontend and JSON API are served together by `web.server`. The browser interface supports requirement collection, property search, conversation history, and saved listings.

## Run locally

Install the project as described in the [README](../README.md), then start a preview:

```bash
.venv/bin/python -m web.server
```

Open [localhost:8080](http://localhost:8080). Preview mode uses sample conversations and listings.

For live searches, configure DeepSeek, OneMap, and the PropertyGuru browser adapter, then start PostgreSQL and the app:

```bash
docker compose up -d --wait
.venv/bin/python scripts/init_postgres.py
.venv/bin/python -m web.server --live
```

The default address is `127.0.0.1:8080`. Change it with `--host` and `--port`, or the `STAYLAH_HOST` and `STAYLAH_PORT` environment variables. The server prints its listening address and mode at startup.

## Using the interface

Describe the home you want, answer any clarification questions, and confirm the requirements before searching. You can cancel an active search, open listing source pages, and save or remove listings using the heart button.

Favorites belong to a conversation. Reopening a stored conversation restores its saved listings. Listing images come from source evidence; when an image cannot load, the interface uses a placeholder.

## HTTP API

The interface uses same-origin JSON requests. Session-bound operations send the token in `X-Session-ID`.

| POST endpoint | Purpose |
| --- | --- |
| `/api/session` | Create or open a browser session |
| `/api/conversations` | List conversation history |
| `/api/turn` | Submit a conversation turn |
| `/api/progress` | Read search progress |
| `/api/cancel` | Cancel the active search |
| `/api/favorites/list` | List saved properties |
| `/api/favorites/add` | Save a returned property |
| `/api/favorites/remove` | Remove a saved property |

Request handling is in [http.py](http.py); session and workflow integration are in [bridge.py](bridge.py). Consult these files for request fields and error responses.

## Deployment limitations

The built-in `ThreadingHTTPServer` is a development server. Browser sessions are stored in the server process, while conversation records, favorites, and workflow checkpoints are stored in PostgreSQL.

The app does not provide production user authentication or web sessions shared across instances. A public deployment needs those features, HTTPS, and process management. Binding to `0.0.0.0` only changes the listening address; it does not add authentication.

The API checks request origins against the host. The frontend renders model and listing text with `textContent`, and source links allow HTTP(S). These measures do not replace access control.

## Files

| Path | Contents |
| --- | --- |
| `public/index.html` | Page structure |
| `public/style.css` | Layout and styles |
| `public/js/` | Conversations, requirements, listings, favorites, and API calls |
| `public/app.js` | Startup and event binding |
| `public/shortcuts.js` | Commute, MRT, and school inputs |
| `public/mrt-stations.json` | MRT station data |
| `server.py` | Server startup |
| `http.py` | HTTP routing |
| `bridge.py` | Browser sessions and orchestration |
| `cards.py` | Listing card data |

## Checks

```bash
for script in web/public/*.js web/public/js/*.js; do node --check "$script"; done
.venv/bin/python -m unittest tests.test_web tests.test_web_assets tests.test_orchestration
```
