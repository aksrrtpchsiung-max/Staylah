# Evaluation runner

This runner records requirement clarification and full property-search conversations for inspection. It combines automatic checks with manual assessment; its logs are not a benchmark score by themselves.

## Datasets

| Suite | Cases | Scope |
| --- | --- | --- |
| `clarification` | A01–A10 | Ten incomplete requests, each passed through the requirements graph once |
| `full` | F01–F20 | Twenty requests with different numbers of housing conditions, run through the full workflow |

The datasets contain user requests and manual annotations, not property listings. Each case gets an independent conversation ID. Soft preferences are annotated separately from hard conditions.

The clarification suite needs a DeepSeek key and uses in-memory state. It does not construct search or evaluation services and does not need PostgreSQL.

The full suite needs the [live application setup](../README.md#run-with-live-data): PostgreSQL, DeepSeek, OneMap, and the PropertyGuru browser connection. It rejects configuration where `source_mode` is not `live`.

## Run

From the repository root:

```bash
.venv/bin/python -m evaluation_suite.runner --suite clarification
.venv/bin/python -m evaluation_suite.runner --suite full --case F01
.venv/bin/python -m evaluation_suite.runner --suite full
```

To run a range:

```bash
.venv/bin/python -m evaluation_suite.runner --suite full --start-at F03 --end-at F05
```

Use `--output` to change the output parent directory. To resume a full-suite run, pass its existing directory to `--resume-dir` and select cases that have not completed:

```bash
.venv/bin/python -m evaluation_suite.runner --suite full --start-at F03 \
  --resume-dir evaluation_runs/<existing-run-directory>
```

Replace the placeholder with the actual run directory. The runner refuses to overwrite completed cases. `--case` cannot be combined with `--start-at` or `--end-at`.

The full suite sends a confirmation after the requirements summary so the case can proceed to search. This is test automation, not verification that the parsed requirements are correct. If the workflow needs additional clarification or approval of changed conditions, the case stops in that state instead of inventing an answer.

## Output

Runs are written under the Git-ignored `evaluation_runs/` directory by default.

- Both suites write a JSON file per case and `summary.csv`.
- Clarification runs also write `dialogue.md`.
- Full runs write a Markdown file per case and update `progress.md` as cases complete.

Full traces include search attempts, listings, coverage, ranking, recommendation review, and timing. Inspect the parsed profile as well as the final answer. Logs may include personal information and real listing text; review them before sharing.

## Assessing results

| Measure | How to assess it |
| --- | --- |
| Clarification success | Check that the graph asks about at least one genuinely missing topic without creating a search handoff or guessing blocking conditions |
| Listing recall | Manually verify found listings and compare them with an independently established eligible reference set from the same time window and search scope |
| Recommendation accuracy | Divide recommendations meeting all hard conditions by the number actually recommended; use N/A when there are none |
| Ranking quality | Score eligible candidates from the same search run and compare their order with the recommendations, for example using nDCG@K |
| Processing time | Report stage durations and wall time separately, with per-case values, median, and P90 |
| Completion rate | Report cases that publish a final recommendation, separating clarification, source failure, and no-result outcomes |

All clarification cases are incomplete requests, so this suite does not measure unnecessary follow-up questions on already complete requests.

Search candidate counts are not eligible counts or recall. Evaluation does not independently rescreen all hard conditions, and unknown evidence should remain unknown during manual assessment. A recall estimate requires a separate reference set; the runner cannot calculate it from its own search results alone.

Record the model, search limits, and run time alongside results. Live listing availability and source failures can change outcomes between runs. This document describes the evaluation procedure and does not claim measured performance.
