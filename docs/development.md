# Development and Diagnostics

Run the following commands from the repository root. See [README](../README.md) for installation, database, and web startup.

## Requirements CLI


Create a Git-ignored `.env.local` in the repository root:

```text
DEEPSEEK_API_KEY=your_local_key
```

On first use, create a project-local virtual environment and install dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Start interactive debugging for the same `thread_id`:

```bash
./.venv/bin/python -m property_agent.requirements.cli --thread demo-001 --tone warm --trace
```

The CLI automatically loads `.env.local` and supports:

```text
/state       full checkpoint state
/profile     current ConversationProfile
/patch       proposed_patch and validated_patch
/answer      most recent HousingQuestionAnswer and sources
/request     RequirementRequest ready to hand to B after confirmation
/trace on    show the nodes traversed in each turn
/tone warm   switch the warm / direct / concise reply template
/reset       clear the checkpoint and local repository in the current CLI process
/quit        exit
```

The CLI uses `InMemorySaver` and `InMemoryProfileRepository`, so debugging state disappears after the process exits.
Tone switching only changes user-visible templates; it does not modify the profile, patch, or RequirementRequest.

When calling DeepSeek V4 Flash locally, read the key only from environment variables:

```python
from property_agent.requirements import DeepSeekRequirementInterpreter, build_requirement_graph

graph = build_requirement_graph(interpreter=DeepSeekRequirementInterpreter())
```

Set `DEEPSEEK_API_KEY` in the terminal before running. Do not write real keys into code, fixtures, command history, or
`.env` and then commit; the repository already ignores `.env` and `.env.local`. The official model ID is
`deepseek-v4-flash`.


## Large Model API Integration

`property_agent/runtime/model_client.py` provides a shared HTTP client through DeepSeek's OpenAI-compatible `/chat/completions` interface.
B's existing LangGraph nodes use it through the `DeepSeekChatModel.ainvoke()` adapter, and C's structured steps use
`DeepSeekChatClient.complete()` with the same underlying implementation.

Python 3.11+, install the dependency versions used for this validation:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Fill in the key provided by the organizers in `.env` at the project root. Existing files can be edited directly; for a new environment, copy the following configuration:

```dotenv
DEEPSEEK_API_KEY=
# Optional override; usually the runtime.toml defaults are sufficient
DEEPSEEK_API_BASE=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_TIMEOUT_SECONDS=45
DEEPSEEK_TEMPERATURE=0
DEEPSEEK_MAX_TOKENS=2600
```

Environment variables take precedence over `.env`; by default it is read from the project root, independent of the startup directory.
The key is required; `.env` and `.venv` are already Git-ignored. For the URL, enter the API root address and do not append `/chat/completions`.

Run the three sets of real model input/output checks in `__main__` (requires a configured key and network access):

```bash
.venv/bin/python -m property_agent.runtime.model_client
```

The three inputs are rental requirements for Tampines, Clementi, and Punggol, calling the real gateway through LangGraph,
printing the actual replies. Each run generates at most 256 tokens and waits according to the configured limit; failure exits non-zero.
`--live` is retained only for command compatibility; it now performs real calls by default and does not inject simulated responses.

Business code creates the model when constructing the service, then injects it into node closures:

```python
from property_agent.runtime.model_client import create_chat_model

model = create_chat_model()

async def model_node(state):
    reply = await model.ainvoke(state["messages"], stream=False)
    return {"messages": [reply]}
```

The model client and key are not placed into shared contracts, `RunContext`, or graph state.
`source_mode` still only describes the listing source; business nodes must subsequently constrain remaining time according to `ctx.deadline_at`.
The search business graph is in `property_agent/search/graph.py`.

The search management node uses the message interface to let the model select a task from the legal task menu, then strictly validates the JSON and task ID.
The model does not generate listings, addresses, or coordinates; these facts must be obtained by the Provider.
Does not rely on the gateway's native tool calling or `bind_tools()`.

## 3a Listing Search and Details

`property_agent/search/capabilities/listings.py` provides two asynchronous internal capabilities for LangGraph execution nodes to call:

- `ListingsCapability.search_page(plan, query_id, *, ctx, cursor=None)`: executes one page of search and returns
  `Result[ListingPage]`, containing standard `Listing`, next-page cursor, pagination trust status, truncation flag, and filter coverage.
- `ListingsCapability.read_detail(listing, *, ctx)`: reads details and merges evidence, returning `Result[Listing]`.
  Detail failure preserves the search page data and returns `partial`; same-basis price conflicts preserve both pieces of evidence and set the amount to `None`.

Run the three sets of real search and detail checks in the module `__main__`:

```bash
.venv/bin/python -m property_agent.search.capabilities.listings --output /tmp/listings-live.json
.venv/bin/python -m property_agent.search.providers.guru_search
```

Inputs come from the shared `SearchPlan` / `RunContext` contracts, querying Tampines, Clementi, and Punggol by default.
Prints the actual 3a inputs, search outputs, and detail outputs; there are no preset listing results, and independent regression tests are separately in `tests/`.
Passes only after all three searches obtain candidates and details succeed. Both modules also support running via `python -m`.
Detail acceptance also verifies item by item the furniture, listing date, landlord co-residence, utilities, Wi-Fi, cooking,
visitors, pets, shared bathroom, and lease term explicitly marked on the page, along with the corresponding detail evidence; a successful request alone is not sufficient to pass.
The adapter reads both semantic labels and icon labels; reused icons must match explicit original text; for example, a listing ID will not be treated as
utility information, `Not tenanted` will not be treated as a visitor rule, and the lease term will not override the lease term.

The real source requires Node.js 20+, OpenCLI, and a connected Browser Bridge. When installing or updating the adapter,
you must copy all three files together: search, detail, and field conversion (copying only search.js will miss modules):

```bash
npm install -g @jackwener/opencli@1.8.7
mkdir -p ~/.opencli/clis/propertyguru
cp guru_search/cli/propertyguru/{search,detail,contract-listing}.js ~/.opencli/clis/propertyguru/
opencli doctor
opencli propertyguru search Tampines --listing rent --max 3500 --page 1 --limit 3 --output-mode page -f json
opencli propertyguru detail <listing ID> --output-mode structured -f json
```

Install and enable the [OpenCLI extension](https://chromewebstore.google.com/detail/opencli/ildkmabpimmkaediidaifkhjpohdnifk) in the browser,
`opencli doctor` should show that the daemon and extension are connected. Search reads public listings and does not need to send inquiries.
Python automatically looks in PATH or `~/.npm-global/bin/opencli`, and `OPENCLI_BIN` can also be configured in `.env`.

The default search/detail commands still retain the original list/summary form. The Python Provider uses the newly added `page` / `structured`
form; legacy adapter output will report a parse failure. The pagination cursor is generated by the source, in the form `pg:v1:2:0`; do not guess the last page yourself.
When the candidate limit is reached midway, the cursor retains the current page offset so that the next search can continue. If a page change invalidates the offset, an error is reported.

At the start of a `search` execution, construct a shared quota object and inject the capability object into the Provider:

```python
from property_agent.search.execution.budget import SearchBudget
from property_agent.search.providers.guru_search import GuruSearchProvider
from property_agent.search.capabilities.listings import ListingsCapability

# plan and ctx are passed in by the orchestration layer; their source_mode/attempt_id must match.
budget = SearchBudget(plan, ctx)
listings = ListingsCapability(GuruSearchProvider(), budget)

async def listings_node(state):
    result = await listings.search_page(
        state["plan"], state["query_id"], ctx=state["ctx"],
        cursor=state.get("cursor"),
    )
    return {"listing_page_result": result}
```

The Provider and quota object remain in the service/node closure and are not placed into serializable graph state. A new quota object is created for each execution,
and queries within the same execution share it; failed calls also consume page attempts. This quota implementation is for single-process execution; when restoring a persisted graph,
the subsequent execution layer must restore the used quota and must not reset it and continue calling.

The price, bedroom, whole-unit/single-room, and property category conditions supported by the source are pushed down to the website by the Provider;
conditions that cannot be expressed precisely are retained as pending verification. Free-text location search does not mean the listing area has been verified. 3a does not relax hard conditions, nor does it handle search management, final aggregation, or neighborhood/commute investigation.

The adapter reads public data from the page's `__NEXT_DATA__` script and is compatible with the Browser Bridge isolated execution environment.
For details, read the address and postal code from the listing's own `listingDetail.location.address` for 3b to query;
do not treat the display template of a nearby MRT as the listing address.

## 2 / 3a / 3b integration testing

The search business subgraph is already connected: `supervisor → execute → supervisor → aggregate → END`.
The management layer first opens only 3a search page tasks (including reusable pages), and only after the search phase ends does it open 3a details and 3b address geolocation.
The Agent selects one task within the current phase, and code is prohibited from calling across phases; quota exhaustion can enter the supplementary phase, but that does not mean the search is fully complete.
After the search ends, aggregate into the contract-specified `Result[SearchResult]`, without modifying the hard conditions given by A.
Plan generation for `property_agent/search/planning/planner.py` is already connected; 3c neighborhood facilities and 3d commuting are also integrated, see the capability description at the end.

```bash
.venv/bin/python -m property_agent.search.execution.supervisor
.venv/bin/python -m property_agent.search.capabilities.location
.venv/bin/python -m property_agent.search.providers.onemap
```

All the above commands make real external calls and require configuration and network access. Each module defaults to at least three sets of input,
and prints the actual output as JSON; there is no mock Provider, preset model decision, or preset coordinates.
`property_agent.search.execution.supervisor` checks that each set has a successful search, details, a unique geolocation, and a real model decision;
model degradation or uncertain geolocation will not count as passing integration testing. It also checks that each round's model menu does not mix phases, and that all search tasks execute before supplementary tasks.
By default, each set actually fetches only one page and one candidate, and reuses a real page with a second query ID, verifying that when candidates already exist, remaining search tasks must still be handled first,
so the result may return `partial` due to the candidate quota; this does not mean a listing meeting all hard conditions has been found.
Phase testing by default focuses on 2→3a→3b and does not register optional companion Providers for now; adding `--with-investigations` also executes registered supplementary investigations.

```bash
.venv/bin/python -m property_agent.search.execution.supervisor --output /tmp/search-live.json
```

2026-09-18 real local verification: the full chain passed for all three sets, Tampines, Clementi, and Punggol,
with corresponding actual listing IDs `500252593`, `500256915`, and `60052380`.
Each set had its task selected by the real gateway, read the search page and details via Browser Bridge, and then called OneMap for geolocation.
All three geolocation results were at building precision, the model did not degrade, and the shared output contract validation passed.
These three actual listings were also used as `--input` for 3b, and real geolocation also passed 3/3.
These are raw candidates, and it cannot be claimed on this basis that they satisfy the whole-unit or minimum-bedroom-count conditions.

### OneMap configuration

3b uses the [OneMap Search API](https://www.onemap.gov.sg/apidocs/search).
It now requires a Token; the [authentication API](https://www.onemap.gov.sg/apidocs/authentication)
returns a Token and expiration time. Add one of the following configurations to the existing `.env`:

```dotenv
# Option one: manually provide a valid Token
ONEMAP_TOKEN=

# Option two: configure the registered email and password, and the Provider obtains and re-obtains the Token after expiration
ONEMAP_EMAIL=
ONEMAP_PASSWORD=
```

You can provide both a Token and email/password; when the Token is invalid, re-authentication is attempted at most once.
When only a Token is available, expiration returns `AUTH_REQUIRED`, and authentication failure will not be treated as no address match.
Sensitive configuration does not enter graph state, model messages, or returned results. The default OneMap request interval is 0.25 seconds,
and each address query reads at most 3 pages; when candidates have not been fully read, it will not claim a unique match.

### Calling in code

```python
from property_agent.search.api import create_live_search_service

# Inject the existing large model gateway, GuruSearchProvider, and OneMapProvider at construction time.
# The service can be reused, and each search independently creates quota, state, and execution cache.
service = create_live_search_service()
result = await service.search(plan, ctx=ctx)

# When integration testing needs to inspect internal execution history and geolocation candidates, use run instead of search:
# state = await service.run(plan, ctx=ctx)
# state["history"] / state["locations"] / state["result"]
```

`plan` and `ctx` come from A/the orchestration layer and still use the fields from `property_agent/contracts.py`:
`source_mode="live"`, the query source is `propertyguru`, `attempt_id` matches,
and `ctx.deadline_at` is a future time with a time zone.
Do not call `run` first and then call `search` to get the same result, as that would start two searches;
the `state["result"]` returned by `run` is the SearchResult aggregated internally by B.
If you need to reorganize the internal state already obtained, you can also call `property_agent.search.aggregation.results.aggregate(state, started)`,
where `started` is the `time.monotonic()` recorded before execution; re-aggregation will not call external services.

For internal debugging, you can also use the CLI to read the JSON input file generated by B, with the structure `{"plan": {...}, "ctx": {...}}`:

```bash
.venv/bin/python -m property_agent.search.execution.supervisor --live --input /absolute/path/search-input.json
```

Real integration testing requires DeepSeek configuration, OneMap credentials, and an OpenCLI / Browser Bridge environment.
When there is no OneMap Token or account password, constructing the real service directly reports `AUTH_REQUIRED` and will not pretend geolocation succeeded.
When the model is unavailable or returns an illegal task, the production flow switches to fixed scheduling and clearly explains this in `issues`;
Real integration acceptance still judged as failed. `SearchService(model=None)` can explicitly use fixed scheduling.

The shared contract retains `source_mode="mock"` for compatibility with other modules; this default entry point and acceptance use only `live`.

### Positioning and Result Boundaries

- 3b reads the explicitly labeled Address, Postal code, Building / Project from 3a's `raw_details`,
  or the address fields in the `Location information` JSON; does not use nearby subway locations to impersonate the property location.
- `LocationCapability.locate(request, *, ctx)` can be reused by subsequent 3c/3d.
  `LocationRequest` and `LocationResult` are defined in `property_agent/search/execution/tasks.py` and are internal interfaces.
- Only a unique and information-consistent candidate confirms coordinates; house number/road conflicts, multiple buildings, or unfinished candidate pagination retain ambiguity.
  Precision indicates matching to a building or road, and does not represent GPS measurement error.
- Positioning facts are written into the existing `Listing.evidence` (`field="location"`, `value` contains address,
  latitude/longitude, precision, and source mode); unresolved items go into `field_issues` and `Result.issues`.
  Do not add public fields, and do not use coordinates or addresses to replace the canonical `location_id`.
- All queries in the same search share the page count and candidate quota. The candidate quota follows 3a's returned item count,
  including duplicate items; the final output is then deduplicated by `listing_key`. After the quota is exhausted, details/positioning can still be supplemented before the deadline.
- Temporary failures are retried at most once by default; respect `retry_after_seconds` and the deadline.
  A complete no-match returns `success` with an empty list; existing listings but incomplete returns `partial`; total failure returns `error`.
- `has_more` only expresses that the source provided a usable continuation cursor; when pagination is unknown, combine with `queries_completed=False`
  and `issues` to judge; `has_more=False` cannot be understood alone as fully searched.
- Cache and task history are used only within a single search; persistent checkpoint recovery is not enabled, and a new quota object cannot continue an old graph.

## 4 / 5 Aggregation and Complete search

`property_agent/search/aggregation/results.py` organizes the management-layer end state, and the `aggregate` node in `property_agent/search/graph.py` generates before exiting
`Result[SearchResult]`. The internal call is `await api.search(plan, ctx=ctx)`; A uses the new entry point `fulfill_requirements` described below.
The aggregation stage does not continue dispatching work, does not rewrite hard conditions, and does not execute C's match filtering or recommendation.

- Deduplicate by `listing_key`, and verify source and mode before merging; do not treat similar titles from different sources as the same listing.
- Merge search/detail/positioning evidence, raw descriptions, and unresolved items. When evidence IDs collide, retain both facts and synchronously update price references.
- Known field conflicts retain markers; price conflict amounts are set to `None`, status is `conflict`, and subsequent duplicate items cannot overwrite the conflict.
- Date and time are unified as UTC, currency is stripped of whitespace and converted to uppercase. Amounts retain the source currency and period, and area follows 3a's sqft;
  do not guess exchange rates, monthly rent conversions, or unknown areas, and do not arbitrarily fill fields from free text.
- `applied_filters` takes the commonly supported items of the actual pages; conditions not applied on some pages are listed in `unsupported_filters`.
  Historical failures after retry recovery do not enter `failed_sources`, and invalid or looping cursors are not passed to the next round.
- If all are complete and there are no issues, return `success`, including a genuine empty result; if there are valid results but gaps/truncation exist, return `partial`;
  if no valid page was obtained and it failed, return `error`, `data=None`.
- `property_agent.domain.validation.validate_search_result` validates the complete output, evidence references, tracking identifiers, version, counts, and coverage consistency;
  output validation failure uses `INVALID_OUTPUT`, distinguished from the caller's `INVALID_INPUT`.

The full-chain test is placed in `if __name__ == '__main__':` in `property_agent/search/api.py`, calling the new public entry point `fulfill_requirements`.
The diagnostic entry point uses requirements designed according to actual PropertyGuru listings in `scripts/live_requirements.py`; currently by default only the first group is run,
and the specific acceptance method is described below. It actually calls the model, guru_search, and positioning service, without injecting reference listings.
The test script directly passes in the request and context, and prints the entry point's raw return.

```bash
.venv/bin/python -B api.py
```

The real input/output check of 4/5 itself is also in the module `__main__`; after at least three groups of search inputs are given,
execute the actual management/search/positioning, send the final internal state to aggregation, and save the actual data on both sides:

```bash
.venv/bin/python -m property_agent.search.aggregation.results --input /absolute/path/search-inputs.json --output /tmp/aggregation-results.json
```

2026-09-18 complete `property_agent.search.api.search` real acceptance passed 3/3: Tampines / Clementi / Punggol,
listing IDs are `500252593` / `500256915` / `60052380` respectively.
Each group obtained real details and OneMap positioning evidence, with no model degradation; all ultimately returned `partial` due to one candidate quota,
the only issue was `BUDGET_EXHAUSTED`, and the `pg:v1:1:1` continuation cursor was retained.
In addition, a merge check was performed on observations of these three listings across two real calls; evidence collision renaming, price references, and duplicate merge idempotency all passed.
This acceptance covered the actual chain with candidates and quota truncation; virtual responses were not used to fake empty results or external service failures.

Detail reading preferentially uses the complete listing link returned by the search page. The search and detail adapters check every 300 milliseconds after navigation
the actual data in `__NEXT_DATA__`, waiting at most 12 seconds; OpenCLI's `page.wait()` returns early after the DOM stabilizes,
so it cannot be used to determine that listing data has loaded. The check also verifies the current query or listing ID to avoid reading an old page.
If it remains not ready, return `TEMPORARY_UNAVAILABLE`, and the existing management layer retries at most once within the remaining quota and deadline.
Similar-priced ads explicitly marked as `promoted-listing-card` on the search page are excluded before candidate truncation and continuation offset calculation,
to prevent over-budget ads from mixing into search hits; ordinary search cards keep their original order. Data that cannot be read is not treated as a successful detail or a fabricated address.
Both commands navigate directly to the target URL themselves, disabling OpenCLI's default homepage pre-navigation, to avoid extra redirects being rejected by the browser
and reporting `Navigation rejected` before executing the adapter. If the target navigation still encounters this explicit browser error,
release only the current adapter's tab lease and recover once in a new tab; it must still pass the same URL, data, and validation page checks,
without adding business search retries or resetting the user's browser.

## New Interface Integration and Complete Integration Testing

A only calls `property_agent.search.api.fulfill_requirements(request, *, ctx)`. The input is the confirmed
`RequirementRequest`, and the output is `Result[RequirementFulfillment]`. Do not have A construct queries again,
Plan, history, or Provider parameters. B internally retains `prepare_query`, `build_search_plan`, `search`
three contract functions, for module integration use; their profile parameters have been changed to `ConversationProfile`.

```python
from property_agent.search.api import fulfill_requirements

result = await fulfill_requirements(request, ctx=ctx)
if result['data'] is not None:
    fulfillment = result['data']
    # When needs_clarification, A asks the question; otherwise the search_result candidates are handed to C for filtering.
    questions = fulfillment['clarification_questions']
    search_result = fulfillment['search_result']
```

The outer LangGraph executes `prepare_requirements → plan_search → execute_search → summarize_requirements`.
The original request is always retained in the current graph state; the internal normalized projection extracts filter conditions that can be pushed to guru_search,
the remaining Listing conditions are deterministically checked against the results, and must not be lost just because the SearchPlan has fewer fields.
No user-confirmed conditions were modified, and no complete ConversationProfile was fabricated. B trusts the confirmation handoff issued by A;
A is still responsible for verifying `confirmed_version == version`, and B checks the requested version, confirmation time, and session consistency.
`ctx` is passed through to each stage unchanged; `attempt_id=None` is allowed, in which case B generates an internal round ID for the plan.

Candidate listings retain their real fields, original sources, and evidence, and C continues filtering and ranking them. Condition mismatches or unknown fields are explicitly
recorded in `field_issues`; unknown hard conditions cause `partial`, and unknown is never treated as satisfied. Exhausting the search budget likewise
returns `partial` along with the actual pagination information. The listing fields themselves follow the latest shared Listing, with no private return fields added.

Derived requirements are returned one by one as fulfilled / unsupported / unverified; real property search, details, and OneMap are already supported,
along with address geolocation, commuting, and five categories of nearby amenities; environment and administrative district attribution verification are not yet supported. Searching by area term does not equal a verified administrative district,
and coordinates do not prove commuting time either. Open requirements can only be checked on a best-effort basis, and are currently explicitly listed in
`skipped_best_effort_requirement_ids`; even if marked hard, they will not block the search or independently lower the completion status.
When information that would block the core query is missing, return `needs_clarification` and a structured question; no external services need to be initialized.

The configuration for the model, browser, and OneMap follows the description above. The search budget comes from `.env` or the process environment:

```dotenv
SEARCH_PAGE_LIMIT=4
SEARCH_CANDIDATE_LIMIT=12
SEARCH_PAGE_RESULT_LIMIT=6
SEARCH_PROVIDER_TIMEOUT_SECONDS=30
SEARCH_MAX_RETRIES=1
SEARCH_PLANNER_TIMEOUT_SECONDS=20
SEARCH_SUPERVISOR_TIMEOUT_SECONDS=8
SEARCH_SUPERVISOR_MAX_CALLS=3
SEARCH_FINALIZE_RESERVE_SECONDS=10
```

The above are default values, and the process environment takes precedence over `.env`. An explicit total quota in the plan still takes precedence; each search page returns
no more than 6 items and no more than the remaining candidate quota. The page count includes failed attempts, and retries cannot exceed the total quota.
The planning model and the management model are limited to 20 seconds and 8 seconds respectively, and are also subject to `DEEPSEEK_TIMEOUT_SECONDS` and the remaining time in this round.
time limit. The management model is called at most 3 times per round (failures also count), after which a fixed schedule is used to continue execution.
Browser calls continue to be serialized. Ten seconds before the deadline, stop and cancel any unfinished external tasks, retain existing results, and proceed to
summarization; do not rewrite `ctx.deadline_at`, and if the search is not finished, still return `partial` or `error`.

Running `python -m property_agent.search.graph` executes the three sets of real search configuration checks in the file (Tampines, Clementi, Punggol);
each set has a default total time limit of 120 seconds, which can be changed with `--timeout-seconds`. It checks real candidates, per-request item count, page count,
model call count/duration, retries, and reserved summarization time; `--output` can save the actual input and output. When a source fails,
it will not substitute virtual data, nor will it mark a case with no real candidates as passed.

You can reuse `property_agent.search.api.create_live_fulfillment_service()`; for internal integration use `service.run(request, ctx=ctx)`
to read the query, plan, SearchResult, and final result. Each request uses independent state and budget, and does not enable cross-request
history persistence or automatic pagination. `SearchDirective` / `AttemptSummary` can still be used for B's internal explicit pagination,
but they are not A's public input; the internal history fingerprint uses `property_agent.search.execution.history.query_fingerprint`.

`scripts/live_requirements.py` retains the following four requirements, and `INPUTS` selects the first set by default. On 2026-09-20, PropertyGuru detail pages were opened to verify the design basis;
these are test requirements that comply with `RequirementRequest`, not A's production logs, and they do not fabricate B's returns.

| No. / ID | Input requirement | Actual listing basis |
| --- | --- | --- |
| 1 / `tampines_condo_rent` | Whole-unit condo rental in Tampines; monthly rent ≤ SGD 3800; at least 2 bedrooms; fully furnished | [Treasure at Tampines](https://www.propertyguru.com.sg/listing/for-rent-treasure-at-tampines-25155665): 3500/month, 2 bedrooms, fully furnished |
| 2 / `clementi_common_room` | Common room in an HDB flat in Clementi; monthly rent ≤ SGD 1300; utilities and Wi-Fi included | [712 Clementi West Street 2](https://www.propertyguru.com.sg/listing/hdb-for-rent-712-clementi-west-street-2-500255206): 1200/month, common room, utilities and internet included |
| 3 / `punggol_family_rent` | Whole-unit HDB rental in Punggol; monthly rent ≤ SGD 4200; at least 3 bedrooms and 2 bathrooms, 1000 sqft | [203A Punggol Field](https://www.propertyguru.com.sg/listing/hdb-for-rent-203a-punggol-field-25359579): 3800/month, 3 bedrooms and 2 bathrooms, 1184 sqft |
| 4 / `bishan_hdb_buy` | HDB purchase in Bishan; total price ≤ SGD 1000000; at least 3 bedrooms, 1000 sqft | [207 Bishan Street 23](https://www.propertyguru.com.sg/listing/hdb-for-sale-207-bishan-street-23-500255052): 920000, 3 bedrooms, 1109 sqft |

Each set has an independent request/session ID, the complete original input text, and the correct `SourceReference` offsets.
For purchases, use `intent=buy`, `transaction_type=sale`, `price.period=total`; for a single room, do not use the whole-unit bedroom count to limit the room.
The table above retains the historical listing basis for the four requirement sets, and is not used as interface input or expected return.

The four complete requests are hardcoded in `request_1` through `request_4` in `scripts/live_requirements.py`.
At runtime, construct `ctx` set by set (the deadline is five minutes from the time of the call), and directly call
`await fulfill_requirements(request, ctx=ctx)`, printing the raw return.
The script does not read example files, and contains no extra validation, reporting, internal scheduling, or command-line options.

```bash
.venv/bin/python -B -m scripts.live_requirements
```

The module run entry point of `property_agent/search/api.py` reuses the same call; `python -m property_agent.search.planning.planner` only runs internal plan generation.
Other modules still reuse these four request sets through `INPUTS`.

The following is the historical chain record of the old inputs, and does not mean that the above new requirements or examples cases have passed acceptance:
On 2026-09-20, the four complete chains of the new public interface were actually executed, 4/4 passed: 8 live listings in total,
all 8 had real details and OneMap geolocation evidence, with no model or source call failures. The four sets returned
partial due to the two-candidate budget; Tampines and Punggol each had one additional rental range pending verification. Punggol's hard + best_effort
open requirement was explicitly skipped and the listing was retained. Administrative district verification was marked unsupported, and coordinates were not treated as proof of administrative district.
In addition, three sets of actual requirements missing currency/period passed the clarification branch check; parsing and query conversion each passed for four sets,
requirement summarization passed for four sets using replay of historical real upstream results, and the final real outputs of these four sets were verified.

## Search card images (handed to the presentation side)

3a parses during the reading process of an existing guru_search search page
`listingData.mediaCarousel.previewMedia` and `thumbnail`, retaining all photos provided by the card,
floor plans, site plans, and cover image size links, without limiting the number of images per listing, without opening the detail page for each listing,
without calling a large model, and without downloading images. The current real page already provides the complete carousel array in the initial structured data,
Therefore there is no need to simulate flipping through images one by one. When future pages no longer provide the complete array, retain the links already obtained and report the gaps.

The shared `Listing` fields remain unchanged. Each listing's `evidence` gains one entry
for `field="media.search_card_photos"`, where `source_url` is the listing page and `observed_at` is the collection time.
The `value` format for this evidence is defined as follows (`version=1`):

| Field | Meaning |
| --- | --- |
| `images` | Array of images saved in source order; empty when there are no images |
| `images[].image_id` | Page CDN image identifier; when the identifier cannot be recognized, use the original link and do not guess the image identity |
| `images[].kind` | `photo`, `floor_plan`, `site_plan`, `thumbnail`, or `video_thumbnail` |
| `images[].urls` | All observed links for the same image; the main image link comes first, followed by cover sizes; do not rewrite sizes or remove query parameters |
| `images[].caption` | Page image caption; `null` if absent |
| `reported_count` | The count shown on the card's Photos; `null` if unknown; excludes floor plans/site plans/videos |
| `extracted_count` | The number of unique `photo` items obtained; multiple sizes of the same image count only once |
| `status` | `complete` when counts align and there are no parsing issues; `partial` when fewer than the reported count; `unknown` when it cannot be confirmed; `unavailable` when no images were obtained and zero was not explicitly indicated |
| `issues` | Image parsing, count, or listing attribution issues; these do not block the search or downgrade the entire search result |

Keep the same URL only once. A standalone cover image that cannot be matched to an album image is still retained as a `thumbnail`,
and is not used to pad the photo count. `complete` only means that the photo count shown on this card has been aligned;
it does not guarantee that links remain valid forever, nor does it promise that the detail page has no other photos.
Detail supplementation, physical page caching, and the final summary will retain this evidence; multiple observations may retain multiple records, and the display side uses the latest one.

The display side can obtain the photo carousel data as follows, with each item's `urls` retained as size fallbacks when loading fails:

```javascript
const observations = listing.evidence
  .filter(e => e.field === 'media.search_card_photos' && e.value?.version === 1)
  .sort((a, b) => Date.parse(b.observed_at) - Date.parse(a.observed_at));
const media = observations[0]?.value;
const photos = (media?.images ?? []).filter(image => image.kind === 'photo');
const slides = photos.length ? photos : (media?.images ?? []).filter(image => image.kind === 'thumbnail');
// For each slide, prefer slide.urls[0]; if loading fails, try the remaining links; if all fail, show a placeholder image.
// Floor plans and site plans can be displayed separately by kind; do not render multi-size links as duplicate photos.
```

The real acceptance entry point is in the 3a file's own `__main__`, which by default runs the three groups Tampines / Clementi / Punggol
for the 2→3a live input, comparing item by item against the original media links, counts, and order from the same search, and verifying caching, deduplication merging, and the final summary:

```bash
.venv/bin/python -B -m property_agent.search.capabilities.listings --photos-only --output /tmp/propertyguru-photo-live-results.json
```

You can use `--input` to specify at least three real `{plan, ctx}` groups. The test uses guru_search's
`--include-media-source` diagnostic switch to obtain the original media; it is not enabled during normal operation, and raw image data is not transmitted repeatedly.
After modifying the adapter, `search.js` and `contract-listing.js` must be synced to `~/.opencli/clis/propertyguru/`.

2026/09/24 real acceptance: three search groups of 20 listings each, 60 listings total, 878 photos, 961 image links
(including other sizes and additional images), matching the original page data from the same run item by item; caching, duplicate merging, and the final summary passed.
After supplementing details for another three real listings, the image evidence remains unchanged. `propertyguru:24214804` in the screenshot
obtained 9 photos and 10 links, and all 10 links loaded successfully cross-site in the browser.

## 3c Nearby Amenities and 3d Travel

3c queries MRT/LRT stations, bus stops, supermarkets, schools, and parks from the residential coordinates. The default radius is **1500 meters straight-line distance**;
an explicit user distance upper bound takes priority, supporting 1–4999 meters. OneMap provides transport and park points; OpenStreetMap
provides supermarket and school points (including kindergartens, colleges, and universities). Records explicitly marked as playgrounds in the OneMap Parks theme are not treated as parks.
The default Overpass node is the global mirror listed in the OSM documentation, `https://maps.mail.ru/osm/tools/overpass/api/interpreter`;
you can use `OVERPASS_URL` to specify another instance with Singapore data. OSM data attribution © OpenStreetMap contributors (ODbL).
Do not configure a regional instance that only has data for other countries, otherwise an empty list has no meaning for Singapore facility coverage.

3d by default travels from the residence to the specified destination; if no commute requirement is raised separately but `user_context` has a work/school address, also produce a commute overview.
Default: **Asia/Singapore time zone, departing at 08:00 on the next future Monday through Friday, public transport combined route**.
Public holidays are not additionally excluded for Monday through Friday, and this assumption is saved in the evidence. When the user specifies walking, driving, cycling, or bus/rail modes,
or an explicit date/time, override the corresponding defaults. Supports ISO dates, today/tomorrow/the day after tomorrow, weekdays, and English clock expressions;
for multiple time periods, time periods that cannot be clearly interpreted, or locations, retain the gaps and do not fall back to conditions the user never specified.
For arrival time requirements, use at most two real departure queries to verify one public transport option that can arrive before the deadline, and do not claim the latest departure time.
The driving, walking, and cycling APIs are static route time estimates and must not be treated as real-time morning peak traffic predictions.

The accompanying walking distance/time conditions are verified by 3c calling the same 3d capability, querying at most the 5 nearest points by straight-line distance for each category.
When only some points are found, do not claim they are absolutely the nearest; for empty results or when no route meeting the threshold is found, retain `unverified`.
Facility representative points may be building/area centers and are not guaranteed to be school gates or park entrances; the point definition is saved in the route evidence.
Do not treat straight-line distance divided by speed as the real walking time, and do not substitute any school of the same type for a specific school name.

A complete request explicitly enters the search graph through `FulfillmentService → SearchService.search_for_request`,
without changing the public `fulfill_requirements` or the shared `search(plan, *, ctx)` signature.
The existing "search → supplement → summarize" scheduling phases remain unchanged; the supplement phase adds `amenities` and `travel` tasks.
A single search by default allows at most 60 route calls and 60 facility category queries; the cache is isolated per user/session/run, and failures are not recorded as successful cache entries,
all actual calls obey the same `ctx.deadline_at`, and module 2 retries retryable failures at most once more.

The output uses only the original fields of the shared contract:

- `nearby_amenity.<category>` in `Listing.evidence`: query scope, source points, straight-line distance, and coverage limits.
- `travel`: origin and destination, mode, specified time period, actual route segments, distance, duration, and default assumptions.
- `derived_requirement.<requirement_id>`: investigation completion status and condition comparison results.
- `field_issues`, `Result.issues`: gaps such as not located, source failure, and insufficient quota.

`fulfilled` means the corresponding investigation has been completed, not that the listing meets the threshold. For example, a real commute of 55 minutes with a requirement upper bound of 40 minutes:
the investigation can be completed, but the corresponding evidence has `check=fail`. C reads this comparison result and the real evidence for ranking and evaluation, without repeating the hard filter.
When a supported requirement has candidates that have not yet been fully investigated, it is `unverified` and returns `partial`; unsupported metrics remain `unsupported`.

The real-source check entry points are kept in each module's own `if __name__ == '__main__':`; offline regression is additionally in `tests/`:

```bash
.venv/bin/python -B -m property_agent.search.capabilities.travel --input /absolute/path/real-commute-input.json --output /tmp/travel-results.json
.venv/bin/python -B -m property_agent.search.capabilities.amenities --input /absolute/path/real-amenity-input.json --output /tmp/amenity-results.json
```

The commute input is at least three items of `{listing, location, requirement, user_context, ctx}`, and the listing and location must come from the actual upstream.
You can use `expected` to specify the mode, hours, minutes, or ambiguous result to assert; no preset map responses are injected.
The amenity input is at least three items of `{request, ctx}`, where `request` is `property_agent.search.execution.tasks.AmenityRequest`.
Each group of five amenity categories is actually requested; the test retains the return values before and after one bounded retry, without hiding external failures.

2026-09-20 this real acceptance: all three groups of 3c, Tampines / Clementi / Punggol, passed, and all five amenity categories obtained real responses;
The third group retained one external timeout and retry recovery record. All three groups of 3d passed on real routes (default transit, 09:00 transit, 08:30 driving),
Another real negative case where one NUS postal code corresponds to multiple buildings correctly returned pending verification, for a total of 4/4. The complete public test request was actually
A→B→guru_search→OneMap/OSM→requirement summary, and all three items—commute, walking transit stops, and nearby supermarkets—were fulfilled;
The sole reason for the overall partial is that one candidate search quota was exhausted. This result does not mean that all listings have been found.
The current shared contract is checked by `tests/test_contracts_sync.py` against the snapshot of 55 TypedDicts before cleanup.
