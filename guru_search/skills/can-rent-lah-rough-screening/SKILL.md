# /rough-screening

Read the user's rental profile, search PropertyGuru with their preferences, evaluate every listing against their needs, and present a ranked, plain-language recommendation list. Handle the filtering and trade-off thinking so the user doesn't have to.

## When to activate

- User says "帮我找房" / "find me a place" / "what's available?"
- User already has a profile from `/onboard`
- User wants to browse with specific criteria

If no profile exists yet, suggest `/onboard` first.

## Core flow

```
Read profile + log → Understand session goal → Search → Evaluate → Present → Log
```

## Phase 1: Load context

### Read the profile

Look for `record/profile/<name>.md` in the repo root. If multiple profiles exist, ask which one to use. Extract:
- School & preferred areas
- Budget (rent-only or all-in)
- Bedrooms needed
- Property type preference (HDB/Condo/room/either)
- Dealbreakers
- Timeline urgency

### Read the log

Read the last 100 lines of `record/profile/log.md`. Look for:
- Areas they consistently liked or rejected
- Price sensitivity signals ("too expensive", "can stretch to X")
- Unresolved questions from last session
- Property types they engaged with

### Clarify this session's goal

If the profile is broad ("Clementi OR Jurong, HDB OR Condo"), ask what to focus on THIS time. Don't search everything at once.

## Phase 2: Search

Run targeted searches using `opencli propertyguru search`. Strategy:

1. **Start narrow** — use the most specific criteria from the profile
2. **If too few results (< 5)** — broaden one dimension at a time (remove property type filter first, then raise budget, then expand area)
3. **If too many results (> 50)** — suggest tightening criteria
4. **Search multiple areas** if the user is flexible — run separate searches, merge and compare

### Search command template

```bash
opencli propertyguru search "<area>" --max <budget> --bedrooms <n> --limit 20 -f json
```

Add `--type hdb` or `--type condo` if the profile has a clear preference.

### When to search multiple areas

If the profile lists 3 preferred areas, search all 3 and compare. This is exactly where agent beats human — 60 listings across 3 areas, cross-compared in seconds.

## Phase 3: Evaluate each listing

For every listing in the results, score it across these dimensions. Don't just dump the raw data — add judgment.

### Scoring rubric

| Dimension | Check | Good signal | Bad signal |
|-----------|-------|-------------|------------|
| **MRT** | `mrt` field | ≤ 8 min walk | > 15 min or missing |
| **Price** | `price` vs budget | Within budget | > 20% over or suspiciously cheap |
| **Type match** | `propertyType` vs profile | Matches preference | Wrong type (Condo when user wants HDB) |
| **Floor area** | `floorArea` | ≥ 500 sqft for 2br | < 400 sqft ("cozy") |
| **Availability** | `availability` | "Ready to Move" or matches timeline | Available too late |
| **Listing age** | `postedDate` | Within last 7 days | > 2 weeks (good deals go fast) |
| **Description** | (if detail fetched) | Clear, specific info | "Cozy", "vibrant", no bathroom photos |

### Red flag detection

Scan the listing data for these and CALL THEM OUT:

- **No aircon** — description mentions "No aircon" or no aircon mentioned in HDB (check description)
- **Tenant agent fee** — description says "Tenant's agent fee applies"
- **Ground floor** — often mentioned in description
- **West sun** — "west facing" or "afternoon sun"
- **"Cozy"** = tiny. Flag it.
- **Price suspiciously low** — > 30% below market for that area/type
- **No MRT info** — listing doesn't show MRT distance, means it's probably far

### Trade-off articulation

The hard part of renting is trade-offs. Make them explicit:

> "This one is $200 below your budget and near MRT, but it's ground floor and partially furnished."
> "This one is perfect except no aircon. At $2500 you could probably get aircon for $200 more."

## Phase 4: Present results

Present a ranked list, not a data dump. Format each recommendation like:

```
### 🥇 #1: 308 Clementi Ave 4 — S$2,500/mo
**Why this fits**: 9 min walk to Clementi MRT (EW+CR line to NUS).
HDB 2br, 722 sqft. Ready to move.
**Watch out**: No aircon. Tenant agent fee applies. Partially furnished.
**Verdict**: Good location & price, but budget ~$200 more for aircon unit + agent fee.
🔗 https://www.propertyguru.com.sg/listing/hdb-for-rent-308-clementi-avenue-4-500149207
```

Rank by overall fit, not by price. Best overall match at the top.

### Ranking tiers

- **🥇 Top picks** (top 3-5): Best overall fit. Few or no red flags.
- **🥈 Worth considering** (next 3-5): Good but has trade-offs.
- **🥉 If you're desperate** (rest): Significant issues but technically meets criteria.

### Don't show everything

Show top 8-10 max. If there are 20+ results, filter to the best 10 and note: "20+ results found, showing top 10. Want to see more or narrow filters?"

## Phase 5: Actions after presenting

After showing results, offer:

1. **"Show details for #3"** → run `opencli propertyguru detail <id>` to get full description
2. **"Shortlist #1 and #4"** → run `opencli propertyguru shortlist <id>` for each
3. **"Search near NTU instead"** → switch areas, new search
4. **"Under $2200 only"** → tighten budget filter
5. **"Compare #1 vs #3 side by side"** → pull both details, present comparison table

## Phase 6: Append to log

After the session, append to `record/profile/log.md`:

```markdown
## <YYYY-MM-DD HH:MM> — Rough screening: <area>, <filters>

### Search context
- Searched: <areas>, max S$<budget>, <bedrooms>br, <type>
- Results found: <count>

### User feedback
- Liked: <which listings and why>
- Rejected: <which listings and why>
- New preferences: <any shifts>

### Learnings
- <any new insight about user's preferences>
```

## Important rules

- **Don't make the user read raw JSON.** Translate every listing into human language.
- **MRT is always the first thing to mention.** It's the #1 factor for students.
- **Always include the URL.** User needs to see photos.
- **Respect the profile's dealbreakers.** Don't show ground-floor units if they said no ground floor.
- **One search at a time, one area at a time.** Don't fire 5 searches in parallel — the user can't process that many results.
- **The log is sacred.** Every session adds one entry. Never delete.
