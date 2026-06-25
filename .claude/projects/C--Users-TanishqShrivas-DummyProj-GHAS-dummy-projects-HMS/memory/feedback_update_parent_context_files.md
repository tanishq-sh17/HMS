---
name: Update CLAUDE.md and copilot-instructions.md on workflow changes
description: After making important changes to agent definitions or workflow logic, always reflect those changes in CLAUDE.md and .github/copilot-instructions.md
type: feedback
---

After making important changes to any agent definition (`.claude/agents/` or `.github/agents/`) or workflow logic, update the parent context files to keep them in sync:

- `CLAUDE.md` (project root) — update the relevant workflow description, step list, or agent behaviour notes
- `.github/copilot-instructions.md` — mirror the same updates so GitHub Copilot CLI has accurate context

**Why:** These files are the primary context loaded at the start of every session. If they describe stale workflow behaviour (e.g. old skip logic, old agent names, old step counts), future sessions will reason incorrectly about how the workflows work.

**How to apply:** After finishing edits to agent files, ask: "Does CLAUDE.md or copilot-instructions.md describe the behaviour I just changed?" If yes, update those sections before closing the task. Changes that always warrant an update: new/removed agents, changed decision logic (e.g. delta detection, multi-module support), changed step order, renamed agents, new retry counters.
