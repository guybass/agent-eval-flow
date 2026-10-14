# Same bytes. Different agent context.

Pi 1.0.1 corrupts ANSI-styled command output when a terminal escape sequence is
split across stream chunks. `ERROR: file.py:1` becomes `ERROR: file.py:1m` when
the closing color reset is split before its final `m`. The same bad text appears
in streaming updates, the command result and Pi's retained conversation messages.

This report accompanies [proposed Pi fix #10503](https://github.com/earendil-works/pi/pull/10503). It is a follow-up to the
[Agent Eval Flow native harness research](https://guybass.github.io/agent-eval-flow/reports/native-harness/).
The direct component probes do not call Agent Eval Flow or a model; the library
was used for evidence retention and regrading in the earlier agent study.

## Result

Three fresh native Pi processes each ran six cases. The plain and unsplit styled
controls passed. The four deliberately selected split variants failed in every
process. These are development probes, not independent user workloads. The third
process also checked `get_messages`: all six retained outputs matched the command
results, including the four corruptions.

The proposed fix preserves ANSI parser state across chunks. The initial source
regressions passed 1/14 before the fix and 14/14 after it. The five existing ANSI
utility tests also passed. Full validation status is recorded in `study-data.json`
and the contribution linked from the HTML report.

An unchanged native six-case RPC probe was also run against the current source
(package version 1.0.3), before and after the patch, with container networking
disabled. Exact streamed, returned and retained text improved from 2/6 to 6/6
in these inspected cases. This is a component fix validation, not a measured
improvement in model answers. The complete code checks and offline build passed;
the complete test suite is not green in the validation environment. See the PR
for failures and the focused tests that passed after building.

Pi PR #10503 was submitted as a draft and auto-closed by Pi's new-contributor
gate. [Approval request #10504](https://github.com/earendil-works/pi/issues/10504)
contains the minimal reproduction. The fix has not merged, and this automatic
gate result is not a technical review decision.

## Read and reproduce

- [Rendered report source](index.html), with method, scope, null findings and limits.
- [Data and evidence checksums](study-data.json).
- [Original six-case native probe](evidence/ansi_context_probe.py).
- [Raw no-model RPC capture](evidence/latest-ansi-context/rpc.jsonl).
- [Retained native messages](evidence/latest-ansi-context/messages.json).

The original probe expects Linux, Python, the official binary at `/opt/pi/pi`, a
clean `/work` directory and writable `/evidence`. Use a disposable container with
networking disabled and no account credentials. Each styled variant concatenates
to identical bytes. The script freezes inputs before sending any command.

Rebuild locally from the repository root:

```bash
python examples/pi_output_report.py
python scripts/build_report_site.py
```

The renderer verifies every exported evidence hash and recomputes the reported
ANSI results. The site builder publishes only explicit approved paths. A Pages
URL becomes available after this report PR is merged and deployed.

## Scope and limitations

Verified: user `!` / RPC `bash` output on the official Pi 1.0.1 Linux binary.
This does not establish a defect in the model-invoked built-in bash tool, an
effect on model answers, or a cause for earlier coding benchmark failures.

The original study remains unchanged. The separate follow-up's twelve direct
investigation attempts found no new editing violation. Six UTF-8 controls passed.
The known truncation defect #10164 was also replicated and is not claimed as a
new discovery. Those separate results are summarized in the report; private
model traces are not included in this evidence export.
