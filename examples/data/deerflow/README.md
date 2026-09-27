# DeerFlow: research runs cut off by the default recursion budget

This example imports **recorded native DeerFlow runs** into Agent Eval Flow and scores each run's tool requests with Toolscore.

We ran 27 pre-registered research questions:
- 15 SimpleQA questions
- 12 FRAMES multi-hop questions

Of those 27 runs, 5 (≈19%) needed more lead-agent turns than DeerFlow's default budget allows. Three of them were cut off by it with no answer.

The Toolscore view shows these runs were not looping: none of them repeated an identical tool request.

Tracing the limit exposed a small code defect and a documentation gap:
- The embedded `DeerFlowClient` ignores the configured `recursion_limit`.
- The default of 100 LangGraph super-steps buys only about 6 lead-agent turns, because each turn is 14 super-steps.

Run from an installed Agent Eval Flow checkout, with the `toolscore` extra:

```sh
python examples/deerflow_review.py --output demo-output/deerflow
```

Open `demo-output/deerflow/report.html`, which links to `tools.html`. The script:
- imports the curated capture
- applies recorded outcomes and Toolscore through the production evaluation pipeline
- saves and reloads the result, then renders both reports

It makes **no model, search or DeerFlow calls**.

## Recorded experiments

Recorded on 28 September 2026. Settings shared by both experiments:
- DeerFlow `bytedance/deer-flow` at [`827acf51`](https://github.com/bytedance/deer-flow/tree/827acf51dc4a713d99632f0319a62ee487598c77), backend only, embedded `DeerFlowClient`, unmodified for all study runs
- Model `gpt-5.6-luna` through `langchain_openai.ChatOpenAI` with the Responses API
- `web_search` = DDG, `web_fetch` = Jina (the `config.example.yaml` defaults)
- `subagent_enabled=True`, `thinking_enabled=False`, memory disabled, summarization at its default (on)
- One trial per question, in a fresh thread

The protocol, including question selection, settings, grading and stop gates, was committed before the first run. Every later decision is logged with a before/after-data label in [`DECISIONS.md`](DECISIONS.md). Every run is logged in [`RUNLOG.md`](RUNLOG.md).

| Experiment | Questions | recursion_limit | Completed | Correct (of completed) | Needed > 6 turns |
| --- | --- | --- | --- | --- | --- |
| 1: SimpleQA | 15 (seed 20260928) | 100 (client default) | 12 | 11 | 3, cut off at turn 6 |
| 1b: re-run of the 3 cut-off questions | 3 | 300 | 3 | 3 | 1 (8 turns) |
| 2: FRAMES, answers ≤ 40 chars | 12 (seed 20260929) | 300 | 11 | 10 | 2 (10 turns; q633 cut off at 300 after 19) |

- Experiment 1 observed the cut-off under the default directly.
- Experiment 2 observed turn demand with a higher limit. A run needing more than 6 turns could not have finished at 100.
- The two designs differ, so each is reported separately.
- Pooled over the 27 primary runs: 5 needed more than 6 turns (95% Wilson interval ≈ 8–37%).

**Evidence loss:** none. In no run did the gold answer appear in retrieved text and then disappear before the final model request. No run delegated to a sub-agent or triggered a compaction summary.

## Why 100 steps means about 6 turns

LangGraph's `recursion_limit` counts super-steps, one per graph node executed. DeerFlow's own `subagents/turn_budget.py` explains this and translates sub-agent turn budgets accordingly (#5485).

Counting the compiled lead-agent graph the same way:

| Configuration | Super-steps per turn | Per invocation | Turns within 100 |
| --- | --- | --- | --- |
| Study config | 14 | 9 | 6 |
| Shipped `config.example.yaml`, `subagent_enabled=True` | 14 | 9 | 6 |
| Shipped `config.example.yaml`, `subagent_enabled=False` | 13 | 9 | 7 |

The per-turn count is `before_model` + `model` + `after_model` + `tools` nodes. The per-invocation count is `before_agent` + `after_agent` nodes. `config.example.yaml` describes each super-step as "at least one LLM call"; at this commit each lead-agent LLM call is 14 super-steps.

The three cut-off SimpleQA runs each ended after exactly 6 model turns, with 6 distinct tool requests:

> `GraphRecursionError: Recursion limit of 100 reached without hitting a stop condition.`

Re-run with `recursion_limit=300`, all three completed with correct answers (1949; Mandlesizwe Zulu; Surendra Kunwar), using 4, 5 and 8 turns. Runs vary, so this shows the budget ended those runs. It does not show that these questions always need more than 6 turns.

## What each tool showed

- **Agent Eval Flow (outcome and evidence path).** For every run it records:
  - whether the answer matched the gold answer
  - how many lead turns it used
  - whether the run ended in a recursion error
  - where the gold answer first appeared and whether it reached the final model request

  Missing evidence stays unknown: runs that ended in an exception have no usage totals.
- **Toolscore (tool path).** Each run is scored against a name-level contract: at least one `web_search` and one `web_fetch`. Toolscore's `redundant_rate` counts calls beyond the contract's per-tool expectation, so multi-search research scores high by construction; it is not a loop detector. The loop evidence is a separate count of identical requests (same tool and arguments): **0 in all 27 runs**, including the five long ones. For example, q633 made 31 distinct requests, ending in a per-match attendance lookup, before the limit stopped it.

Without the tool-path view, a `GraphRecursionError` reads like an agent stuck in a loop. With it, these were productive research runs stopped by a budget denominated in the wrong unit.

## Upstream contribution

**Defect.** `DeerFlowClient._get_runnable_config` sends `recursion_limit=100` whenever the caller does not pass one, ignoring `config.yaml` `recursion_limit` and `max_recursion_limit`. The Gateway (#5390) and the headless CLI (#4615) already use the configured value.

**Proposed fix.** Default to the configured value, capped at `max_recursion_limit`. Explicit per-call values are unchanged; invalid configured values fall back to 100. With the shipped config (`recursion_limit: 100`), behavior is identical to before.

**Verification.** A deterministic regression test, `backend/tests/test_client_recursion_limit.py`:
- on `main`: 2 failed, 6 passed (the 6 are controls)
- on the fix branch: 8 passed

The full backend suite (`make test`) gives 20,744 passed and 10 failed. The same 10 fail on unpatched `main`: optional browser and crawler integrations not installed in this environment. `make lint` is clean.

**Scope.** This verifies that configuration reaches embedded runs. It does not change the default budget. Whether the default should be translated into lead-agent turns (as #5485 does for sub-agents), documented differently, or raised is left to the maintainers. The symptom matches the open RFC #2820 on GAIA and SWE-bench.

## Provenance and publication boundaries

`capture.json` is a curated projection of retained local traces:
- **Included:** questions and gold answers (public datasets), short final answers, statuses, turn counts, token usage, tool names and arguments (search queries and URLs), and for each tool result its character count and whether the gold answer appeared.
- **Also included:** detector and Toolscore observations, and the SHA-256 of each raw trace.
- **Omitted:** page bodies, prompts, system text, full reports and local paths.

Timestamps are approximate (trace-file write time minus elapsed time). Recorded dollar cost is a lower bound: runs that end in an exception never reach DeerFlow's final usage event.

**Datasets:**
- **SimpleQA:** `Simple QA Test Set.csv` at GPT Researcher commit `6f998577` (the file used in the GPT Researcher case study), SHA-256 `feee3f7e…` (LF bytes; identical content to the CRLF copy `334c967d…`). Licensed MIT ([notice](SIMPLE-EVALS-LICENSE)).
- **FRAMES:** `google/frames-benchmark` `test.tsv`, SHA-256 `4255093c…`. Licensed Apache-2.0 ([notice](FRAMES-LICENSE)).
