# Before-implementation baseline

The later [full-suite checkpoint](../BASELINE.md) records all 222 acceptance
cases before the complete module LLD was written. The entries below retain
their historical E2E-only counts.

Recorded on 7 September 2026, before writing the LLD architecture.

| Check | Actual result |
| --- | --- |
| Python / pytest | Python 3.12.14; pytest 9.1.1 in the ignored local `.venv` |
| `python -m pytest --collect-only -q` | **22 tests collected**, exit 0 |
| `python -m pytest tests/e2e --tb=short -q` | **8 setup errors, 14 skipped**, exit 1 |
| Reason for all 8 errors | `agent_eval_flow` is not implemented/installed; explicit RED message |
| Reason for 14 skips | No live profile selected: 12 local/Vertex toy cases, 2 native GCP cases |
| Static checks | 11 Python test/fixture files parse; constructor keyword names match the typed proposal; JSON fixtures parse |
| Fixture-only smoke | Owned `plain`, `skill`, `tool`, `flow` subprocess modes returned valid fixture JSON |

This is the expected starting state of tests written before implementation.
It is **not** an E2E pass or proof that the proposed runtime code works. The API
setup errors prevent the library assertions from running until implementation
exists. No fake implementation was added to make those assertions green.

No native Codex/Claude task, Vertex request, OpenSRE invocation, OpenKritt scan,
GCP deployment or credential change was performed. Only test dependencies were
installed into the repository's virtual environment. Upstream source inspection
and local CLI help/version reads are documented in the runtime profiles.

Review subsequently tightened assertions for the known fixture summary/delta/
selection values and fresh tool receipts; syntax/API checks were repeated.
No scientific metric quality or native-agent benchmark was tested.

## Pipeline contract revision — 8 September 2026

After making `EvaluationPipeline` explicit at level 1, existing toy scenarios
exercise its execution and saved-run grading paths. Additional cases cover
extension preflight, fresh executions when reusing a configured pipeline, and
the module-level convenience entry point.

| Check | Actual result |
| --- | --- |
| `python -m pytest --collect-only -q` | **27 tests collected**, exit 0 |
| `python -m pytest tests/e2e --tb=short -q` | **13 setup errors, 14 skipped**, exit 1 |
| Reason for all 13 errors | `agent_eval_flow` is still not implemented/installed; explicit RED message |
| Reason for 14 skips | No live profile selected |
| Static checks | Typed proposal and all test Python files parse; proposal annotation names resolve; local links in the changed design/readme documents resolve |

This remains a before-implementation baseline. No production library or live
backend was executed, and none of the library behavior assertions passed yet.

## Level-2 architecture revision — 8 September 2026

The [level-2 design](../../docs/LLD_LEVEL_2.md) names the owning services and
exception family. T11 now requires `ConfigurationError` for a bad extension
binding, retaining its checks that no backend or evaluator work begins.

Validation was repeated after that contract change: **27 tests collected**
(exit 0); **13 expected missing-package setup errors and 14 live skips**
(exit 1). All 11 Python test/fixture files and the typed proposal parse, the
changed documents' local links resolve, and no production source directory
was created. These results do not validate the proposed service behavior.

## Configuration and data contract 0.4 — 8 September 2026

The [revised data contract](../../docs/DATA_CONTRACT.md) adds native-job and
batch boundaries, rich imported captures, explicit metric sources, output
availability and grading-activity/inventory records. Existing fixtures now use
these signatures. Four new [data-contract cases](test_data_contract.py) cover
partial job exports, batch joining/shared expenses, strict identity types and
retained-grade persistence. The retained-grade case also checks that incomplete
grading capture produces an unknown historical total, while saved-run grading
can still have known zero incremental expense.

| Check | Actual result |
| --- | --- |
| `python -m pytest --collect-only -q` | **31 tests collected**, exit 0 |
| `python -m pytest tests/e2e --tb=short -q` | **17 setup errors, 14 skipped**, exit 1 |
| Error reason | Every offline case requires the missing `agent_eval_flow` package |
| Skip reason | The 14 native CLI/GCP cases remain unselected |
| Static checks | 13 Python/proposal files parse; 66 explicit API record constructor calls match required fields/keyword names; 10 changed documents pass local-link, fence, table-column and whitespace checks |

These are acceptance specifications awaiting implementation. No runtime
behavior assertions ran, and no native agent or GCP job was started.
