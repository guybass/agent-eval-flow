# Decisions

| Date | Decision | Reason | Before/after data |
| --- | --- | --- | --- |
| 2026-09-28 | Addendum B result (gpt-5.6-luna): fact stored 24/50 main vs 46/50 c1 (Fisher p=2.1e-6); silent loss 22 vs 0; controls 8/10 vs 10/10. Decision rule met | Pre-registered | after data |
| 2026-09-28 | Addendum C results: gpt-4.1-mini 29/50 vs 47/50 (p=3.6e-5), silent 20 vs 1, controls 5/10 vs 6/10; gpt-5.4-mini 16/30 vs 23/30 (p=0.10, not significant at n=30), silent 5 vs 0 (p=0.052), controls 6/6 vs 6/6. Decision rule met for all three; 5.4-mini reported as consistent in direction, not significant alone | Pre-registered | after data |
| 2026-09-28 | Reported separately, not credited to the fix: gpt-5.4-mini made no write at all in 9/30 (main) and 7/30 (c1) update runs; conditional on writing: 16/21 vs 23/23 | Model behaviour independent of the server | after data |
| 2026-09-28 | Limitation stated: 1 c1 run (4.1-mini u10) received the skip notice and ignored it | Reporting cannot force the agent to act | after data |
| 2026-09-28 | Exploratory metric kept labelled exploratory: false confirmation (reply claims saved, fact not stored) | Not pre-registered; keyword heuristic | after data |
| 2026-09-28 | Skip notice grammar fixed after the experiments ("entity that already exist" -> "exists"); experiments used the earlier wording, differing by one letter | Honest disclosure | after data |
| 2026-10-01 | **Correction:** Addendum C and an earlier README revision said gpt-5.6-luna is listed at $0.20 / $0.75 and that Addendum B's spend was an overestimate. Both are wrong: the raw data of developers.openai.com/api/docs/pricing lists $0.20 input / $1.20 output (the $0.75 came from a summarized page read). Addendum B's recorded spend was correct; total agent-run spend is about $0.48. No finding or reported number depends on prices | Accuracy; the protocol text is left as written | after data |
