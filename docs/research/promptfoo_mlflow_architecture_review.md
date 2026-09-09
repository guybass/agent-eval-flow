# Promptfoo and MLflow: architecture red-team review

Research date: **2026-09-08**. Scope: the proposed
[level-2 design](../LLD_LEVEL_2.md) and
[public contract](../contracts/agent_eval_flow.pyi). Upstream documentation and
source were inspected; no upstream integrations were executed. Links to `latest`
and `main` describe the reviewed state, not a pinned compatibility guarantee.

Our proposal is mostly a **new implementation of established evaluation
patterns**. Its useful product could be a consistent experiment and evidence
contract across different agent systems. Calling an agent, applying custom
checks, rescoring captured outputs and displaying results already exist.
The case for this library must be that these jobs become easier and more
trustworthy together for its users.

## What mature projects already separate

| Project | Observed architecture | Implication for our design |
| --- | --- | --- |
| Promptfoo | Configuration feeds an `evaluate` entry point. Providers produce responses; assertions grade them; the result record produces a summary. Functions can supply providers and assertions. | Our pipeline/backend/evaluator split is an established pattern. It is worth adopting without claiming invention. |
| MLflow GenAI | `evaluate(data, scorers, predict_fn=None)` accepts fresh predictions, precomputed outputs or traces. Scorers consume inputs, outputs, expectations and/or trace evidence. | Separation between execution and evaluation, including saved-run evaluation, is also established. |

Sources: [Promptfoo Node API](https://www.promptfoo.dev/docs/usage/node-package/)
and [MLflow evaluation source](https://mlflow.org/docs/latest/api_reference/_modules/mlflow/genai/evaluation/base.html).

Promptfoo's provider response includes output, error, token usage, cost, cache
status and arbitrary metadata. A provider can call an entire application, not
only an LLM. Its Python support crosses a Node/Python boundary. Python assertion
functions are another extension point. These are useful interoperability seams;
they do not make Promptfoo a native Python dependency with the same lifecycle
as our objects. Sources: [provider interface](https://www.promptfoo.dev/docs/providers/custom-api/),
[Python provider](https://www.promptfoo.dev/docs/providers/python/) and
[Python assertions](https://www.promptfoo.dev/docs/configuration/expected-outputs/python/).

MLflow scorers can return scalar values, rich `Feedback`, or several feedback
objects, and can inspect tool and subagent spans. Its evaluation result exposes
aggregated metrics, a row DataFrame and a run ID for retrieving traces. Sources:
[custom scorers](https://mlflow.org/docs/latest/genai/eval-monitor/scorers/custom/)
and [result access](https://mlflow.org/docs/latest/genai/eval-monitor/faq/#how-do-i-programmatically-access-evaluation-results).

## Attacks on our proposal

### 1. “Why would I adopt another evaluation engine?”

**Evidence:** the existing projects already expose the main execution/check/result
pattern. Promptfoo also documents trace, tool-trajectory and skill assertions;
MLflow evaluates stored traces without invoking a predictor.
[Promptfoo assertions](https://www.promptfoo.dev/docs/configuration/expected-outputs/),
[MLflow stored-trace evaluation](https://mlflow.org/docs/latest/genai/eval-monitor/running-evaluation/traces/).

**Attack:** an OpenSRE user with working Promptfoo evaluations sees six new
object names and four integration factories before receiving additional value.
“Evaluate full agents” alone cannot justify that migration.

**Recommendation:** prove one concrete benefit: import the user's existing
capture, preserve assignment coverage and native evidence, apply a new suite,
then change cost/latency preferences without rerunning the agent. Compare this
experience with a small native Promptfoo or MLflow implementation. If our code
mostly renames their fields, ship an integration package rather than another
general engine. This is a product test, not a claim those projects cannot do it.

### 2. “A wrapper silently changes how many times my agent runs.”

**Evidence:** MLflow documents an extra prediction used to validate tracing,
with an option to disable it. Promptfoo enables caching by default and documents
disabling it for fresh repeated calls. [MLflow prediction validation](https://mlflow.org/docs/latest/genai/eval-monitor/faq/#why-does-mlflow-make-n1-predictions-during-evaluation),
[Promptfoo caching](https://www.promptfoo.dev/docs/configuration/caching/).

**Concrete failure:** we declare two fresh OpenKritt scans and wrap a whole
MLflow evaluation inside our executor. Trace validation may start a third scan.
Alternatively, an embedded Promptfoo runner may replay an earlier response
while our new run ID makes it appear freshly executed. These violate execution
and accounting contracts regardless of the chosen accuracy metric.

**Recommendation:** choose one owner for execution. Initially reuse individual
scorers or import an externally completed evaluation. Do not put a second full
evaluation runner inside each `MetricEvaluator.compute`. If an external runner
owns execution, its integration must explicitly expose validation calls,
retries, cache hits and their provenance. Fresh IDs alone prove nothing.

### 3. “Your adapters promise more than their upstream interface provides.”

**Evidence:** a provider response supports cost and metadata, but that alone is
not evidence of a stopped process tree, complete child execution inventory, or
exclusive per-child charges.
[Promptfoo provider response](https://www.promptfoo.dev/docs/providers/custom-api/).

**Concrete failure:** importing a total cost of $0.12 as a parent charge while
also importing $0.08 and $0.04 child charges produces $0.24. Cancelling an HTTP
wait while a scan continues does not establish our `wall_time_limit` capability.

**Recommendation:** a native adapter includes mapping rules and operational
behavior, not just field renaming. Require a compatibility table per integration:
available, estimated, unknown or unsupported. Preserve raw exports and native
IDs; require fixtures for aggregate-only costs, missing usage and unconfirmed
stop. Our existing observation/capability types support the remedy, but the
diagram alone does not establish correctness.

### 4. “A thin scorer wrapper multiplies the expensive work.”

**Evidence:** one MLflow scorer can return several named feedback records.
Our current callback returns one task measurement plus diagnostic rows.
[MLflow scorer outputs](https://mlflow.org/docs/latest/genai/eval-monitor/scorers/custom/#outputs).

**Concrete failure:** an existing judge returns correctness, citation quality
and explanation completeness in one call. Three naive metric wrappers invoke
that same judge three times; sibling scores may then describe different judge
responses.

**Recommendation:** define one explicit adapter operation that captures the
upstream feedback bundle once, then projects declared metrics with shared
evidence and resource ownership. Before committing to that interface, test
whether the proposed dependency contract can represent the bundle cleanly;
do not hide it in an undocumented global cache. This is a representation and
execution issue, not an objection to the user's metric definitions.

### 5. “Importing the library accidentally starts adopting a platform.”

**Evidence:** MLflow evaluation creates/reuses tracking runs; its agent guide
configures a tracking server. Local tracking is supported, and managed dataset
entities have SQL-backed server requirements. Ordinary dictionaries/DataFrames
are separate supported inputs. [evaluation source](https://mlflow.org/docs/latest/api_reference/_modules/mlflow/genai/evaluation/base.html),
[agent setup](https://mlflow.org/docs/latest/genai/eval-monitor/running-evaluation/agents/),
[tracking architecture](https://mlflow.org/docs/latest/self-hosting/architecture/tracking-server/),
[dataset requirements](https://mlflow.org/docs/latest/genai/datasets/).

**Attack:** making MLflow our obligatory storage/evaluation implementation
changes the small-library contract, even when the deployment is local.

**Recommendation:** preserve the proposed offline record reader. MLflow tracking
should be an explicit optional exporter or importer, with user-owned connection
configuration. A scorer adapter should document its own side effects and SDK
requirements. Optional dependencies must stay out of root imports.

### 6. “Saved results become unusable on my colleague's machine.”

**Evidence:** Promptfoo offers JSON/JSONL exports and self-contained HTML reports.
Our current save/load guarantee covers records and references; it explicitly
does not bundle every artifact.
[Promptfoo export formats](https://www.promptfoo.dev/docs/configuration/outputs/).

**Concrete failure:** an OpenKritt finding retains a correct local ZIP path in
saved JSON. Copying that JSON to a colleague preserves the score but breaks the
evidence link. The current reload E2E uses the original cache, so it misses this.

**Recommendation:** keep this v0 limitation visible. Add a future portable bundle
operation and a relocation acceptance test before advertising shareable complete
evidence. Reuse archive/hash primitives; our work is the reference manifest and
consistent rewriting across all evidence locations.

## What to build, wrap or interoperate with

| Responsibility | Recommendation |
| --- | --- |
| Study/assignment identities, coverage, unknown observations, capture reconciliation | Own this small domain core. This is the contract we promise regardless of upstream runner. |
| Metric dependency ordering and mapping to result records | Own the minimum coordination needed; reuse graph/data primitives. |
| Existing quality judges | Optional scorer adapters where semantics and resource reporting map faithfully; avoid reimplementing their judge prompts. |
| Native agent/model calls | Use supported SDKs/CLIs/APIs. Own launch/mapping/cleanup adapters, not a new model-client abstraction. |
| Promptfoo | Start with versioned JSON/JSONL import. Add process-level execution integration only if demanded; Node remains an explicit dependency. |
| MLflow | Optional scorer bridge plus trace/result import/export. Keep tracking ownership explicit. |
| Reports and persistence | Reuse codecs/templates/archives; own our schema, migration rules and evidence joins. |

There is no defensible percentage of reused code before implementation. Today
the design names mostly custom modules; it has not actually built on either
engine. A well-designed first release should measure its value with a small
end-to-end workflow and a documented compatibility surface, then grow from
demonstrated needs rather than the number of boxes in its repository tree.
