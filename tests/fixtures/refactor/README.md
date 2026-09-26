# Refactor regression fixtures

The original application baseline is Git commit `41f86d9`.

- `behavior.json`: original Decision graph and C fallback outputs captured with
  `tests/refactor_scenarios.py`. The answer “不用了” is interpreted by the original
  stub flow as a general answer/handoff; `resume_answer` preserves that behavior.
  Only durations are zeroed and the in-process `__interrupt__` wrapper is omitted;
  the pending question payload, identifiers, evidence and ordering remain.
- `source_fingerprints.json`: SHA-256 of original function/class ASTs; only import
  paths are canonicalized by `test_refactor_source_equivalence.py`.
- `frontend_fingerprints.json`: SHA-256 of verbatim sections of the original app.js.
- `contract-schema.json`: all 55 TypedDict fields and required keys captured before
  removing the root alias, so schema checks do not depend on a duplicate contract.
- `module-map.json`: machine input for retired-path and AST checks. The only
  human-maintained module correspondence table is [模块对应表](../../../模块对应表.md).
- `live_search.json`: the request/context and SearchResult from the real recording
  `evaluation_runs/test_all_20260924_133304/01_request-nus_bedroom_rent_input.json`
  and `01_request-nus_bedroom_rent_output.json`. All 12 public PropertyGuru listings
  and their observations are preserved. The historical evaluation_runs directory
  was local/ignored and has been removed after extraction; the input needed by regression is committed here.
- `live_fulfillment_expected.json`: result of replaying the recorded request and
  search output through the original `part45.requirements.build_fulfillment`;
  only duration_ms is zeroed. This is a historical replay, not current availability.

Intentional future product changes should update the relevant assertions and
explain changed baseline expectations; do not blindly regenerate snapshots to
make a failing test pass.
