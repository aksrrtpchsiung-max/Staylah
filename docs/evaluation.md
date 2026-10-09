# Recommendation evaluation

The evaluation stage ranks the candidates returned by search, prepares a recommendation, and checks its supporting evidence. The code calls this stage **C**. Search is **B**, and requirement collection is **A**.

The public function facade is [evaluation/service.py](../property_agent/evaluation/service.py). [PartCEvaluationModule](../property_agent/decision/module_c.py) connects it to the decision graph.

## Candidate handoff

`screen(listings, profile)` is a compatibility wrapper. It validates inputs and deduplicates listing keys, then puts those keys in `ScreenResult.eligible`. It does not test hard conditions or reject unsuitable listings.

The field name `eligible` should therefore be read as a candidate handoff, not a guarantee of suitability. Missing or conflicting evidence from search still matters when interpreting recommendations.

## Ranking

`retrieve(query, eligible_listings, top_k, ctx)` scores and ranks the supplied candidates against the requirements. It accepts at most 12 unique candidates. Model scores are validated before they are used.

If model scoring is unavailable or invalid, retrieval uses structured requirement scoring and returns `partial` with `MODEL_UNAVAILABLE`. The fallback method is recorded in the result.

Implementation: [retrieval.py](../property_agent/evaluation/retrieval.py).

## Recommendation draft

`evaluate(...)` consumes the confirmed profile, retrieval result, candidate handoff, listing snapshot, coverage, and routing policy.

The built-in model adapter selects up to `min(display_limit, 10)` listings in retrieval order. The model generates a summary and limitations and suggests a supported next action. Python validates the action against candidate counts and available continuation searches. Factual recommendation claims are constructed from listing data and evidence references.

The result contains:

- `recommendation.ordered_items`, `summary`, and `limitations`;
- `assessment.next_action` and `next_reason_code`;
- a `search_directive` when further search is available.

A model failure falls back to deterministic selection and routing, marked `partial` with `MODEL_UNAVAILABLE`. Enough candidates for the routing policy does not mean every candidate has been independently verified against every hard condition.

Implementation: [evaluation.py](../property_agent/evaluation/evaluation.py) and [review_model.py](../property_agent/evaluation/review_model.py).

## Evidence review

`review(...)` checks the recommendation draft against the profile and listing snapshot.

Code checks versions, listing keys, recommendation counts, ranks, evidence references, and evidence age. The semantic model review looks for unsupported claims, unknown facts presented as certain, exaggerated summaries, and missing limitations. The model may only refer to the review targets and issue categories provided to it.

Review can remove unsupported claims, replace an exaggerated summary, and add limitations directly to the draft. Issues remain in the review result after corrections.

`passed=true` means the resulting draft has no blocking review issues. It does not prove that every housing requirement is satisfied. It also does not mean semantic review ran successfully: when the model is unavailable, deterministic checks can still produce `passed=true` alongside a partial result.

Implementation: [review.py](../property_agent/evaluation/review.py).

## Routing

`decide_next(state, policy)` is deterministic. It checks cancellation, stale profile versions, deadlines, source failures, and review state before acting on the evaluation suggestion.

| Action | Meaning |
| --- | --- |
| `publish` | Return the recommendation |
| `research` | Continue searching with a valid directive and remaining budget |
| `ask_user` | Present a pending follow-up question |
| `finish` | End the round without further work |
| `stop` | Stop because of cancellation, an error, or invalid state |
| `repair` | Compatibility route for a review implementation that requests repair |

If a proposed action cannot run, routing chooses an available fallback. The production review normally corrects the draft itself rather than requesting a separate repair round.

Implementation: [routing.py](../property_agent/evaluation/routing.py). For user replies and checkpoint resumption, see [Follow-up integration](clarification-integration.md).

## Tests and evaluation

```bash
.venv/bin/python -m unittest tests.test_part_c_integration tests.test_decide_next
```

The [evaluation runner](../evaluation_suite/README.md) records live results for manual quality assessment. Candidate counts alone are not recall or recommendation accuracy.
