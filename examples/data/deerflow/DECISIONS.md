# Decisions

| Date | Decision | Reason | Before/after seeing pilot data |
| --- | --- | --- | --- |
| 2026-09-28 | Target bytedance/deer-flow at 827acf51 | 83k stars; research agent like GPT Researcher; many hand-offs (sub-agents, compaction) | before |
| 2026-09-28 | Model via OpenAI API, not a CLI coding agent | DeerFlow's CLI coding-agent entries are ACP delegation targets, not LLM providers | before |
| 2026-09-28 | Model = `gpt-5.6-luna` at $0.20 input / $1.20 output per 1M tokens (standard tier, developers.openai.com/api/docs/pricing, read 2026-09-28) | Same model as Guy's GPT Researcher case study, so the two case studies are comparable; estimated ~$0.03 per question (~100k input, ~5k output tokens), ~$0.50 for 15. Tool-calling support is confirmed by the smoke run, not assumed | before |
