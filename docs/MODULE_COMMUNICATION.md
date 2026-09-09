# Agent Eval Flow: module communication map

**Accepted design extension, 9 September 2026:** start with the
[unified assessment flow](UNIFIED_ASSESSMENT_FLOW.md) for the new parallel
configuration-inspection and behavioral-evaluation branches. They share a frozen
candidate and produce scoped assessments for explicit decisions. Inspection
blocks execution only when a policy declares that dependency. This extension is
not implemented and does not replace revision-0.4 interfaces. The diagrams on
this page describe the earlier behavioral branch only. For the detailed new
call graph, use [one assessment invocation through the modules](lld/README.md#one-assessment-invocation-through-the-modules)
and the [shared assessment contract](lld/ASSESSMENT_CONTRACT.md).

For the final-review design, use the [current grouped communication graph](lld/README.md#communication-grouped-by-directory)
and eight detailed module pages. The generated image/draw.io assets below
preserve the earlier level-2 view.

**Proposed level-2 design, 8 September 2026.** Files from the same directory
are grouped together. This graph records the earlier per-assignment design.
The [revision-0.4 data contract](DATA_CONTRACT.md) and updated
[level-2 architecture](LLD_LEVEL_2.md) supersede its request/output signatures.

The later [integration proposal](INTEGRATION_DECISION.md#how-the-proposed-pieces-connect)
shows how existing libraries could sit underneath the common interface. Its
native-job and batch-grading changes are now defined in revision 0.4, but are
not drawn in this earlier graph. Implementation-status statements in this
historical map are superseded by [current implementation status](IMPLEMENTATION.md).

Start with the overview, then use the three detailed graphs for exact calls.
Solid arrows are calls/data passed forward; dashed arrows are returned records
or recorder callbacks. This is a communication map, not a Python import graph.
Everything except the explicitly shown native process/worker boundary is
ordinary in-process library code, not a separate service deployment.

![Directory-grouped module communication](diagrams/module-communication.png)

[Zoomable SVG](diagrams/module-communication.svg) ·
[Editable draw.io](diagrams/module-communication.drawio) ·
[Diagram generator](diagrams/build_module_map.py) ·
[Full level-2 responsibilities](LLD_LEVEL_2.md)

The overview compresses caller handoffs: an engine returns the result to the
caller, who invokes result methods. It does not directly call `results/` or
save/report automatically. `objects/` supplies contracts throughout; routine
type imports are omitted to keep the graph readable.

## 1. Prepare: definitions become a runnable or gradeable request

```mermaid
flowchart LR
    USER["Caller: configure, then eval()"]
    subgraph O["objects/"]
      direction TB
      STUDY["study.py + candidate.py + suite.py<br/>Study definitions"]
      CHECK["validation.py + identity.py<br/>structure, fingerprints"]
      SAVED["runset.py<br/>optional saved RunSet"]
    end
    subgraph P["pipeline/"]
      direction TB
      API["api.py<br/>EvaluationPipeline"]
      BIND["bindings.py<br/>RuntimeBindings"]
      PRE["preflight.py<br/>prepare_evaluation / check_capture"]
    end
    subgraph X["execution/"]
      direction TB
      PLAN["planning.py<br/>build_plan"]
      XP["preflight.py<br/>prepare_execution"]
    end
    subgraph E["evaluation/"]
      COMPILE["compiler.py<br/>compile_suite"]
    end
    EXT["Registered backends / evaluators / reducers<br/>runtime objects owned by caller"]
    USER --> API
    STUDY --> API
    EXT --> BIND
    API --> BIND
    API --> PRE
    SAVED --> PRE
    BIND --> PRE
    PRE --> CHECK
    PRE -->|"both paths: resolve checks"| COMPILE
    PRE -->|"fresh path only"| XP
    XP --> PLAN
    XP -->|"ref and capabilities only"| EXT
    COMPILE -->|"evaluator/reducer refs only"| EXT
    PRE -.->|"PreparedEvaluation"| API
```

No agent or metric callback executes here. Saved-run preparation validates
compatibility without requiring a backend. The entire study fingerprint is
not its matching key: changing only the suite is allowed. Runtime capability
checks do not guarantee that a later network request or native process succeeds.

## 2. Execute or import: public inputs become captured evidence

```mermaid
flowchart LR
    subgraph P["pipeline/"]
      API["api.py<br/>fresh execution path"]
    end
    subgraph O["objects/"]
      direction TB
      DATA["dataset.py<br/>agent_input(unit)"]
      RS["runset.py<br/>RunSet with every assignment"]
    end
    subgraph X["execution/"]
      direction TB
      RUN["runner.py<br/>RunExecutor.execute"]
      BUF["capture.py<br/>CaptureBuffer"]
      IMP["importing.py<br/>import_runs"]
    end
    subgraph A["adapters/"]
      direction TB
      NATIVE["codex.py / claude_code.py<br/>opensre.py / openkritt.py"]
      PROC["process.py<br/>ProcessSupervisor"]
      WORK["worker.py<br/>PreparedWorkerClient"]
    end
    subgraph S["storage/"]
      CACHE["artifacts.py<br/>local artifact cache"]
    end
    UP["Native CLI / service / prepared GCP worker"]
    FILE["Caller supplies source + plan + RunImporter"]
    API -->|"PreparedExecution"| RUN
    RUN --> DATA
    DATA -.->|"public AgentInput only"| RUN
    RUN -->|"run(request, recorder)"| NATIVE
    RUN -->|"one recorder per assignment"| BUF
    NATIVE -.->|"execution / event / artifact callbacks"| BUF
    NATIVE -->|"local process path"| PROC
    NATIVE -->|"remote path"| WORK
    NATIVE -->|"native service API path"| UP
    PROC --> UP
    WORK --> UP
    NATIVE -->|"retain bytes"| CACHE
    WORK -->|"materialize + remap references"| CACHE
    NATIVE -.->|"final Run"| RUN
    RUN -->|"finish and reconcile by ID"| BUF
    BUF -.->|"immutable Run"| RUN
    RUN --> RS
    FILE --> IMP
    IMP -->|"validate; absent becomes unobserved"| RS
    RS -.->|"captured RunSet"| API
```

The three native paths are alternatives. A prepared worker invokes the selected
adapter locally; it does not recursively launch another remote worker. Native
importers are supplied through `RunImporter`; not every adapter requires one
in v0. Imports reuse capture validators without calling an agent. A pending
or missing run remains available to the chosen evaluators.

## 3. Evaluate, inspect and save: evidence becomes usable results

```mermaid
flowchart LR
    subgraph P["pipeline/"]
      API["api.py<br/>captured or supplied RunSet"]
    end
    subgraph O["objects/"]
      direction TB
      INPUT["dataset.py + protocols.py<br/>private EvaluationContext"]
      OUT["result.py<br/>EvaluationResult"]
    end
    subgraph E["evaluation/"]
      direction TB
      ENGINE["engine.py<br/>EvaluationEngine.evaluate"]
      PRIM["primitives.py<br/>run.* helpers"]
      SCORE["scoring.py<br/>score_task"]
      AGG["aggregation.py<br/>summarize_candidate"]
    end
    METRIC["MetricEvaluator.compute<br/>user code or existing scorer adapter"]
    REDUCER["SummaryReducer.reduce<br/>user code or existing reducer adapter"]
    USER["Caller: inspect / choose / save / report"]
    subgraph R["results/"]
      direction TB
      QUERY["query.py<br/>explain / summary"]
      COMP["comparison.py<br/>compare"]
      SELECT["selection.py<br/>select"]
    end
    subgraph S["storage/"]
      direction TB
      MAN["manifests.py<br/>ManifestStore"]
      CODEC["codec.py<br/>RecordCodec"]
    end
    subgraph H["reporting/"]
      HTML["html.py<br/>HtmlReportRenderer"]
    end
    API -->|"compiled suite + dataset + runs"| ENGINE
    INPUT --> ENGINE
    ENGINE --> PRIM
    ENGINE -->|"spec + context + Run"| METRIC
    METRIC -.->|"MetricOutput and grader resources"| ENGINE
    ENGINE -->|"task measurements"| SCORE
    ENGINE -->|"all candidate assignments and measurements"| AGG
    AGG --> REDUCER
    REDUCER -.->|"CandidateSummary"| AGG
    ENGINE -.->|"returns through pipeline"| OUT
    OUT --> USER
    USER --> QUERY
    USER --> COMP
    USER --> SELECT
    USER -->|"save / load"| MAN
    MAN --> CODEC
    USER -->|"result + optional Selection"| HTML
    HTML --> QUERY
```

The compiler's output was prepared in graph 1. The engine reuses each dependency
measurement within this call; result access invokes no evaluator. Saving and
loading preserve records and references. Loading does not connect to a backend
or fetch unavailable evidence files.

## Communication contracts to review at level 3

| Sender → receiver | Data / operation | Who owns execution? |
| --- | --- | --- |
| Caller → pipeline | `Study` and registries; `eval(runs=None)` | Pipeline coordinates the chosen path. |
| Pipeline → execution | `PreparedExecution` → `RunSet` | Executor dispatches; native adapter owns retries and stopping. |
| Executor → native adapter | `RunRequest` plus one `RunRecorder` → `Run` | Executor calls the adapter once; adapter owns declared native attempts and retries. |
| Adapter → recorder | `Execution`, `Event`, named `ArtifactRef` | CaptureBuffer reconciles identities, not metric meanings. |
| Pipeline → evaluation | `CompiledEvaluation`, dataset, `RunSet` → result | Engine runs the checks; no agent invocation. |
| Engine → metric | `MetricSpec`, `EvaluationContext`, `Run` → `MetricOutput` | Metric owns its declared measurement computation. |
| Aggregation → reducer | Candidate assignments, runs, measurements, scores → summary | Reducer owns the declared aggregation. |
| Result methods → results | Stored result plus IDs or `SelectionPolicy` | Pure record operations; no callbacks. |
| Object save/load → storage | Supported root and filesystem path | Store/codec own record encoding, not a runtime session. |
| Result report → reporting | Result, output path, optional selection | Renderer produces HTML from saved facts. |

`values.py`, `errors.py` and the protocol definitions are shared contracts,
not independent runtime services. Most of the code in this graph does not
exist yet. See the [build-versus-reuse review](ARCHITECTURE_REUSE_REVIEW.md)
before interpreting every box as a subsystem we should implement ourselves.
