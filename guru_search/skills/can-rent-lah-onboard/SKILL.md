# /onboard

Interview the user about their Singapore rental needs, educate where they're uncertain, and produce a structured preference document. After each session, append what was learned to an append-only log so future sessions get smarter.

## Core flow

```
Interview → Educate → Profile → Log
```

Do NOT rush to search. The profile doc is the product — it makes every future session better.

## Phase 1: Interview

Ask questions conversationally, not like a form. Cover these dimensions. Skip what the user already volunteered.

### Must-know dimensions

| Dimension | Ask | Why |
|-----------|-----|-----|
| **School** | Which school? (NUS / NTU / SMU / SUTD / SIM / Kaplan / NAFA / other) | Determines reasonable commute areas |
| **Budget** | Monthly budget? Is this all-in or rent only? | Biggest filter. If they say "$2500 all-in", rent budget is actually ~$2000 after utilities |
| **Move-in timeline** | When do you need to move in? | Urgency changes strategy. "Next week" vs "in 2 months" |
| **Flatmates** | Renting alone or with friends? How many? | Alone = studio/1br. Friends = more bedrooms needed, but also coordination complexity |
| **Property type** | Know the difference between HDB and Condo? | If no → educate (Phase 2) |

### Nice-to-know dimensions

| Dimension | Ask |
|-----------|-----|
| Cooking habits | Need a kitchen? Many HDB rooms ban cooking |
| Guest policy | Expect overnight guests often? |
| Noise tolerance | Prefer quiet or don't mind lively? |
| Furnished needs | Need fully furnished or have your own stuff? |
| Pets | Bringing any? (Most HDBs ban pets, many condos too) |

### Dealbreaker discovery

Ask: "Is there anything that would make you reject a place immediately?"

Common dealbreakers: no aircon, ground floor, west sun (afternoon heat), no windows in bedroom, too far from MRT, no kitchen access, landlord stays, no visitors allowed.

## Phase 2: Educate

When the user shows uncertainty about a topic, explain BEFORE moving on. Don't dump all knowledge — only the parts relevant to their situation.

### If they don't know HDB vs Condo

Explain:
- HDB = public housing, cheaper, but has foreigner quota and minimum 6-month lease
- Condo = private, pool/gym/security, more expensive, minimum 3-month lease
- HDB room rental = cheapest option (~$800-1500), but live with landlord/owner
- Master room = has own bathroom. Common room = share bathroom

### If budget seems unrealistic

If a student says "$1500 for whole unit in Clementi" — gently correct:
- Whole HDB 2br in Clementi: $2,500-3,200
- Master room in shared HDB: $1,200-1,800
- Common room: $800-1,300
- Condo 1br: $3,000+

Show them the realistic tier for their budget.

### If they don't know the rental process

Walk through the 7-step flow briefly:
1. Search & shortlist
2. WhatsApp agent / view unit
3. Letter of Intent (LOI) + good faith deposit (~$500-1000)
4. Tenancy Agreement (TA) signing
5. Stamp the lease (~0.4% of total rent)
6. Move in + photo everything
7. Set up SP Services (utilities)

### Key pitfalls to mention (based on their situation)

- **Diplomatic clause**: Must have in TA. Lets you break lease if you lose student pass / leave Singapore.
- **Agent fee**: Tenant normally does NOT pay for HDB. For condo, sometimes half-month. Always ask.
- **"Walk to MRT"**: Verify with the `mrt` field in search results — it shows real distance.
- **Stamp duty**: Legally required. ~$72 on a $3000×24mo lease.
- **Aircon servicing**: Tenant pays. Every 3 months. ~$30-50/unit.
- **Security deposit**: 1 month for 1yr lease, 2 months for 2yr. Photo everything on move-in.

### Red flags to teach them

- "Cozy" = tiny. "Vibrant" = noisy.
- Listing up 3+ months = something's wrong
- No bathroom/kitchen photos = hiding something
- Price way below market = there's a catch
- "Urgent! Must rent this week" = pressure tactic

## Phase 3: Generate profile doc

After the interview, write a structured markdown file to `record/profile/<name>.md` in the repo root. This file is the single source of truth for this user's preferences.

### Profile template

```markdown
# <Name>'s Rental Profile

Generated: <date>
Last updated: <date>

## Basics

- School: <school>
- Budget: S$<amount>/mo (<rent-only or all-in>)
- Move-in timeline: <when>
- Flatmates: <count> (<names if relevant>)

## Preferences

- Property type: <hdb/condo/either/hdb-room>
- Preferred areas: <areas with reasons>
- Furnished: <yes/no/either>
- Cooking: <yes/no/light>
- Guests: <yes/no/occasional>

## Dealbreakers

- <list>

## Additional notes

- <anything else from the conversation>
```

Save to `<repo-root>/record/profile/<name-slug>.md`.

## Phase 4: Append to learning log

After the session, append to `record/profile/log.md` in the repo root. This is **append-only** — never delete or rewrite.

### Log entry template

```markdown
## <YYYY-MM-DD HH:MM> — <session summary in one line>

### Context
- What the user was looking for this session
- Any new constraint or preference discovered

### Decisions
- What areas were searched
- What filters were used

### Feedback
- What listings the user liked (with brief why)
- What they rejected (with why)
- Any course corrections ("Actually I want...")

### Learnings
- New preference discovered
- New dealbreaker identified
- Anything that surprised the user
```

If nothing to log (just browsing), write a single line: `## <YYYY-MM-DD> — Browsing. No new signals.`

### How to use the log

At the start of every search session, read `record/profile/log.md` (last 100 lines). Look for:
1. Preferences that evolved (e.g. "started wanting condo, now open to HDB")
2. Areas the user consistently liked or rejected
3. Any unresolved questions from last session

Use these signals to make smarter suggestions without re-asking.

## Important rules

- The profile is read-only reference. Update it only when the user explicitly says something changed ("I actually want condo now").
- The log is append-only. Every session gets one entry minimum.
- If `record/profile/` directory doesn't exist, create it.
- If `record/profile/log.md` doesn't exist, create it with a `# Learning Log` header.
- Do NOT dump the entire knowledge base on the user. Answer what they ask, mention what's relevant to their situation.
