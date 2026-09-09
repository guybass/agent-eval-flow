# Foundations and native-agent fit

Research date: 8 September 2026. Recommendations for discussion, not installed
dependencies or revised API contracts. Official documentation and selected
source were read; no external agent, model or cloud workload was executed.

## Reuse beneath our domain objects

These libraries support implementation details; the agent-evaluation engines
are assessed separately. Owning our six objects need not mean writing their
validation, scheduling primitives or rendering machinery.

| Responsibility | Proposed dependency | Verified capability / evidence of use | What remains our responsibility |
| --- | --- | --- | --- |
| Typed configuration, measurements and JSON documents | Pydantic v2 | [Models](https://docs.pydantic.dev/latest/concepts/models/) and [serialization](https://docs.pydantic.dev/latest/concepts/serialization/) expose validation, JSON and schema support. Anthropic's actual [response-model source](https://github.com/anthropics/anthropic-sdk-python/blob/main/src/anthropic/_models.py) subclasses Pydantic. This is downstream code use, not an estimate of production deployments. | Field meanings, public/private data projection, cross-record IDs, evidence joins, schema migration policy and canonical fingerprints. |
| Direct local process I/O and asynchronous task coordination | AnyIO 4, for the direct execution integration | [Process APIs](https://anyio.readthedocs.io/en/stable/subprocesses.html) provide one-shot and streaming process handles; [cancel scopes](https://anyio.readthedocs.io/en/stable/cancellation.html) provide structured cancellation. Anthropic's [package metadata](https://github.com/anthropics/anthropic-sdk-python/blob/main/pyproject.toml) requires AnyIO. Its [process tests](https://github.com/agronholm/anyio/blob/master/tests/test_subprocesses.py) exercise output, checked failure, environment and working-directory behavior. | Mapping native outcomes to assignments, capture finalization and proving backend-specific termination. SDK dependency use does not establish that its subprocess path is exercised by that SDK. |
| Metric dependency ordering | Python `graphlib.TopologicalSorter` | [Python 3.12 API](https://docs.python.org/3.12/library/graphlib.html) supports ordering and cycle detection. CPython's [current tests](https://github.com/python/cpython/blob/main/Lib/test/test_graphlib.py) cover cycles, duplicate dependencies and invalid operation order. Current tests include behavior beyond Python 3.12; use the chosen interpreter's documented API. | Resolve metric IDs and versions, reject undeclared dependencies and invoke callbacks with their declared inputs. The sorter is not an evaluation engine. |
| Offline HTML report rendering | Jinja2 3.1 | [Jinja API](https://jinja.palletsprojects.com/en/stable/api/) supports rendering and configurable autoescaping. [Flask documents](https://flask.palletsprojects.com/en/stable/templating/) Jinja as its required template engine. | Report layout, evidence links, explicit escaping configuration and interpretation-free rendering of stored results. We do not need Flask to use Jinja. |

Pydantic's default coercion and extra-field behavior need explicit choices at
each boundary. Use [strict validation](https://docs.pydantic.dev/latest/concepts/strict_mode/)
where the contract requires it; retain raw upstream records separately rather
than silently discarding extra fields. `frozen=True` does not freeze nested
mutable containers, as the [model documentation](https://docs.pydantic.dev/latest/concepts/models/#faux-immutability)
explains. Therefore our deep immutability/fingerprint promises still need a
deliberate data representation. These requirements do not justify writing a
replacement general validator or JSON serializer.

AnyIO's cancellation controls do not prove that a remote OpenKritt scan or every
descendant of a local CLI has stopped. Threaded synchronous work cannot be
forcibly cancelled through a cancel scope. Keep transport timeouts, native job
cancellation and confirmed termination separate. Delegate Harbor jobs to
Harbor's scheduler instead of putting an AnyIO scheduler around each of its
trials. Use the direct path only where we actually own that invocation.

We have not installed or jointly tested these proposed versions. The current
project test interpreter is Python 3.12.14. Dependency resolution and supported
platform checks belong to the first integration exercise, not inferred from
the presence of source code. Moving-source links above were read on the research
date and are not package pins.

## Native agents constrain the integration choice

The existing [OpenSRE fixture](../../tests/e2e/fixtures/opensre/README.md)
already identifies a pinned CLI boundary: `opensre --json ask -` on a prepared
worker. Preserve its native JSON and output streams. Its JSON-only scenario
does not expose token or billing data, so those remain unknown. Native Vertex
routing and GCP hosting are distinct settings. A Harbor custom agent could
invoke that CLI, but this is an integration option to prove, not existing
Harbor support for OpenSRE. Running its native CLI through a direct process
integration is also faithful to the vision.

The [OpenKritt fixture](../../tests/e2e/fixtures/openkritt/README.md)
already has a native scan API and Docker-hosted engine. It imports a workflow,
creates a scan, and retrieves scan state, canonical findings and available ZIP
exports. Retain that engine's scheduling and workflow behavior. A generic
model/tool binding would risk replacing the harness under evaluation. Its
fixture produces findings, permits zero findings, and does not modify the
target. Native Vertex support is not established by this profile.

These boundaries were pinned and source-reviewed in the fixture work on
7 September; this note reuses that evidence and does not claim a fresh runtime
verification. The integration decision should accommodate both a whole Harbor
job and an already hosted native service. A prepared GCP machine does not need
a new cloud provisioning subsystem inside Agent Eval Flow.

## Consequence for the LLD

Keep the user's `eval()` experience, but permit asynchronous native job
interfaces and whole-job imports beneath it. Treat Pydantic-backed records as
an alternative implementation of the proposed dataclasses. Replace a custom
graph algorithm with `graphlib`; replace hand-built report rendering with Jinja.
Choose a normal HTTP client or the native SDK for a service bridge; no custom
network stack is warranted. Do not add pandas, a statistical framework, a trace
database or a model gateway to the mandatory core before a concrete feature
needs them. None of these recommendations fixes the user's metric semantics.
