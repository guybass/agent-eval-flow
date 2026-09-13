# Version 0.5.0 — developer preview

Agent Eval Flow evaluates complete agent systems through their existing runtimes
or retained execution logs. This initial public release is intended for developers
building evaluation workflows and inspecting failures.

## Included

- Typed studies, candidates, tasks, execution evidence and evaluation results.
- Import and regrade saved runs without invoking the target agent again.
- Parallel configuration inspection and behavioral evaluation with explicit
  decision policies.
- Optional runtime observations and deterministic checks for instructions,
  tools, model settings, loops, memory and environment state.
- HTML reports that retain measurement status, provenance and expected versus
  observed values.
- Runnable offline examples and a curated [OpenSRE/OpenKritt report
  gallery](examples/agent-evaluation/README.md).

## Verification

Local release validation on 2026-09-13 passed **428 tests**, with **21 expected
skips**: twenty unselected live-profile cases and one POSIX-only process-group
test on Windows. Both offline examples passed, and all **90 frozen acceptance
and fixture hashes** were unchanged. No live model profiles were selected.

The source distribution and wheel built successfully; package contents matched
the implementation and preserved the fixture hashes. The installed wheel loaded
and rendered both saved result formats and exposed the runtime-evidence modules.
The GitHub Actions matrix runs the release checks on Ubuntu and
Windows with Python 3.11, 3.12 and 3.13.

The timeout regression now allows the fixture's normal startup interval and
checks that the report process actually timed out, retained stdout/stderr,
stopped its direct child and cleaned up its staging directory. It no longer
assumes a new Python interpreter starts within 300 ms.

Fresh-checkout CI also checks pinned workflow bytes under Git's different
line-ending modes and reads native artifact paths without dropping Windows
drive letters or interpreting literal path characters as URL syntax.

## Scope

This is an initial implementation. Native adapters require version-specific
runtime setup. The shared runtime-observation contract does not automatically
interpret arbitrary logs; collectors must explicitly capture and map observations.

The two report case studies use small synthetic development tasks with adaptive
changes. They demonstrate specific system improvements, not general reliability,
security accuracy or isolated changes in model reasoning. Their raw local
captures, experimental harnesses and social-media drafts are not distributed.

The runnable archive example uses a separately licensed, pinned upstream
trajectory. Retained upstream test fixtures are intentionally included as test
inputs; see [third-party notices](../THIRD_PARTY_NOTICES.md).
