<div align="center">

<a href="https://github.com/jackwener/OpenCLI"><img src="https://img.shields.io/badge/powered%20by-opencli-2563eb?style=flat-square" alt="powered by opencli"></a>
<img src="https://img.shields.io/github/license/KehaoC/can-rent-lah?style=flat-square" alt="license">
<img src="https://img.shields.io/badge/region-Singapore%20%F0%9F%87%B8%F0%9F%87%AC-red?style=flat-square" alt="singapore">
<img src="https://img.shields.io/badge/PRs-welcome-brightgreen?style=flat-square" alt="PRs welcome">

</div>

# can-rent-lah 🏠🇸🇬

> *Can rent lah, don't worry.*

**can-rent-lah** is an AI agent toolkit for renting in Singapore. It turns [PropertyGuru](https://www.propertyguru.com.sg) into a CLI, then layers agent skills on top to handle the entire rental lifecycle — from "I don't even know which district to look in" to "the contract is signed and stamped."

Built on [opencli](https://github.com/jackwener/opencli) by [@jackwener](https://github.com/jackwener). Made for international students. Works for anyone.

---

## Why?

Singapore's rental market is brutal for newcomers. Thousands of listings, agents who only speak WhatsApp, contracts full of clauses you've never heard of, and everyone asking "so when can you view?"

can-rent-lah gives your AI agent the tools to handle this end-to-end:

| Without can-rent-lah | With can-rent-lah |
|---------------------|-------------------|
| Manually browse PropertyGuru for hours | Agent searches 3 areas in 30 seconds |
| Wonder if $2,500 is reasonable for Clementi | Agent benchmarks against market data |
| "What even is a diplomatic clause?" | Agent explains and checks if it's in your contract |
| "Cozy room" turns out to be a broom closet | Agent flags "cozy" = tiny before you waste a viewing |
| Juggle 5 WhatsApp conversations with different agents | Agent tracks every conversation per listing |
| Read a 20-page TA and hope nothing's wrong | Agent audits every clause against SG norms |

## Installation

```bash
# 1. Install opencli
npm install -g @jackwener/opencli

# 2. Install the Chrome extension
opencli doctor    # will prompt you to install Browser Bridge if missing

# 3. Clone and install adapters
git clone https://github.com/KehaoC/can-rent-lah.git
mkdir -p ~/.opencli/clis/propertyguru
cp can-rent-lah/cli/propertyguru/*.js ~/.opencli/clis/propertyguru/

# 4. Verify
opencli propertyguru --help
```

**Requirements**: Node.js ≥ 20, Chrome with [opencli Browser Bridge](https://chromewebstore.google.com/detail/opencli/ildkmabpimmkaediidaifkhjpohdnifk) extension.

## Agent Skills

Four skills covering the full rental lifecycle. Works with Claude Code, Codex, Cursor, or any agent that reads SKILL.md files.

### `/onboard` — Understand your needs

The agent interviews you about your school, budget, timeline, and preferences. Explains Singapore rental concepts only when you need them (HDB vs Condo? Master room vs Common room?). Outputs a structured profile to `record/profile/<name>.md` so every future session remembers who you are.

### `/rough-screening` — Find and rank properties

Reads your profile. Searches multiple areas. Evaluates every listing against your criteria — MRT distance, price reasonableness, red flags. Presents a human-language ranked list (🥇 🥈 🥉) with specific pros, cons, and direct links.

### `/contact` — Communicate with agents

Extracts agent contact details from listings. Generates clickable WhatsApp links with pre-filled messages in English. Manages a per-listing communication log at `record/targets/<id>.md` — status tracking, question checklists, viewing notes. Every conversation, nothing lost.

### `/check` — Review your contract before signing

Upload a TA or LOI. The agent extracts every clause and checks it against Singapore standard practice:

| Severity | Meaning |
|----------|---------|
| 🔴 Critical | Don't sign until this is fixed (missing diplomatic clause, unfair termination) |
| 🟡 Warning | Negotiate (deposit return timeline, repair responsibilities) |
| 🟢 Pass | Standard practice |

HDB-specific checks (MOP, foreigner quota, subletting approval). Condo-specific checks (MCST rules, facility access). Generates a structured report saved to `record/targets/<id>/contract-check.md`.

## CLI Commands

```bash
# Search listings
opencli propertyguru search "clementi" --max 3000 --bedrooms 2 --type hdb -f json

# Get full listing details
opencli propertyguru detail 500149207 -f json

# Save a listing
opencli propertyguru shortlist 500149207

# Get agent contact + WhatsApp link
opencli propertyguru contact 500149207 -f json
```

### `search <location> [options]`

| Flag | Description |
|------|-------------|
| `<location>` | Area, district, MRT station (required) |
| `--max <n>` | Max monthly rent (SGD) |
| `--min <n>` | Min monthly rent (SGD) |
| `--bedrooms <n>` | Number of bedrooms |
| `--type <type>` | `hdb`, `condo`, `apartment`, `landed`, `semi-d`, `terraced`, `detached`, `bungalow`, `executive-condo`, `walk-up`, `studio` |
| `--listing <type>` | `rent` (default) or `sale` |
| `--limit <n>` | Max results (default 20) |

### Falcon integration: normalized search records

The `propertyguru search` adapter now returns a complete, Falcon-compatible
`Listing` object for every search-card result. It always emits the same nested
keys (`price`, `attributes`, `evidence`, and lifecycle fields), instead of the
previous 12-field summary.

- Values visible on the search card, such as price, bedrooms, bathrooms, floor
  area, property type, and listing date, are normalized where reliable and
  accompanied by source evidence.
- A fact that the search card does not establish is `null`; required categorical
  values use the Falcon contract's `unknown` value. The adapter never changes an
  unknown boolean into `false`.
- Search results set `fetched_at`, but begin with `listing_status: "unknown"`
  and `last_verified_at: null`. Appearing in a search page alone is not treated
  as confirmation that a listing remains available.

Do not fetch every detail page after a search. First screen the search-card
records by the hard constraints, then use `propertyguru detail <id>` only for
the shortlisted candidates and merge its evidence into the same listing.

### `contact <id>`

Returns: agent name, mobile number (+65), CEA license, and a clickable WhatsApp URL with a pre-filled message:

```
https://wa.me/6584874492?text=Hi Robin Phua,
I'm interested in 308 Clementi Avenue 4 (S$ 2,500 /mo).
Is it still available? When can I view?
```

One click → WhatsApp opens → message is ready → press send.

## How it works

PropertyGuru is a Next.js server-side rendered site. The CLI adapters navigate to search URLs with query parameters and extract structured data from `window.__NEXT_DATA__` — no API key, no scraping fragile DOM selectors, no CAPTCHA issues.

```
┌──────────┐     ┌──────────┐     ┌─────────────┐
│  Agent   │────▶│ opencli  │────▶│   Chrome     │
│  (Claude)│     │  daemon  │     │  (your tabs) │
└──────────┘     └──────────┘     └─────────────┘
     │                                  │
     │  read / write                    │  navigate, extract
     ▼                                  ▼
┌──────────┐                    ┌─────────────┐
│ record/  │                    │ PropertyGuru │
│ profile  │                    │   .com.sg    │
│ targets  │                    │              │
└──────────┘                    └─────────────┘
```

## Project structure

```
can-rent-lah/
├── cli/propertyguru/        # opencli adapters
│   ├── search.js            #   search listings
│   ├── detail.js            #   listing details
│   ├── shortlist.js         #   bookmark listings
│   └── contact.js           #   agent contact + WhatsApp link
├── skills/                  # agent skills
│   ├── can-rent-lah-onboard/    # /onboard
│   ├── can-rent-lah-rough-screening/  # /rough-screening
│   ├── can-rent-lah-contact/    # /contact
│   └── can-rent-lah-check/      # /check
├── record/                  # user data (gitignored)
│   ├── profile/             #   user profiles
│   ├── targets/             #   per-listing tracking
│   └── log.md               #   learning log
```

## FAQ

**Do I need a PropertyGuru account?**
No. Browsing and searching works without login. Shortlisting requires a free account.

**Do I need to pay for an API key?**
No. The adapters read data from the page itself — the same data your browser already downloads when you visit PropertyGuru.

**Does this work for buying property, not just renting?**
Yes. Use `--listing sale` to search properties for sale.

**Can I use this for other property sites?**
The adapter pattern works for any site opencli supports. 99.co, SRX, EdgeProp — same approach. PRs welcome.

**Is this legal?**
Yes. The adapters read publicly accessible data that your browser already receives. No scraping, no bypassing paywalls, no violating ToS.

## Credits

**Built on [opencli](https://github.com/jackwener/opencli) by [@jackwener](https://github.com/jackwener).**

opencli is the foundation that makes this possible — it turns any website into a CLI by connecting to your existing Chrome session. No API keys, no headless browsers, no scraping frameworks. The PropertyGuru adapters in this repo are opencli site adapters that follow the [opencli-adapter-author](https://github.com/jackwener/OpenCLI) conventions.

If you find can-rent-lah useful, give opencli a star too. It powers everything here.

---

<div align="center">

**Can or not? Can.**

Made with ❤️ for everyone trying to find a roof in the Little Red Dot.

</div>
