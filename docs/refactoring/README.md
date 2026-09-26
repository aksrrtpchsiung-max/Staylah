# Behavior-preserving refactor

Baseline: `41f86d9` (the original application), Python 3.12.13, existing dependency versions.
The only human-maintained old/new module list is [模块对应表](../../模块对应表.md).
Final results and reproducible commands are in [acceptance.md](acceptance.md).

The implementation keeps HTTP payloads, prompts, ranking, graph node/state names,
checkpoint identifiers, database schema and configuration precedence. After the
user requested cleanup and live A/B/C passed, 45 old source aliases were removed.
A second cleanup removed unused tools and documents and retained only the fixture subset used by application regressions. Use canonical Python paths; web and orchestration startup commands are unchanged.

## Baseline calibration

The original suite ran 152 tests: 135 passed, 9 failed, 1 errored, and 7 required a
separate PostgreSQL database. The failures were reproduced before production edits.

| Existing expectation | Current implementation | Calibration |
| --- | --- | --- |
| Chinese question / fallback / room text | English text | Assert the existing English output; retain counts and factual checks |
| Display limit mentions eligible listings | Display limit mentions B candidates | Preserve order/count assertions and use the current wording |
| C.screen rejects expensive/unknown listings | C.screen is a deduplicating compatibility handoff | Assert every B candidate remains and no second screening occurs |
| Page one triggers research after C screening | Candidate count includes all B candidates | Start the pagination scenario with two B candidates |
| Short fixture implicitly produces a relaxation | Legacy stub no longer receives rejected rows | Supply an explicit C proposal and retain natural-language accept/resume assertions |
| Real C invents a relaxation proposal | Unsupported ask_user falls back to finish | Assert finish, unchanged profile, no proposals and disclosed fallback |

After calibration: 152 tests, 145 passed and 7 PostgreSQL tests skipped. No production
behavior was changed in the calibration commit. Existing mock examples are test
inputs, not evidence of real listing quality.

## Rollback

Each stage is a separate commit on `codex/behavior-preserving-refactor`. Revert the
affected stage (and dependent later stages) to roll back. No database migration is
introduced. The baseline remains available at `41f86d9`; do not reset an active
working tree containing unrelated edits.

## Verification

Run `python -m unittest discover -s tests -q` for offline regression. PostgreSQL
checks require an isolated database via `TEST_DATABASE_URL`, never the daily-use
database. Browser and real-source results are documented in the acceptance report.
