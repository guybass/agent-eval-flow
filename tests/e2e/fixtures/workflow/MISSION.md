# Investigate the HTML rendering incident

You are reviewing an owned application's use of MarkupSafe. The task gives you
`task_id`, a fresh `nonce`, and an incident file. Use the downloaded dependency;
do not rely on a different installed version or conclusions from memory.

1. Discover the available input files and read the incident/case inventory.
2. Choose and execute the supplied reproduction cases. You may group cases or
   run them separately. The tool returns actual outputs and source locations
   obtained from the imported implementation.
3. After receiving reproduction results, inspect relevant source at the returned
   locations. For each follow-up read, pass the reproduction's `receipt_id` as
   `based_on_receipt_id`. Inspect other downloaded documentation/tests as useful.
4. Return a structured report covering every supplied case. Copy each observed
   output exactly, explain what you observed, and cite a reproduction receipt
   plus a subsequent source-read receipt. Include your assumptions or unanswered
   questions. Do not claim a defect in the dependency merely because two call
   sites behave differently.

Three tools are available:

| Tool | Arguments | Returned evidence |
| --- | --- | --- |
| `workflow.inventory` | `{}` | Incident, cases, source revision, file paths and hashes |
| `workflow.run_cases` | `{"case_ids": ["untrusted-html", "pretrusted-html"]}` | Actual case inputs/outputs/types and implementation source hints |
| `workflow.read_source` | `{"path": "src/markupsafe/__init__.py", "start_line": 1, "end_line": 50, "based_on_receipt_id": "<receipt>"}` | Exact numbered source span, source hash and receipt |

Each response is a receipt containing `receipt_id`, `invocation_id`, `nonce`,
`action`, `arguments`, `result`, `source_manifest_sha256`, and `tool_sha256`.
The runtime handles the command transport; native CLIs may expose the tool
through their terminal tool. The action and arguments have the same meanings.
Use the schema in `report.schema.json` for the final response. Do not invent
receipt IDs, execution observations, or source contents. No source modifications,
package installation, external requests, or benchmark grade are needed.

The workflow requires a real evidence-dependent follow-up, not a prescribed
number of model turns. Within the execution limits, decide how to group the
cases and which relevant source spans to inspect. Report observable findings;
private reasoning is neither requested nor part of the trace contract.
