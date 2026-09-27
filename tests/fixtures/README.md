# Regression inputs

The 6 JSON files under `search/` are extracted byte-for-byte from the historical synthetic data package at commit `a72653f` before cleanup,
and are used for boundary, pagination, timeout, and session tests; they do not represent the current real listings. The generator and the unused large-batch outputs have been removed.

`decision_routes.json` retains all 9 `decide_next` cases from the interface review samples in the same commit,
with inputs and expected values identical item by item; they are executed by `test_decide_next.py`. The remaining uncalled documentation samples and signature stubs have been deleted.

For the sources, normalization rules, and real search records of `refactor/`, see its [documentation](refactor/README.md).
