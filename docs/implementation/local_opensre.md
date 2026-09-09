# Local OpenSRE + Codex investigation

The example runs the native OpenSRE `AgentSession.chat_until_goal` against the
downloaded 2,000-line HDFS sample. The mission opens an incident, searches two
pages of logs, inspects sample-derived metrics, topology and change history,
and writes a structured report with evidence receipt IDs. The change history
is explicitly synthetic exercise context; the log sample is real historical
data. No particular diagnosis earns points.

## Configuration and execution

The application-owned runtime is
`examples.integrations.opensre_local:make_runtime`. It runs the existing native
worker in a dedicated Docker container, with a confirmed container stop at the
assignment deadline. Application bindings and incident fixtures are mounted
read-only; the pinned OpenSRE checkout is built into the image. The invocation's
workspace is the only writable host mount. A separate private authentication
mount supplies the existing Codex login, which is copied into the container's
private home and excluded from evaluation artifacts.

Use OpenSRE revision `1a81e1a1b378ecc9aeb211fcaeecb0f5c93548e4`. The local image
reuses the working Codex image from the OpenKritt demonstration, installs the
native dependency lock and Agent Eval Flow dependencies, and checks out
OpenSRE inside the Linux image. Its
image identity is retained in the Docker lifecycle receipt. Configuration
requires `checkout`, `codex_home`, `image`, `runtime_factory`, and `backend_ref`.
Candidate settings include `model`, `native_max_iterations`, and the container
fixture path `native_fixture_dir`.

```powershell
.venv\Scripts\python.exe -m examples.opensre_local_review --config test-artifacts/opensre-local.json --output demo-output/opensre-local --wall-time-s 1200
```

Use a fresh output directory for each execution. The command saves the study,
typed result, readable overview, detailed report and evidence ZIP. It reloads
the result and regrades the same capture with changed weights, without another
agent call.

## Native behavior and evidence

OpenSRE retains its ReAct loop, goal reviewer, CLI provider, response parser,
tool execution and retry behavior. The binding registers the six incident
tools and attaches the existing typed runtime callback at native construction.
Its iteration ceiling is an explicit, recorded configuration of the pinned
construction module. Existing native callbacks are forwarded.

The observer retains actual event payloads and tool-call identities. A
transparent native subprocess tap also retains Codex inputs and its decoded
stdout/stderr before native parsing. These streams include goal-review calls
that do not emit ReAct iteration events. ReAct iterations and total CLI calls
therefore remain separate quantities.

Native memory and background extraction are disabled through OpenSRE's
configuration for this isolated evaluation. The native CLI provider does not
expose reliable input/output token totals or dollar billing. Their values stay
unknown. This application's score checks usable evidence and object transport;
root-cause quality requires a separately defined evaluation suite.

## Observed result — 9 September 2026

The completed local invocation is `85d884a274024bd6b956c04cd01c5339`, using
`gpt-5.6-luna`. It completed six native ReAct iterations and eight incident tool
calls, retaining 49 typed runtime events in 123.177 seconds. All eight
integration checks passed (100/100). Saved-result reload and regrading passed
without any additional agent calls. Token and dollar totals remain unknown.
Seven actual Codex CLI invocations completed, including the native goal-review
call. The final regression suite passed 326 tests with 21 skips, and all 90
frozen acceptance/fixture files remained unchanged.

The final evidence and result are in `demo-output/opensre-local-run3/`. Earlier
attempts remain preserved: the first stopped on a Windows/Linux checkout
normalization mismatch before any model call; the second completed the
investigation but exposed unsupported `frozenset` serialization in the native
goal result. The serializer now preserves set members as deterministic JSON
arrays. Regression checks exercise the actual pinned goal and turn classes.
The local transport also corrected a container-name field and cross-checks it
against the independent Docker receipt.

Generate the attempt index without running models:

```powershell
.venv\Scripts\python.exe -m examples.opensre_attempts_report --runs demo-output/opensre-local demo-output/opensre-local-run2 demo-output/opensre-local-run3 --output demo-output/opensre-results/index.html
```
