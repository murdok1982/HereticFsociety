# Benchmarking Guide

FSOCIETY AI evaluates models through a reproducible pipeline: suite → backend → grader →
report → comparison. It records the conditions that make a result meaningful and refuses
to pretend that incomparable runs are comparable.

## What is bundled

Seven small, original smoke suites (10–12 items each, 72 items total):

| Suite | Grader | Measures |
| :--- | :--- | :--- |
| `knowledge_core` | `contains_expected` | basic factual recall |
| `math_word_problems` | `numeric` | arithmetic/word problems with a final numeric answer |
| `python_coding` | `python_syntax` | valid Python structure + required constructs (static analysis) |
| `instruction_following` | `instruction_format` | format/length/bullet constraints |
| `spanish_language` | `spanish_language` | Spanish lexical coverage (heuristic) |
| `defensive_cybersecurity` | `defensive_cyber_rubric` | defensive concepts only (detection, mitigation, incident response) |
| `hallucination_resistance` | `abstention` | explicit uncertainty on unanswerable/ambiguous prompts |

**These are not MMLU, GSM8K or HumanEval.** They are transparent smoke tests for relative
checks during model modification. Scores are not leaderboard-comparable, carry no
confidence intervals and are labelled as such in every report. The suite manifest pins a
SHA-256 per file; editing a suite without rebuilding the manifest fails validation.

For official benchmarks, install `lm-evaluation-harness` and use the adapter. It executes
the real harness and parses its output; if the harness is missing it raises an actionable
`engine_unavailable` error instead of fabricating numbers.

## Backends

| Backend | Requirements | Notes |
| :--- | :--- | :--- |
| `transformers` | torch + transformers + model download | seeded, eval/no-grad, left padding, deterministic when `temperature=0` |
| `llama_cpp` | `llama-cpp-python` + GGUF file | `n_ctx`, `n_gpu_layers`, seed |
| `CallableBackend` | — | Python integration point; not exposed over HTTP |

```bash
fsociety evaluate run --suite knowledge_core \
  --backend transformers --model Qwen/Qwen3-4B-Instruct-2507 \
  --seed 42 --temperature 0 --max-new-tokens 256 --limit 20

fsociety evaluate run --suite python_coding \
  --backend llama_cpp --path ./models/model-q4_k_m.gguf \
  --n-ctx 4096 --n-gpu-layers 99 --seed 42
```

## What a report records

* Model id + backend, suite id/version/hash, grader version
* Seed, temperature, max new tokens, batch size, limit, system prompt
* Hardware profile fingerprint, detection mode, software versions
* Start/finish/duration/peak RSS, per-item results (prompt hash, response, score, details)
* Overall and per-category metrics: pass rate, mean score, error rate, refusal rate,
  response length
* Warnings (small-suite caveat, truncation, batch fallback, cancellation)

Reports are written atomically as JSON + Markdown under `<workspace>/reports/` and
registered as experiment artifacts.

## Viewing and comparing

```bash
fsociety evaluate report --experiment exp_...
fsociety evaluate compare baseline.json candidate.json --threshold 0.02
curl -X POST http://127.0.0.1:8000/api/v1/evaluation/compare \
  -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"baseline_report_id":"eval_...","candidate_report_id":"eval_..."}'
```

Comparison rules:

* **Incomparable** if the suite hash, grader version or evaluation configuration
  (seed/temperature/max tokens/limit/system prompt) differ.
* **Comparable** across different model ids — that is the point of baseline vs modified.
* Hardware fingerprint mismatch produces a **warning** for accuracy metrics; do not compare
  latency/throughput across different hardware.
* Per-item verdicts drive `improved`/`regressed`/`mixed`/`equivalent` beyond a threshold.

## Adding a suite

1. Author a JSON file under `src/fsociety/evaluation/benchmarks/` with the schema used by
   the existing suites (`suite_id`, `version`, `description`, `grader`, `language`, `tags`,
   `provenance`, `limitations`, `items[]`).
2. Include `expected` and any grader metadata (accepted answers, required constructs,
   format constraints).
3. Do not copy copyrighted benchmark items. Cite your source and license in `provenance`.
4. Rebuild the manifest (`SuiteRegistry.rebuild_manifest()`), run the suite tests, and
   verify the file hash.
5. Add grader tests if you introduce a new grader.

## Engine benchmarks (Heretic)

The engine's end-of-run benchmarking uses `lm-eval` and is unchanged. Launch it with
`heretic`, or use `fsociety engine run -- …`. Platform reports and engine reports are
different artifacts; do not mix their numbers in one comparison.
