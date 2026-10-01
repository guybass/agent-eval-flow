# DeerFlow: research runs that outgrow the default recursion budget

This example imports **recorded native DeerFlow runs** into Agent Eval Flow and scores each run's tool requests with Toolscore.

We ran 27 pre-registered research questions:
- 15 SimpleQA questions
- 12 FRAMES multi-hop questions

**5 of the 27 runs needed 6 or more tool rounds.** At DeerFlow's default `recursion_limit` of 100, a lead-agent run completes only with **at most 5 tool rounds** (at most 6 model calls). The Toolscore view shows none of these runs was looping: 0 identical tool requests.

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
- DeerFlow `bytedance/deer-flow` at [`827acf51`](https://github.com/bytedance/deer-flow/tree/827acf51dc4a713d99632f0319a62ee487598c77), backend only, embedded `DeerFlowClient`
- DeerFlow's code was unmodified. We attached one observing middleware through the public `middlewares=` parameter (see [Instrumentation](#instrumentation-and-what-it-changed)).
- Model `gpt-5.6-luna` through `langchain_openai.ChatOpenAI` with the Responses API
- `web_search` = DDG, `web_fetch` = Jina (the `config.example.yaml` defaults)
- `subagent_enabled=True`, `thinking_enabled=False`, memory disabled, summarization at its default (on)
- One trial per question, in a fresh thread

The protocol was committed before the first run. Every later decision is logged with a before/after-data label in [`DECISIONS.md`](DECISIONS.md), including the corrections that came out of an independent review. Every run is logged in [`RUNLOG.md`](RUNLOG.md). Hand-graded answers are in [`MANUAL_REVIEW.json`](MANUAL_REVIEW.json).

| Experiment | Questions | recursion_limit | Completed | Correct (of completed) | Needed ≥ 6 tool rounds |
| --- | --- | --- | --- | --- | --- |
| 1: SimpleQA | 15 (seed 20260928) | 100 | 12 | 11 | 3, all cut off |
| 1b: re-run of those 3 | 3 | 300 | 3 | 3 (1 by manual review) | 1 (7 tool rounds) |
| 2: FRAMES, answers ≤ 40 chars | 12 (seed 20260929) | 300 | 11 | 10 | 2 (9 rounds; q633 still cut off at 300) |

Pooled over the 27 primary runs: 5 needed 6 or more tool rounds (95% Wilson interval ≈ 8–37%).

**Evidence loss** was the original hypothesis. It found nothing: in none of the 23 completed runs did the gold answer appear in retrieved text and then disappear before the final model request. No run delegated to a sub-agent or triggered a compaction summary.

## What 100 super-steps buys

LangGraph's `recursion_limit` counts super-steps, one per graph node executed. DeerFlow's own `subagents/turn_budget.py` explains this and translates sub-agent turn budgets accordingly (#5485).

At this commit the lead agent compiles 14 super-steps per model turn plus 9 per invocation. [`capacity_probe.py`](../../deerflow_study/capacity_probe.py) builds DeerFlow's real lead agent with a scripted fake model and a stub tool (no API, no network) and finds the exact boundary:

| recursion_limit 100 | Completes | Fails |
| --- | --- | --- |
| Pristine DeerFlow (study config) | ≤ 5 tool rounds (≤ 6 model calls) | ≥ 6 tool rounds |

`backend/docs/TUI.md` already says the limit "is a LangGraph super-step budget … rather than mapping one-to-one to conversational turns". `config.example.yaml` instead describes super-steps as "each is at least one LLM call"; at this commit a lead-agent LLM call is 14 of them.

## Instrumentation and what it changed

Our capture middleware records each lead-agent model request. It overrides `before_model` and `after_model`, and each overridden hook compiles into its own graph node, so the study's live runs cost **16 super-steps per turn**. With it attached, runs complete with at most 4 tool rounds.

The three SimpleQA cut-offs each ended on their 6th tool request, 5 of which had executed. A trajectory needing 6 or more tool rounds also fails on pristine DeerFlow (probe above). So for these trajectories the instrumentation moved the failure one round earlier; it did not create it.

The re-runs at `recursion_limit=300` finished in 3, 4 and 7 tool rounds (4, 5 and 8 turns). Runs vary: two of the three questions can finish within the pristine budget on a different trajectory.

For future captures, record through LangChain callbacks rather than graph-node hooks, so that measuring does not change the budget being measured.

## What each tool showed

- **Agent Eval Flow (outcome and evidence path).** For every run it records:
  - whether the answer matched the gold answer (normalized match; surname aliases only when the question asks for a surname; manual review for the remainder)
  - turns and tool rounds
  - whether the run ended in a recursion error
  - where the gold answer first appeared and whether it reached the final model request

  Missing evidence stays unknown: runs that ended in an exception have no usage totals, and their Toolscore coverage is marked incomplete.
- **Toolscore (tool path).** Each run is scored against a name-level contract: at least one `web_search` and one `web_fetch`.
  - Toolscore's `redundant_rate` counts calls beyond the contract's per-tool expectation, so multi-search research scores high by construction.
  - The loop evidence is a separate count of identical requests (same tool and arguments): **0 in all 30 runs**. For example, q633 made 31 distinct requests, ending in a per-match attendance lookup.
  - Toolscore now reports this directly as `identical_count` / `identical_rate`.

Without the tool-path view, a `GraphRecursionError` reads like an agent stuck in a loop. With it, these were productive research runs that outgrew a budget counted in graph steps.

## Relation to upstream

- The embedded client's and headless CLI's default of 100 is documented design (PR #4615, `TUI.md`). Gateway runs read `config.yaml` `recursion_limit` (#5390).
- The open RFC #2820 reports the recursion limit as the main blocker on GAIA and SWE-bench. These measurements, and the offline probe, are offered there as supporting evidence.
- The `config.example.yaml` wording ("each is at least one LLM call") is the one factual correction proposed.

## Provenance and publication boundaries

`capture.json` is a curated projection of retained local traces:
- **Included:** questions and gold answers (public datasets), short final answers, statuses, turn counts, token usage, tool names and arguments (search queries and URLs), and for each tool result its character count and whether the gold answer appeared.
- **Also included:** detector and Toolscore observations, manual-review decisions, and the SHA-256 of each raw trace.
- **Omitted:** page bodies, prompts, system text, full reports and local paths.

Timestamps are approximate (trace-file write time minus elapsed time). Recorded dollar cost is a lower bound: runs that end in an exception never reach DeerFlow's final usage event.

**Datasets:**
- **SimpleQA:** `Simple QA Test Set.csv` at GPT Researcher commit `6f998577`, SHA-256 `feee3f7e…` (LF bytes; identical content to the CRLF copy `334c967d…` recorded in the GPT Researcher case study). Licensed MIT ([notice](SIMPLE-EVALS-LICENSE)).
- **FRAMES:** `google/frames-benchmark` `test.tsv`, SHA-256 `4255093c…`. Licensed Apache-2.0 ([notice](FRAMES-LICENSE)).
