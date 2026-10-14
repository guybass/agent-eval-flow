# Approval required for main

- Every push or merge that updates the remote `main` branch requires the user's fresh, explicit approval of the final, reviewable commit or commit range. This includes direct pushes, pull request merges, API updates, automated merges, and releases that update `main`.
- Before requesting approval, finish the change and its checks, and show the user the destination repository/branch, commit(s), concise change summary, and validation results. Do not update `main` until that approval arrives.
- A previous approval, a general instruction to work autonomously, or an earlier request to commit/push does not authorize a later update to `main`. If the approved commits or scope change, obtain new approval.
- Preparing commits on feature branches is allowed within the task's scope. Never approve a pull request on the user's behalf, enable an automatic merge, or bypass branch protections to satisfy this approval requirement.
