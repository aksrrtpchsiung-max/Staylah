# /check

Review a rental contract (Tenancy Agreement / Letter of Intent) against Singapore standard practice. Flag missing clauses, dangerous terms, and anything that deviates from what's normal. Generate a structured report saved alongside the target listing.

## When to activate

- User says "帮我看看合同" / "check this lease" / "review this TA"
- User uploads a PDF, image, or pastes contract text
- User received a draft LOI/TA from an agent and wants a second opinion
- User asks "is this clause normal?"

## What you need

Ask the user for:
1. **The contract** — PDF, screenshot, or text paste
2. **Which listing** — if it's for a target already in `record/targets/<id>.md`, read that first for context (price, property type, agent info)

If the user hasn't done `/onboard` yet, also ask about:
- Student pass status (diplomatic clause needs this)
- Whether they're renting whole unit or room (different norms)

## Phase 1: Extract all clauses

Read the contract thoroughly. Extract every clause into structured form. Don't skip the fine print.

## Phase 2: Check against Singapore norms

For each category below, compare what the contract says against what's standard in Singapore.

### Must-have clauses (RED if missing)

| Clause | Standard | Why it matters |
|--------|----------|---------------|
| **Diplomatic Clause** | Can terminate after 12-14 months with 2 months notice if: (a) job transfer out of SG, (b) student pass expires/cancelled, (c) death | Without this, you're stuck paying even if you leave Singapore. **This is the #1 thing international students get burned by.** |
| **Security Deposit** | 1 month rent for 1-year lease, 2 months for 2-year. Must be returned within 7-14 days after lease ends, subject to deductions for damage only (not "fair wear and tear") | Some landlords try to keep deposit for repainting/cleaning. This is NOT normal unless you caused actual damage. |
| **Lease Period** | Typically 12 or 24 months. HDB minimum 6 months. Condo minimum 3 months. | If the term is unusual (e.g. 18 months), ask why. |
| **Notice Period** | Usually 1-2 months. Should be symmetric (both sides). | If landlord can give 1 month but you need 3, that's unfair. |
| **Termination by Landlord** | Only for: non-payment (14+ days), breach of terms, or diplomatic clause trigger | If landlord can terminate anytime "at their discretion", that's a huge red flag. |

### Common unfair clauses (YELLOW flag)

| Clause | Normal | Flag if |
|--------|--------|---------|
| **Aircon servicing** | Tenant pays for quarterly servicing (~$30-50/unit). Major repairs (>$150) paid by landlord. | If contract says tenant pays for ALL aircon repairs/replacement |
| **Minor repairs** | Tenant pays for small items <$100-150. Landlord pays for major repairs, appliances, structural. | If tenant is responsible for "all repairs and maintenance" |
| **Cooking** | "Light cooking" allowed in most HDB. Full cooking may be restricted in rooms. | "No cooking at all" or "cooking only with landlord present" |
| **Guests/Overnight** | Occasional guests are normal. | "No visitors" or "no overnight guests ever" |
| **Landlord access** | With 24-48h notice for inspections/repairs. | "Landlord may enter at any time without notice" |
| **Rent increase** | Fixed for lease duration. | Contract allows mid-lease increase |
| **Inventory list** | Should be attached to TA. Photos recommended. | No inventory list = dispute risk on move-out |
| **Stamp duty** | Tenant pays. ~0.4% of total rent. Should be mentioned. | If not mentioned, confirm who pays (should be tenant, but some landlords offer to split) |

### HDB-specific checks

- [ ] **Minimum Occupation Period (MOP)** — Is the HDB's MOP fulfilled? HDB cannot be fully rented out during MOP.
- [ ] **Foreigner quota** — Some HDB blocks have a quota on non-Malaysian foreign tenants. Agent should verify before signing.
- [ ] **HDB subletting approval** — Landlord must get HDB approval for subletting whole flat. Ask if it's been obtained.
- [ ] **Room rental rules** — HDB room rental has occupancy caps (max 2 tenants per room for 3-room flats, etc.)

### Condo-specific checks

- [ ] **MCST rules** — Condo management may have additional rules (moving in/out hours, facility usage). Ask for a copy.
- [ ] **Maintenance fee** — Landlord pays. Confirm it's included in rent.
- [ ] **Facility access** — Are gym, pool, BBQ pit, function room included? Some condos restrict tenants from certain facilities.
- [ ] **Parking** — Included or extra?

### LOI-specific checks

If this is a Letter of Intent (not the full TA yet):

- [ ] **Good faith deposit** — Standard: $500-1000. Must state it's refundable if TA not signed due to landlord OR tenant reasons.
- [ ] **Option period** — How long does landlord have to accept? Usually 3-7 days.
- [ ] **LOI is not binding** — LOI is an offer, not a contract. The TA is the actual contract. Don't let the agent tell you otherwise.

## Phase 3: Cross-reference with listing info

If the listing is in `record/targets/<id>.md`, cross-check the contract against what was advertised:

- [ ] **Rent amount matches** the listing
- [ ] **Furnishing level matches** (if listing said "fully furnished", contract should say so)
- [ ] **Property type matches** (HDB/Condo/Room)
- [ ] **Any additional fees** not mentioned in the listing? (maintenance, parking, etc.)

## Phase 4: Generate report

Write the report to `record/targets/<id>/contract-check.md` (if target exists) or `record/profile/contract-check-<date>.md` (if standalone).

### Report template

```markdown
# Contract Review: <listing title>

- Reviewed: <YYYY-MM-DD>
- Contract type: LOI / TA
- Property: <address>, <type>
- Rent: S$<amount>/mo, <lease period>

## Risk Summary

🔴 Critical: <count> | 🟡 Warning: <count> | 🟢 Pass: <count>

## Critical Issues (must fix before signing)

### 🔴 Missing Diplomatic Clause

**What it says**: (not found in contract)
**Why it matters**: As an international student, if your student pass expires or is cancelled, you're legally bound to pay the remainder of the lease. This is the single most important clause for foreign tenants.
**Fix**: Request: "The Tenant may terminate this Agreement by giving 2 months' written notice if: (a) the Tenant's Student's Pass is cancelled or not renewed, or (b) the Tenant is required to leave Singapore by the relevant authorities."

### 🔴 <next issue>

...

## Warnings (negotiate)

### 🟡 Security deposit return: 30 days

**What it says**: "Deposit shall be refunded within 30 days after lease expiry"
**Standard**: 7-14 days
**Fix**: Ask to change to 14 days. This is a reasonable request.

...

## Passed (looks fine)

- 🟢 Lease period: 24 months (standard)
- 🟢 Aircon servicing: tenant pays quarterly servicing, landlord pays repairs (standard)
- 🟢 Notice period: 2 months, symmetric (fair)

## Missing from this contract

These are clauses that SHOULD be in a Singapore TA but aren't in yours:

- [ ] Inventory list not attached (will cause disputes on move-out)
- [ ] No mention of stamp duty responsibility
- [ ] No clause about renewal terms

## Questions to ask agent before signing

1. "Can you add the diplomatic clause? Here's the wording: <suggested text>"
2. "Can the deposit return period be shortened to 14 days?"
3. "Can you attach an inventory list before signing?"

## Overall Verdict

**Proceed with caution.** <1-2 sentences summary>

The diplomatic clause addition is non-negotiable. The other issues (deposit timeline, inventory list) are common fixes. Don't sign until at least the diplomatic clause is added.
```

## Phase 5: Follow-up after fixes

If the user negotiates changes and gets a revised contract, offer to re-check. Focus on:
- Did the critical issues get fixed?
- Did the agent change anything ELSE while adding the requested clause? (Some agents slip in new unfavorable terms when revising)

## Important rules

- **Diplomatic clause is non-negotiable for foreign students.** If it's missing, flag it as critical regardless of anything else.
- **Don't give legal advice.** Say "this deviates from standard Singapore practice" not "this is illegal."
- **If something is very unusual**, recommend the user consult a proper tenancy lawyer. This skill catches common issues, not edge cases.
- **Always save the report** to the target directory so it's there when the user negotiates.
- **Read the user's profile and target info** before reviewing — context makes the review smarter.
