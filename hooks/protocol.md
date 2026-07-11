Handoff memory protocol (handoff-mcp plugin):

- **Session start:** call `mcp__handoff__get_brief` before doing anything else,
  to load where the previous session left off.
- **During work:** call `mcp__handoff__log_event` for each atomic signal as it
  happens — a goal set or changed, a non-trivial decision (include the *why*),
  a dead-end worth not repeating, an open question, or the concrete next step.
  One item per call, 1-2 sentences. Do NOT batch a whole-session summary.
- **Session end / context switch:** call `mcp__handoff__checkpoint` with a
  one-line summary to finalise the brief. A checkpoint only captures what
  `log_event` recorded — logging as you go is what makes the next brief useful.
