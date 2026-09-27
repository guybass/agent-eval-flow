# Protocol (frozen before the first live run)

**Question.** Does DeerFlow retrieve the correct answer to a factual question and then lose it before its final answer?

**Target.** bytedance/deer-flow at `827acf51dc4a713d99632f0319a62ee487598c77`, backend-only, embedded `DeerFlowClient`.

**Questions.** The SimpleQA test set, the same file as Guy's GPT Researcher case study: `evals/simple_evals/problems/Simple QA Test Set.csv` at GPT Researcher commit `6f998577d547b1e54ec662dac63583aa11e3b84b`.
- SHA-256 of the raw GitHub bytes (LF): `feee3f7e7db3617e94e8fcf1977b756ec420ef8568f4e0fcbbe0e92e9d5fc032`.
- This is identical content to the Windows (CRLF) copy recorded in Guy's case study, `334c967dab1dea572cdf7c1052e151fe65fd4206cc31ccbe78b6e1f3c4e1be6e`.
- Selection: `select_questions(seed=20260928, n=15)`. Stage 1 runs the first 5; stage 2 runs the remaining 10.
- No question is added, dropped or replaced after selection.

**Settings.**
- `thinking_enabled=False`, `subagent_enabled=True`, `plan_mode=False`, `recursion_limit=60`.
- Tools: `web_search` = DDG, `web_fetch` = Jina (the config.example.yaml defaults). Memory, sandbox/bash, file-write, browser and image tools are disabled.
- Model: the single OpenAI model recorded in DECISIONS.md before stage 1.
- One trial per question, in a fresh thread per question. Each question is sent with "Answer concisely." appended.

**Grading.** A deterministic, normalized match of the gold answer in the final answer. The only aliases are ones derived from the gold string itself: the full string, and the surname for multi-word names. Empty or ambiguous answers go to a manual-review queue, whose decisions are recorded in DECISIONS.md. No LLM judge.

**Evidence-loss definition.** A question counts as a loss when it was answered wrongly and both of these hold:
- the normalized gold answer appears in at least one retrieval-side stage: a tool result, a sub-agent event, or an earlier model request excluding the question and system prompt
- it does not appear in the last captured model request

A stage whose capture coverage is unknown is never counted as a loss point.

**Gates.**
- G1 (after stage 1): capture must work (lead model requests recorded for every completed run), and the projected cost of 15 questions must be ≤ $4.00. Otherwise stop.
- G2 (after stage 2): at least one confirmed evidence-loss case. Otherwise stop and record a negative result.
- G3 (after the mechanism is found): the defect must be a deterministic code path, not model judgment. Otherwise no PR.

**Budget.** OpenAI hard usage limit $5.00; the runner stops before a new question at $4.00 estimated spend.

**Publication boundaries.** Only curated projections are published: counts, stage observations, URLs and hashes. Raw traces, prompts and page bodies stay local.

## Experiment 2 (pre-registered 2026-09-28, before any FRAMES run)

**Question.** How many lead-agent turns does DeerFlow need on multi-hop research questions, and how often is that more than the default budget allows (6 turns at `recursion_limit` 100 with `subagent_enabled=True`: 14 super-steps per turn + 9 per invocation, measured on both the study and the shipped example config)? Secondary: does evidence loss appear when sub-agents and compaction are exercised?

**Questions.** Google FRAMES `test.tsv` (Hugging Face `google/frames-benchmark`, 824 rows, SHA-256 `4255093c93b595b5b04c7c8dde290b48ec87d72ca0fb0b760d9dd02740d669ff`). Rows with a gold answer of 40 characters or fewer are eligible, so answers can be graded deterministically. `select_questions(seed=20260929, n=12)` samples from the eligible rows.

**Settings.** The same as experiment 1, except that `recursion_limit=300` is passed per call, so that runs can finish and their turn counts are observed. One trial per question.

**Primary measure.** Lead-agent model turns per completed run, and the share of runs using more than 6 turns. A run that used more than 6 turns could not have finished under the default of 100. This is stated as "needed more than the default allows", not as a paired re-run.

**Secondary.** Grade (deterministic; ambiguous answers go to manual review, recorded in DECISIONS.md), evidence-loss detector, and the Toolscore view (searches, fetches, sub-agent tasks, identical repeats).

**Budget.** The runner stops before starting a question once total estimated spend (including the $0.24 already spent) reaches $3.00.
