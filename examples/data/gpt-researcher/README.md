# GPT Researcher: correct leads lost before the writer

This example imports a **recorded native GPT Researcher pilot** into Agent Eval
Flow. Three questions produced two correct target answers and one incorrect
target answer. Tracing the failure exposed a source handoff bug: the planner
saw relevant sources, but those sources never reached the report writer.

Run from an installed Agent Eval Flow checkout:

```sh
python examples/gpt_researcher_review.py --output demo-output/gpt-researcher
```

Open `demo-output/gpt-researcher/report.html`. This imports the curated capture,
applies recorded metrics through the production evaluation pipeline, saves and
reloads the result, and generates the HTML report. It makes **no model or search
calls** and does not regenerate the original answers or judgments.

The committed public report was generated with
`--evidence-uri https://raw.githubusercontent.com/guybass/agent-eval-flow/codex/gpt-researcher-case-study/examples/data/gpt-researcher/capture.json` and copied into
`examples/reports/03-gpt-researcher.html`. This keeps its evidence links portable
and avoids publishing a machine-specific artifact-cache path.

## Recorded experiment

- Recorded on 23 September 2026 using GPT Researcher commit
  [`6f998577`](https://github.com/assafelovic/gpt-researcher/tree/6f998577d547b1e54ec662dac63583aa11e3b84b).
- Native `conduct_research()` and `write_report()`; experimental local Codex CLI
  provider binding; requested model `gpt-5.6-luna`, low reasoning; DuckDuckGo;
  local BAAI/bge-small-en-v1.5 embeddings; three planned queries; five results per
  query; 600-word report prompt. No evaluation references went into the researcher.
- The three cases were selected from historical upstream failures **before**
  current inference. They are neither a representative sample nor a ranking of
  the hardest cases. One trial per case.
- Three research runs: 9 model calls, 15 search calls, 30 captured pages;
  402.63 seconds total research time, excluding installation and grading.
- Research usage: 96,114 input tokens (including cached input), 6,126 output
  tokens. Grading was three additional calls, accounted for separately.
- The CLI does not expose its resolved backend model in the retained JSONL,
  enforce the upstream temperature/token settings, or report a dollar charge.
  Model identity here is the **requested** model. Cost remains unknown.

| Case (zero-based CSV index) | Expected target | Reported answer | Target judgment |
| --- | --- | --- | --- |
| 3810: first UNAM mathematics PhD | Roberto Vázquez García | Roberto Vázquez García | Correct |
| 1111: Hall Medal, 2011 | Olga Polverino | Olga Polverino | Correct |
| 3787: Green Chemistry Award, 2016 | Anastas | Chirik | Incorrect |

Judgments used the upstream SimpleQA rubric with the same requested model and
were checked against the target names during review. This is target-answer
evaluation, not independent judging or a complete factuality audit of the reports.

## Where the failed case changed direction

The question asks: “What is the surname of the individual who won the Green
Chemistry Award in 2016?”

1. **LLM call 1 — role selection:** chose a scientific research role. No winner
   was selected here.
2. **Initial web search:** returned both the Yale/Royal Society of Chemistry
   leads for Paul Anastas and an EPA award page for Paul Chirik.
3. **LLM call 2 — query planning:** generated the explicit query
   `Paul Anastas 2016 Royal Society of Chemistry Green Chemistry Award`.
   The correct lead was still present.
4. **Retrieval:** that targeted search and the repeated original query returned
   empty lists after DNS errors. Other queries supplied EPA-related pages.
   Initial planning results were not fed into the page-reading pipeline.
5. **LLM call 3 — report writing:** received 15,987 characters of retrieved
   context. Its complete prompt contained zero occurrences of `Anastas`, `Yale`,
   or `Royal Society of Chemistry`; `Chirik` appeared five times. No DNS failure
   notice was included. It answered **Chirik**.

Call 3 is the first demonstrably wrong answer against the benchmark target. The
earlier observable defect is the loss of evidence between planning and writing.
The trace supports this mechanism; it does not prove a unique causal explanation
for the model's choice without a controlled end-to-end intervention.

There is a real ambiguity: the question omits the awarding organization.
[RSC's announcement](https://blogs.rsc.org/gc/2016/05/09/editorial-board-member-paul-anastas-wins-prestigious-green-chemistry-award/)
names Anastas, while [EPA's academic award page](https://www.epa.gov/greenchemistry/presidential-green-chemistry-challenge-2016-academic-award)
names Chirik for a different award. This is not an invented person or fabricated
EPA award. The report failed to preserve and resolve the competing interpretation.

## Proposed upstream fix and verification

Carry the initial results into normal URL fetching and context compression,
retaining the retriever's distinction between snippets and full content. Deduplicate
URLs through the existing visited-source path. Keep later queries, supplied-document
behavior, and MCP processing. If an initial page fails to load, continue follow-up
research. No extra LLM call is introduced.

An offline regression uses synthetic page content and mocked query planning:
the first search returns a source, then subsequent searches return nothing.
Before the patch, three source-preservation tests fail and two control tests pass.
Afterward, eight new tests and 29 adjacent tests pass (**37 total**) with outbound
network connections blocked. Coverage includes ordinary reports, subtopics,
prefetched full content, supplied documents, empty initial searches, multiple
retrievers, initial-page failures, and MCP exclusion.

This verifies source handoff, **not improved live answer accuracy**. The patch
can fetch up to the initial result limit in additional pages. It does not add
retrieval retries, repair DNS, validate every claim, or resolve award ambiguity.
A frozen-search, paired model rerun is the next quality experiment.

The tested implementation and regression tests are available on the
[upstream contribution branch](https://github.com/guybass/gpt-researcher/tree/codex/preserve-planning-sources)
(commit `95df3f0`). To reproduce the focused regression in that checkout, install
GPT Researcher's test dependencies and run
`python -m pytest tests/test_planning_sources.py -q` with `GPTR_BLOCK_NETWORK=1`.

## Provenance and publication boundaries

`capture.json` is a deliberately curated projection of retained local evidence.
It includes results, counts, source URLs, model-stage observations, and hashes of
original files. Full prompts, scraped page bodies, full reports, CLI identifiers,
and machine paths are omitted. Hashes identify the retained originals; the
public summary alone cannot independently establish their contents.

The three questions and reference answers come from the upstream
[`Simple QA Test Set.csv`](https://github.com/assafelovic/gpt-researcher/blob/6f998577d547b1e54ec662dac63583aa11e3b84b/evals/simple_evals/problems/Simple%20QA%20Test%20Set.csv),
which mirrors [OpenAI SimpleQA](https://github.com/openai/simple-evals).
Its retained SHA-256 is
`334c967dab1dea572cdf7c1052e151fe65fd4206cc31ccbe78b6e1f3c4e1be6e`.
Selection used the upstream historical 100-problem log dated 22 February 2025
(SHA-256 `c10be2dc6e531939696e3f9609058d593402b0a19321e8acf86623d9afdb8161`).
That historical run used a different configuration and is not a paired baseline.

Retained notices: [Simple Evals MIT license](SIMPLE-EVALS-LICENSE) and
[GPT Researcher Apache-2.0 license](GPT-RESEARCHER-LICENSE). Linked source pages
retain their owners' copyrights; their bodies are not redistributed here.

The `grade.report_sha256` field hashes LF-normalized UTF-8 report text, while
`raw_file_sha256` hashes original file bytes; these may differ on Windows.
