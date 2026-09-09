# Runtime evidence across agent systems

Runtime checks use the existing `Event`, `MetricSpec`, `Measurement`, and
`EvaluationPipeline` objects. They work with live captures and imported or saved
runs. No new execution loop, mandatory score, or domain policy is introduced.

A versioned collector records **declared value → observed value**, together with
the component, phase, observation boundary, coverage, and supporting evidence.
The evaluator applies a caller-selected requirement. The report displays the
saved check and the observations that it actually evaluated.

## One contract, different components

| Subject chosen by the application | Observation boundary | Example requirement |
| --- | --- | --- |
| `task.instructions` | Captured provider CLI input | Required text is present before the first action. |
| `tools.available` | Native tool registry after discovery | The required tools are available at that phase. |
| `tool.schema` | Schema delivered to a particular call | The delivered schema equals the declared schema. |
| `loop.post_terminal_calls` | Complete lifecycle audit for one loop | Recorded actions after accepted termination are zero. |
| `model.id` | Resolved provider configuration | The selected model matches the experiment. |
| `environment.image` | Worker launch receipt | The worker uses the declared image digest. |
| `memory.tenant` | Memory retrieval receipt | The observed tenant matches the permitted tenant. |

Subjects, phases, and boundaries are application-defined strings. Collectors
must document their meaning. OpenSRE task assembly and OpenKritt reporting rules
remain integration concerns. The shared evaluator knows neither agent's names,
tools, task wording, success criteria, nor native event format.

## Record a supported observation

```python
from agent_eval_flow import EvidenceRef, VersionRef
from agent_eval_flow.objects.runtime_evidence import RuntimeObservation
from agent_eval_flow.objects.values import observed

# Existing immutable ArtifactRefs supplied by the collector.
declaration = EvidenceRef(artifact=task_artifact, description="Selected task")
delivery = EvidenceRef(artifact=stdin_artifact, description="Captured CLI input")

record = RuntimeObservation(
    collector=VersionRef(name="my-harness.prompt-capture", revision="1"),
    subject="task.instructions",
    component_id="prompt.main",
    phase="first_action",
    boundary="provider_cli_input",
    call_id="call-1",
    declared=observed(full_task, evidence=(declaration,)),
    observed=observed(delivered_task_section, evidence=(delivery,)),
    coverage="complete",
    transformations=("Native wrapper; literal task section extracted",),
)
event = record.to_event(id="observation-1", execution_id=execution.id)
```

The collector obtains values from actual configuration and native evidence.
It must not fill observations from intended settings or generated claims of
execution. A captured file alone does not prove that its content was delivered.
The transformation descriptions are collector records, not causal judgments.

The helper performs no I/O and does not edit the run. A backend may emit this
event alongside native events. An importer may add it when creating a **derived
RunSet**, preserving original events and the source capture. Existing saved runs
are never silently enriched or rewritten by evaluation.

The payload uses schema `1` in events with kind `aef.runtime.observation`.
`decode_runtime_observation(event)` validates opted-in payloads and returns
`None` for ordinary native events. Typed `Event.inputs`, `outputs`, and `source`
also retain the evidence references, so existing evidence traversal can discover
them outside opaque JSON fields. Save/load requires no change to schema 0.4.

## Configure a check

```python
from agent_eval_flow import EvaluatorSource, MetricSpec
from agent_eval_flow.evaluation.runtime_checks import RuntimeEvidenceEvaluator

evaluator = RuntimeEvidenceEvaluator()
metric = MetricSpec(
    id="first_action.delivery",
    source=EvaluatorSource(ref=evaluator.ref),
    output_type="bool",
    role="diagnostic",
    params={
        "subject": "task.instructions",
        "phase": "first_action",
        "boundary": "provider_cli_input",
        "component_id": "prompt.main",
        "call_id": "call-1",
        "operator": "contains",
        "expected": "Preserve pending orders.",
        "expected_count": 1,
    },
)
```

Add this metric to the study's suite and bind the evaluator by its `ref.name`.
Omitting `expected` compares against the recorded declaration. Supplying it
expresses the evaluation requirement explicitly, including permitted changes
such as a wrapper around required content. Nothing requires byte equality for
every agent input.

Supported operators are:

- `equals`: strict structural equality; booleans, integers and floats remain
  distinct, as do array order and multiplicity.
- `contains`: case-sensitive text containment.
- `includes`: array inclusion with strict element equality and multiplicity;
  order may differ.
- `at_most` / `at_least`: numeric bounds, excluding booleans and string coercion.

Selectors require subject, phase and boundary, and may additionally specify a
component or call. `selection` is `all` by default, or `first` / `last` in
**retained event order**. These options do not establish chronological order or
prove that the first actual runtime call was captured.

## Scope and uncertainty

`coverage` describes the **selected value**, not the completeness of the whole
runtime trace. For example, a full first-call input can establish that a clause
was absent there. A partial input cannot establish either a complete match or
a violation through these conservative checks.

A passing result means that the selected recorded observations match. It is
not proof that every runtime action was recorded. Use explicit call/phase
selectors and `expected_count` when the expected population is known. This
count is checked before `first` / `last` selection. To check a complete loop,
a collector must establish that loop's coverage before emitting a complete
aggregate such as `loop.post_terminal_calls`; an execution inventory alone is
insufficient.

| Evidence condition | Measurement |
| --- | --- |
| Complete observed value matches | `ok`, observed `True` |
| Complete observed value violates the requirement | `ok`, observed `False` |
| No matching observation, unknown/estimated value, partial coverage, or fewer records than explicitly expected | `missing`, null value/basis; resulting acceptance can remain unknown |
| Malformed selected payload, detached evidence references, invalid operator types, or excess records beyond an explicit expected count | `error`, null value/basis |

A supported counterexample remains a failure even if another observation or
part of the expected population is missing. A selected malformed record takes
precedence as an evaluator error. Requirements about record counts are not
hidden agent penalties.

The evaluator validates the common records; native-to-common extraction and
artifact integrity remain the collector/importer's responsibility. Evidence
links are retained without fetching remote content. A CLI input observation
does not assert the contents of the remote HTTP request or the model's attention.
Known JSON null can be represented as `{"value": null}`; existing `Observation`
uses a null top-level value for unknown observations.

## Re-evaluate without an agent

```python
from agent_eval_flow import EvaluationPipeline, EvaluationResult

saved = EvaluationResult.load("results/prior")
result = EvaluationPipeline(
    study=revised_study,  # same tasks/candidates/execution; changed suite
    evaluators={evaluator.ref.name: evaluator},
).eval(runs=saved.runs)
result.report("results/runtime-evidence.html")
```

This evaluator is deterministic and declares zero grading resources. Other
selected evaluators may invoke judge models or probes; eval-only suppresses
target agent execution, not all possible grading work. Legacy runs lacking
the optional observations remain valid and receive missing results for these
checks.

The report shows expected and observed values, scope, collector provenance,
coverage, evidence links and the **saved** check result. It runs no evaluator
and does not rewrite the capture. The existing assessment projection can also
wrap these ordinary behavioral measurements.

## Verification and limits of this increment

Tests were written before implementation. Unit cases cover truncated tasks,
permitted transformations, dynamic tool availability, lifecycle counts, model
and environment mismatch, uncertainty, malformed data and strict value types.
The E2E test executes the real toy process, imports its tool evidence, preserves
the native events, saves and reloads the result, then changes the requirement
and observes a changed verdict without another backend invocation.

This increment supplies the shared contract, checker and report view. It does
not automatically interpret arbitrary native logs, repair native prompt
truncation, clarify example task wording, or terminate a native loop. Those
changes require explicit, versioned collectors or candidate/runtime revisions.
