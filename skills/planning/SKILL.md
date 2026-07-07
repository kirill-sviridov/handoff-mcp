---
name: session-planning
description: >-
  Break a large or multi-step task into session-sized chunks and persist the
  plan across sessions using handoff-mcp memory. Use when the user starts
  something big or asks to plan it — in English ("let's plan this", "break this
  down", "where do I start", "this is a big one", "make a roadmap") or Russian
  ("давай распланируем", "разбей на этапы/куски", "с чего начать", "составь
  план", "это надолго", "набросай дорожную карту"). Companion to the
  session-handoff skill; works best with the handoff-mcp MCP server connected.
---

# Session-sized planning with handoff-mcp

Turn a big task into an ordered list of chunks, each small enough to finish (or
make real progress on) in one focused working session, and store the plan in
memory so it survives across sessions.

This skill only uses the existing handoff-mcp tools — it adds no new ones. If the
server isn't connected, you can still produce the plan, but warn the user it
won't persist between sessions.

## 1. Scope it (briefly)

Ask at most one or two clarifying questions if the goal is genuinely ambiguous
(target, constraints, what "done" means). Don't over-interrogate — prefer
sensible assumptions and state them.

## 2. Decompose into session-sized chunks

Break the work into an **ordered** list of milestones. Each chunk should be:

- a coherent, independently completable piece (something you could checkpoint),
- roughly one focused session of work,
- phrased as an outcome ("Auth flow works end-to-end"), not a vague theme.

Front-load the riskiest/most-uncertain chunks so dead-ends surface early.

## 3. Estimate honestly

Give a **rough** session count, explicitly labelled as a guess — e.g. "≈4–6
sessions, depends on X". Do **not** present a precise number as fact; LLMs
estimate effort poorly. The real signal is progress against the chunk list, not
the original guess.

## 4. Persist the plan in memory

- Save the plan as a durable entity: `note_entity("Plan", "<ordered chunks>")`
  (or a project-specific name). Reference it as `[[Plan]]`.
- Record the overall goal: `log_event(type="goal", ...)`.
- Set the first chunk as the immediate next action:
  `log_event(type="next_step", "Chunk 1: …")`.

## 5. Work the plan, one chunk per session

- Start each session with `get_brief` (the session-handoff skill covers this) to
  see the goal, current chunk, and what's done.
- When a chunk is finished, record it: `log_event(type="decision"/"next_step", …)`
  and append progress to the plan: `note_entity("Plan", "DONE: chunk N — …")`.
  Set the next chunk as the new `next_step`.
- At the end of the session, `checkpoint` so the next session resumes mid-plan.

## 6. Re-plan when reality changes

If scope shifts or a chunk turns out wrong, update the `[[Plan]]` entity and, if a
decision is reversed, use `log_event(..., supersedes=[old_id])` so the outdated
step drops out of future briefs. The plan is living, not frozen.
