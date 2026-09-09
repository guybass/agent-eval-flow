---
name: fixture-format
description: Produce a small structured receipt for the E2E mission.
---

Read the supplied task ID and text. Return an object with `task_id`, `message`
and `items` (an array of strings). Preserve the task ID. No external resources
are needed. The scripted fixture recognizes this file as its formatting skill;
live harnesses must load it using their native skill/instruction mechanism.
