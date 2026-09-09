# Pydantic Evals: architecture and reuse review

Reviewed **2026-09-08** against [our level-2 proposal](../LLD_LEVEL_2.md) and
[public contract](../contracts/agent_eval_flow.pyi). This is source inspection,
not an integration experiment. Upstream documentation and `main` are moving
references; pin and test a released version before choosing a dependency.

## Decision in brief

**Recommendation:** reuse Pydantic for typed validation and serialization, and
prototype a Pydantic Evals integration before writing a general evaluation
runner. Keep our experiment identity, captured-run contract, evidence joins and
selection policy under our ownership. Calling everything a wrapper would hide
real work; calling the entire pipeline new would overstate it.

The key question is whether our proposed extra contracts materially simplify
the OpenSRE and OpenKritt integrations. Those remain separate studies. A useful
proof compares two versions of each agent and regrades the saved runs without
launching the agents again.

## What already exists

**Facts:** Pydantic Evals already exposes the simple experience we want:
`Dataset.evaluate(task)` returns an `EvaluationReport`. A case holds typed
inputs, optional expected output, metadata and case-specific evaluators. The
task can be a synchronous or asynchronous callable; `evaluate_sync` provides
the synchronous entry point. Options include bounded concurrency, repetitions,
task/evaluator retries and per-case lifecycle hooks. This is considerable
overlap with our facade and execution coordination.
([Dataset API](https://pydantic.dev/docs/ai/api/pydantic_evals/dataset/))

Custom evaluators receive input, output, expected output, metadata, duration,
attributes, metrics and an accessible span tree when recording is available.
They can return named scalar results with explanations, synchronously or
asynchronously. The implementation validates return values and records
evaluator failures separately. We should not suggest that extensible metrics
or evaluator-error records distinguish us by themselves.
([Evaluator API](https://pydantic.dev/docs/ai/api/pydantic_evals/evaluators/),
[output handling source](https://github.com/pydantic/pydantic-ai/blob/main/pydantic_evals/pydantic_evals/evaluators/_run_evaluator.py))

Report evaluators receive the whole experiment report and can produce scalar
or richer analyses. Therefore custom aggregation is also established work,
not a unique capability of our `SummaryReducer`.
([Report evaluators](https://pydantic.dev/docs/ai/evals/evaluators/report-evaluators/))

Datasets support JSON/YAML loading and saving, generated JSON Schema and
explicit registries for custom evaluator types. Pydantic's `TypeAdapter` also
validates and serializes ordinary dataclasses and other supported types; we
can use it without making our entire public API inherit `BaseModel`.
([Dataset serialization](https://pydantic.dev/docs/ai/evals/how-to/dataset-serialization/),
[TypeAdapter](https://pydantic.dev/docs/validation/latest/concepts/type_adapter/))

## What a wrapper would still have to implement

These are **mapping differences in inspected interfaces**, not claims that
upstream cannot be extended.

| Our requirement | Integration work we would still own |
| --- | --- |
| Candidate × task × repetition plan, stable IDs and unavailable assignments | Preserve assignment identity and reconcile every planned run, including imports. |
| `eval(runs=...)` preserves original capture | Convert existing runs into evaluation contexts and distinguish regrading overhead from original execution facts. |
| Native outputs, events, artifacts and partial retry costs | Capture and normalize evidence before reducing it to a score/report. |
| Metric dependencies and keyed diagnostic rows | Schedule dependent checks once, preserve task/detail grain and map outputs into our measurements. |
| Offline explanations, comparisons and preference changes | Persist sufficient evidence and summaries, then apply our result and selection contract. |

The inspected dataset runner executes task and evaluators together per case;
its ordinary evaluator list is gathered concurrently. It does not implement
our declared `depends_on` graph in that path. A wrapper could put our graph
inside one compound evaluator, but then our graph engine still exists beneath
the wrapper.
([Dataset source](https://github.com/pydantic/pydantic-ai/blob/main/pydantic_evals/pydantic_evals/dataset.py))

Span-based checks already support inspecting execution behavior, including
tool calls. The inspected `ReportCase` retains trace/span IDs; it does not have
a span-tree field. Its failure record retains exception information and trace
references, rather than our full partial-resource/evidence record. These are
reasons to specify an explicit evidence export/import mapping, not reasons to
claim upstream cannot inspect traces or preserve failures.
([Span evaluation](https://pydantic.dev/docs/ai/evals/evaluators/span-based/),
[report records source](https://github.com/pydantic/pydantic-ai/blob/main/pydantic_evals/pydantic_evals/reporting/__init__.py))

## Red-team attacks on our proposal

**1. We may rebuild commodity machinery before proving our own value.**
For a two-case tool test, a callable plus Pydantic Evals already runs checks and
returns inspectable results. Requiring users to construct numerous resource
and evidence records for that case would increase adoption cost. Add small
callable/evaluator adapters with honest defaults, including unknown resource
observations. Keep rich records available when the harness supplies them.

**2. Synchronous-only contracts shift avoidable complexity into integrations.**
Consider an asynchronous Vertex client used by a notebook or service with an
active event loop. Repeatedly creating loops inside adapters is a fragile
integration strategy. Pydantic Evals demonstrates a sync facade over an async
entry point. Decide our async extension seam before making the sync-only
protocol permanent; keep one implementation path.
([Execution API](https://pydantic.dev/docs/ai/api/pydantic_evals/dataset/))

**3. A successful conversion can silently destroy evidence.**
An OpenSRE run might capture a paid attempt and then time out. Converting only
its final exception into a report loses facts needed by any later cost metric.
The defect is missing evidence, irrespective of the user's scoring policy.
Require round-trip mapping checks for failed runs, unknown observations,
Decimal money and evidence locators before claiming compatibility.

**4. Reusing validation does not automatically satisfy our invariants.**
Pydantic documents that frozen models still permit mutation of contained
dictionaries. Our deep-immutability/fingerprint promise therefore requires
explicit normalization and nested immutable containers. Join integrity,
exclusive resource accounting and assignment coverage also remain our rules.
Avoid implementing a second general schema system to enforce those few rules.
([Pydantic immutability](https://pydantic.dev/docs/validation/latest/concepts/models/#faux-immutability))

## Practical build-versus-reuse recommendation

Prototype both options against the same existing toy contracts: Pydantic
Evals as the internal coordinator, and Pydantic evaluators behind our
`MetricEvaluator` seam. Choose by how much identity, evidence and failure
translation code remains, not by counting wrapped method names. Do not publish
a percentage before that experiment.

Borrow the public design qualities: a small entry point, typed contexts,
explicit custom evaluators, separate result records and optional integrations.
Keep our core responsible for portable experiment records and their joins;
reuse schema, asynchronous primitives and provider SDKs underneath.

One dependency check matters: the overview says `pydantic-evals` does not depend
on `pydantic-ai`, while the inspected `main` package declares a matching
`pydantic-ai-slim` dependency. Treat the selected release's metadata as the
authority for installation cost and isolation, rather than repeating a broad
dependency-free claim.
([Overview](https://pydantic.dev/docs/ai/evals/evals/),
[package metadata](https://github.com/pydantic/pydantic-ai/blob/main/pydantic_evals/pyproject.toml))
