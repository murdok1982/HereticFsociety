# Optimization Guide

FSOCIETY AI uses **Optuna** (the same library the Heretic engine uses) with explicit
seeds and full trial persistence. Two distinct things are called "optimization" here:

1. **Engine optimization** — Heretic's own trial loop that searches ARA/abliteration
   parameters against scorer plugins. Unchanged; run it with `heretic` or
   `fsociety engine run -- …`.
2. **Platform optimization** — the `fsociety` optimizer: a declarative search space,
   seeded sampler, SQLite persistence and, through the API/CLI, a fixed analytical
   resource-planning objective. This guide covers (2).

## Search space

```json
{
  "dimensions": [
    {"name": "dtype", "kind": "categorical", "choices": ["bfloat16", "float16"]},
    {"name": "quantization", "kind": "categorical", "choices": ["none", "bnb_4bit"]},
    {"name": "batch_size", "kind": "int", "low": 1, "high": 32, "step": 1},
    {"name": "sequence_length", "kind": "categorical", "choices": [2048, 4096, 8192]},
    {"name": "learning_rate", "kind": "float", "low": 1e-5, "high": 1e-3, "log": true}
  ]
}
```

Rules: unique names, `int`/`float` require bounds, `log` requires positive bounds,
`categorical` requires choices. The CLI/API resource objective requires at least one of
`dtype`, `quantization`, `batch_size`, `sequence_length`.

Presets exist for Python callers: `SearchSpace.preset("lora_rank")`,
`preset("modifier")`, `preset("runtime")`, plus `default_space()`.

## Running via CLI

```bash
fsociety optimize run --model ./models/my-model --space space.json \
  --trials 30 --seed 42 --sampler tpe
```

Output: experiment id, best parameters, best estimated bytes and fit status. Every trial
is persisted:

```bash
fsociety experiments trials exp_...
```

## Running via Python (arbitrary objective)

```python
from fsociety.config import PlatformSettings
from fsociety.experiments.store import ExperimentStore
from fsociety.optimizer import Dimension, OptimizationRequest, Optimizer, SearchSpace

store = ExperimentStore("experiments.sqlite3")
space = SearchSpace(dimensions=[Dimension(name="x", kind="float", low=-5, high=5)])

def objective(params):
    value = (params["x"] - 0.25) ** 2
    return [value], {"x": params["x"]}

result = Optimizer(store=store, settings=PlatformSettings()).run(
    OptimizationRequest(name="demo", space=space, n_trials=25, seed=7),
    objective,
)
print(result.best_params, result.best_values)
```

The objective may return a float, a list of values, or `(values, metadata)`. Multiple
values require matching `directions`. Exceptions are recorded as failed trials and do not
abort the study unless every trial fails.

## Determinism

Identical seed + space + deterministic objective produce an identical sequence of
suggested parameters. This is covered by tests. Determinism does not extend to objectives
that read time, randomness or hardware state.

## Resource planning objective

The API/CLI objective computes, for each candidate configuration:

* `estimated_total_bytes` (weights + KV cache + activations + overhead)
* `does_not_fit` (0/1 against the detected memory budget)

Both are minimized. The result is a **plan**, not a measurement. Read the assumptions
returned with every estimate and validate the chosen configuration on the target machine
before a long engine run.

## Constraints and hardware awareness

`optimizer.planner` provides:

* `plan(model_info, hardware, …)` → `ResourceEstimate`
* `validate_configuration(estimate)` → violations, recommendations, utilization
* `recommend_execution_plan(...)` → dtype/quantization/batch/device + rationale
* `recommend_search_space(model_info, hardware)` → bounded space
* `merge_plan_with_params(plan, params)` → config with explicit overrides

When the model does not fit, the planner collapses to batch 1 and proposes quantization;
it never claims that a configuration will run.

## Reading results

* `best_values[0]` is the estimated total memory in bytes; `best_values[1] == 0` means it
  fits.
* Trial states: `complete`, `failed`, `pruned`, `running`.
* Cancellation is cooperative; already completed trials remain persisted.
* Timeout stops the study and records `stop_reason="timeout"` when at least one trial ran.

## Anti-patterns

* Treating estimates as benchmarks. They are not.
* Optimizing memory with a space that does not vary memory-relevant parameters (rejected).
* Reusing an old study while changing grader/suite semantics; record a new experiment.
* Comparing trials run under different hardware fingerprints as if conditions matched.
