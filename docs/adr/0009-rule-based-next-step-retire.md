# ADR-0009: Rule-based auto-retire for next_step events

- **Status:** Accepted
- **Date:** 2026-07-11

## Context

next_step events silted up: nothing retired them unless the agent explicitly
superseded, and agents rarely did. Real-world briefs carried 15+ stale "next
steps" spanning weeks. Meanwhile the project principle (ADR-0003, README
Limitations) is that supersession is *explicit, not inferred* — no LLM or
similarity heuristic decides that one memory retires another.

## Decision

When `log_event` records a `next_step`, the new event's `supersedes` list is
automatically extended with every ACTIVE next_step from *earlier sessions* of
the same project (sorted ids; manual entries deduplicated). Same-session next
steps never retire each other. The brief renders surviving next steps from
finished sessions with a "(possibly stale)" marker.

Retire-at-checkpoint was considered and rejected: events are append-only, so
checkpoint-time retirement needs in-place mutation or synthetic marker events,
and it silently fails for the dominant failure mode — sessions that never
checkpoint.

## Consequences

- The rule is deterministic and auditable — it is *rule-based*, not
  *inferred*: no model, no similarity, no guessing. The "explicit, not
  inferred" principle narrows to decisions/goals/questions, where staleness is
  genuinely ambiguous; for next_step, "a newer step exists" IS the staleness
  signal.
- A next_step that was still valid alongside a new one gets retired; the
  mitigation is to re-log it (it returns as the newest step). Documented in
  README Limitations.
