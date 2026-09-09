# Test-first checkpoints

The original checkpoint below is historical. The subsequent realistic-workflow
revision changes test sources after the LLD checkpoint; the original manifest
is retained unchanged rather than presented as the hash of today's suite.
See [the showcase scenarios](e2e/SHOWCASE.md). Current verification is appended
below; both checkpoints retain their original source inventories.

Recorded at **2026-09-08 14:21:14 UTC**, before writing `docs/lld/` module pages.
The [manifest](ACCEPTANCE_MANIFEST.json) contains all collected node IDs and
SHA-256 hashes of test sources/fixtures. [Coverage map](README.md).

| Check | Actual result |
| --- | --- |
| Full collection | **222 cases**, exit 0 |
| Default full execution | **205 expected missing-package setup errors, 17 unselected live skips**, exit 1 |
| Error audit | All 205 failures name the absent `agent_eval_flow` package; runtime assertions did not execute |
| Python static validation | 22 test/fixture Python files parse; 237 explicit public record constructor calls match required fields/keyword names |
| Production implementation | No `src/` directory; no runtime stub substituted |
| External execution | No native agent, optional evaluation library or GCP job started |

The inventory contains 188 focused public-contract cases, 17 offline full-flow
cases, 14 native CLI/GCP cases and 3 optional-library integration cases. Native
OpenSRE and OpenKritt remain separate studies. Optional integrations require
the real factories, pinned runtimes and genuine archived evidence described in
their profiles; those prerequisites have not been fabricated.

The earlier [E2E-only baseline](e2e/BASELINE.md) records prior checkpoints. This
full-suite checkpoint supersedes its counts for repository-wide acceptance.
Source collection and static checks are not a runtime pass.

## Realistic-workflow revision

Recorded at **2026-09-08 17:56:43 UTC** after completing the richer E2Es.
[Current source hashes and collected cases](SHOWCASE_MANIFEST.json).

| Check | Actual result |
| --- | --- |
| Full collection | **227 cases**, exit 0 |
| Default full execution | **1 passed, 206 expected missing-package setup errors, 20 unselected live skips**, exit 1 |
| Passing case | Independent fixture check: verifies the downloaded MarkupSafe source, executes six actual dependency cases, and rejects altered/missing/stale report evidence |
| Error audit | All 206 errors name the absent `agent_eval_flow` package; no library runtime assertion was claimed as passing |
| Static validation | 44 Python files parse, including downloaded source; 258 explicit public record constructor calls match required fields/keyword names |
| Download validation | Pinned SWE-agent archive: 12 actions; OpenSRE input: 2,000 log lines; OpenKritt input: 22 files/26,053 bytes; MarkupSafe input: 6 files/23,019 bytes; original licenses/notices retained |
| Documentation | 14 authored Markdown files checked for local links and balanced code fences |
| Current manifest | 90 test/configuration/source/fixture files hashed; historical manifest left unchanged |
| Production/live execution | No production package or adapter substitute; no new native agent/model/GCP job executed |

The E2E directory now has 36 cases: 18 offline library E2Es, 17 live cases and
one independent fixture-preparation check. The full suite additionally includes
188 focused contract cases and three optional-library integration cases.

Additional input/tool/decoder/bundle helper checks verified native-source hashes,
the OpenSRE pagination/report fixture, OpenKritt's native workflow shape and
stream decoders, and evidence-bundle tamper rejection. They establish fixture
readiness, not compatibility with unexecuted native runtimes. Downloaded upstream
test files are input data and excluded from pytest discovery through `norecursedirs`.
