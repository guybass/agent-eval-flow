# Contributing

Agent Eval Flow is a developer preview. Contributions are welcome: clearer
instructions, small examples, reproducible bug reports, tests, and code fixes
all help. You can run the offline examples and default tests without model credentials.

## Choose a starting point

- **Improve the docs:** clarify a setup step, fix a broken link, or explain an
  example in [README.md](README.md), [the test guide](tests/README.md), or an
  adjacent example README. Small edits can be proposed directly in GitHub.
- **Report a bug:** include the package/Python versions, a minimal reproduction,
  and the expected and observed results in a
  [bug report](https://github.com/guybass/agent-eval-flow/issues/new?template=bug_report.yml).
- **Add an example or regression test:** demonstrate a small, reproducible
  workflow with public or synthetic inputs. Offline examples are a useful place
  to start; see [archive_review.py](examples/archive_review.py) and
  [assessment_review.py](examples/assessment_review.py).
- **Improve the library:** work on an adapter, evidence handling, evaluation, or
  reports. Use the [code map](#find-the-right-code) below to find the relevant area.

Browse [open issues](https://github.com/guybass/agent-eval-flow/issues) or
[suggest an improvement](https://github.com/guybass/agent-eval-flow/issues/new/choose).
For a larger feature or public API change, open an issue to discuss the use case
and approach first. Small fixes can go straight to a pull request. If you pick
up an existing issue, leave a comment to help others avoid duplicate work.

## Set up a checkout

You need Git and Python 3.11 or later. Fork
[the repository](https://github.com/guybass/agent-eval-flow/fork) on GitHub,
then replace `YOUR-USERNAME` below with your GitHub username:

```bash
git clone https://github.com/YOUR-USERNAME/agent-eval-flow.git
cd agent-eval-flow
git remote add upstream https://github.com/guybass/agent-eval-flow.git
git switch -c my-contribution
```

Create and activate a virtual environment. On macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, use `.\.venv\Scripts\python.exe` in place of
`python` in the commands below; activation is optional.

Install the checkout and development dependencies, then run a first check:

```bash
python -m pip install -e ".[test,cli]" build
python scripts/verify_acceptance_checkpoint.py
python -m pytest tests/e2e/test_archived_trajectory.py
python examples/archive_review.py --output demo-output/contributing
```

Open `demo-output/contributing/report.html` to see the generated report. The
example evaluates a retained run without calling a model. The editable install
means changes under `src/` are used on the next run.

## Find the right code

| Area | Where to look |
| --- | --- |
| Public API and orchestration | [src/agent_eval_flow/__init__.py](src/agent_eval_flow/__init__.py), [pipeline/](src/agent_eval_flow/pipeline/) |
| Shared records, validation, and evidence | [objects/](src/agent_eval_flow/objects/) |
| Planning, execution, and capture | [execution/](src/agent_eval_flow/execution/) |
| Runtime adapters and native formats | [adapters/](src/agent_eval_flow/adapters/) |
| Evaluation and scoring | [evaluation/](src/agent_eval_flow/evaluation/) |
| Querying, comparisons, and selection | [results/](src/agent_eval_flow/results/) |
| Saving results and artifacts | [storage/](src/agent_eval_flow/storage/) |
| HTML reports and styles | [reporting/](src/agent_eval_flow/reporting/) |
| Runnable examples | [examples/](examples/) |
| Tests and test selection | [tests/README.md](tests/README.md) |

Follow the surrounding code style and keep each pull request focused on one
problem. For a behavior change, add a regression check in the relevant test
folder. Use small deterministic inputs when possible.

Keep metric meaning, provenance, and missing evidence explicit: unknown values
must not become zero or success. Native adapters should preserve the runtime's
own execution loop. Explain any changes to the public API or saved formats.

The files listed in [tests/fixtures_manifest.json](tests/fixtures_manifest.json)
are frozen acceptance tests and fixtures. Preserve their bytes and hashes; add
new tests and fixtures in separate files instead of updating the manifest to
make a failing integrity check pass. Third-party fixtures retain their own
licenses and provenance; see [third-party notices](THIRD_PARTY_NOTICES.md).

## Check your change

Run commands from the repository root with your virtual environment's Python.
During development, select the relevant test file or folder, for example:

```bash
python -m pytest tests/contracts tests/unit
python -m pytest tests/e2e/test_archived_trajectory.py
```

For code changes, run the same offline checks as the main CI job before requesting
review:

```bash
python scripts/verify_acceptance_checkpoint.py
python -m pytest
python examples/archive_review.py --output demo-output/contributing
python -m build
python scripts/verify_distribution.py
```

For Toolscore integration changes, also run its optional checks:

```bash
python -m pip install -e ".[test,cli,toolscore]"
python -m pytest tests/unit/test_toolscore_integration.py
python examples/toolscore_review.py --output demo-output/toolscore
```

For documentation-only changes, check the rendered Markdown and links; if you
change runnable instructions, try the affected commands. No new test is needed
for a typo fix. Note what you checked in the pull request.

[CI](.github/workflows/tests.yml) runs on Linux and Windows with Python 3.11,
3.12, and 3.13. You do not need to reproduce every platform locally. Default
tests do not start live models; unselected live tests and some platform-specific
cases may skip. Live profiles require explicit selection and prepared runtimes;
see [the test guide](tests/README.md#select-a-live-integration-explicitly).
Offline success alone does not establish live runtime compatibility.

If a check fails, include the command and relevant error in your pull request or
issue, along with your Python version and OS. Explain any checks you could not
run. A draft pull request is welcome while you work through a problem.

## Keep shared files reproducible

Remove credentials, private prompts, and task data before sharing evidence.
Prefer a short, scrubbed excerpt over a full run capture.

Local outputs belong in ignored `demo-output/` or `test-artifacts/` directories.
Keep design drafts, research, and planning in ignored `doc/` or `docs/` folders.
Public instructions belong in tracked READMEs and example guides, rather than
the ignored `docs/` folder. Only selected public report artifacts belong in
`examples/reports/`.

Configuration templates must have `.example` in the filename and contain
placeholders only, like `examples/profiles.local.example.json`. Copy templates
to the ignored local configuration path when preparing a native runtime. Keep
actual credentials in that runtime's supported authentication mechanism; never
put real credentials in a template. Dotenv files, PyPI/netrc credentials and
private key files are ignored. Offline examples need no credentials, and PyPI
publishing uses the configured GitHub trusted publisher rather than a token file.

## Open a pull request

1. Review your diff and commit only the files needed for your change.
2. Push your branch to your fork: `git push -u origin my-contribution`.
3. Open a pull request from that branch to `guybass/agent-eval-flow`'s `main`
   branch. Use a short title that describes the resulting improvement.
4. Fill in the pull request template: explain the problem, the change, and the
   checks you ran. Link a related issue when there is one; an issue is not
   required for a small fix. For report layout changes, include a scrubbed
   screenshot.
5. Review the CI results and respond to feedback. Push follow-up commits to the
   same branch to update the pull request.

Keep discussion respectful and focused on the work. Questions and small first
contributions are welcome.
