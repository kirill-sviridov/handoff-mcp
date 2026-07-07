---
name: session-handoff
description: >-
  Manage persistent cross-session memory with the handoff-mcp server. Use at the
  START of a session to load where the last one left off, and whenever the user
  signals they are stopping, switching context, or moving to a next session — in
  English ("let's continue in the next session", "wrap up", "save progress",
  "that's it for today", "checkpoint this") or Russian ("го в следующую сессию",
  "на сегодня всё/хватит", "сохрани прогресс", "давай заканчивать", "сделай
  слепок", "продолжим потом"). Also use when the user refers to past work, a
  previous decision, or another project. Requires the handoff-mcp MCP server.
---

# Session hand-off with handoff-mcp

This skill encodes *when* to use the handoff-mcp memory tools. The tools
themselves (`get_brief`, `log_event`, `checkpoint`, `search_memory`,
`note_entity`) describe *what* they do; this skill is the workflow around them.

If the handoff-mcp tools are not available, tell the user the MCP server isn't
connected and stop — do not fabricate memory.

## 0. Setup — make sure this project has a memory instruction file

The first time you use handoff-mcp in a project, check that the project's
agent-instruction file tells future sessions to use memory — otherwise an agent
that hasn't loaded this skill (or a different client) won't know to. Pick the
file your client reads:

| Client | File |
|--------|------|
| Claude Code / Desktop | `CLAUDE.md` |
| Codex / generic agents | `AGENTS.md` |
| Cursor | `.cursor/rules/handoff.mdc` (or legacy `.cursorrules`) |
| Windsurf | `.windsurfrules` |

If that file is missing, or has no handoff section, create/append this block
(don't duplicate it, don't clobber existing content):

```markdown
## Memory (handoff-mcp)
At the START of a session, call `get_brief` and resume from the next step —
don't redo settled decisions or repeat recorded dead-ends. As we work,
proactively `log_event` goals/decisions(+why)/dead-ends/files/questions/next step
— ONE atomic item per call (1-2 sentences), not a session summary; reference
durable notes inline as `[[Entity]]`. If a decision is reversed, log the new one
with `supersedes` = the old id. On "stopping / го дальше / на сегодня всё" call
`checkpoint`. When the user mentions past or other-project work, `search_memory`.
```

## 1. At the start of a session — load context first

Before doing substantive work on a project, call **`get_brief`** to load the
prior session's goal, next step, key decisions, dead-ends, open questions, and
related knowledge. Briefly orient the user ("Resuming: last time you …; next
step was …") and continue from the next step. Do **not** redo settled decisions
or repeat recorded dead-ends.

## 2. During the session — record progress as it happens

Call **`log_event`** proactively (don't wait to be asked) when:

- the goal is set or changes → `type="goal"`
- a non-trivial decision is made → `type="decision"` (include the *why*)
- you reverse an earlier decision → `type="decision"` with `supersedes=[old_id]`
- something was tried and failed → `type="deadend"` (so it isn't repeated)
- an important file is touched → `type="file"`
- an open question arises → `type="question"`
- the next concrete action is identified → `type="next_step"`

**Keep events atomic.** One item per `log_event` call, 1-2 sentences (a decision
+ its *why*, a single dead-end, one next step). Log several small events rather
than dumping a whole session summary into one — the brief must stay skimmable. If
you've done a lot, make several focused calls.

Use **`note_entity`** for durable project knowledge that outlives one session
(architecture, conventions, what a component does). Reference entities inline as
`[[Name]]` — both in events and notes — so they surface in the brief's "Related
knowledge".

## 3. At the end of a session — checkpoint

When the user signals they are stopping, switching, or moving on — e.g. "го в
следующую сессию", "на сегодня всё", "сохрани прогресс", "давай заканчивать",
"that's it for today", "let's continue next time" — **this is your cue to call
`checkpoint`** with a one-line summary, without being explicitly told to.

Before checkpointing, make sure the current state is captured: if the next step
or a recent key decision hasn't been logged yet, `log_event` it first, then
`checkpoint`. Show the user the returned brief so they can confirm the hand-off.

## 4. When the user references prior or other-project work

If the user says things like "how did we solve X before", "in one of my projects
we did …", "what did we decide about …", "last time" — call **`search_memory`**.
Use `scope="all"` to recall across every project (the default), `scope="current"`
to stay in this one. Pull the relevant past decision into the conversation.
