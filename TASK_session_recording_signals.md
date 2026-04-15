# Session-recording signals — open work

Recorded as a follow-up after a session was scored 0.9 despite being killed
by the user with no test evidence and no impact_check calls. The score
formula and recorder lacked enough negative signals to flag it.

## Already done (0.8.x patch, no version bump)

- `estimate_score`: penalty for unclean `session_end` extended from
  `{"user_killed"}` → `{"user_killed", "max_turns", "error"}` and bumped
  `-0.15` → `-0.20`. Turn-limit and error exits no longer score the same as
  voluntary `agent_done`.
- `score_breakdown(session, commit, debt)` added in `scoring.py` — returns
  `{score, signals}` where each empty signal is reported as `"no_data"`
  instead of being silently absorbed into the formula. UI / dashboard /
  improve-loop can use it to distinguish "high score with evidence" from
  "high score with nothing measured".

## Still open — needs a design decision

### 1. Test outcome integration

Today `tests_passed` is parsed only when the agent prints something that
looks like pytest output in the transcript (`recorder.py:_is_test_result`).
Brittle and incomplete. Options:

- **A. PostToolUse(Bash) hook** — capture stdout/stderr of any `pytest` /
  `npm test` / `cargo test` and parse for pass/fail. Easy to add but noisy:
  agents often run `pytest -k single`, not the full suite.
- **B. Explicit `winkers verify` command** — agent (or hook on Stop) runs
  the configured test command and writes `tests_passed` into session.json.
  Less noise, but adds a step the agent must remember.
- **C. CI integration** — bind sessions to commits, then read pass/fail from
  GitHub Actions / GitLab CI. Cleanest signal, requires `gh` / `glab`
  configured and the commit to actually be pushed.

### 2. impact_check optional → debt metrics empty

In 0.8.1 we made `impact_check` muted (Stop gate removed, hook auto in
Claude Code, optional elsewhere). For non-Claude-Code agents this means
`session.winkers_calls["impact_check"] == 0`, which leaves `compute_debt_delta`
with no graph snapshots — debt scoring degenerates.

Options:

- **A. Re-enforce impact_check** for non-Claude-Code agents (revert part of
  0.8.1 mute decision). Keeps recorder happy but reintroduces Stop-gate
  fragility.
- **B. Decouple "agent-facing impact_check" from "recorder graph snapshot"**.
  Recorder takes a snapshot at session-finalize time independently of
  whether the agent called the tool. Snapshot becomes free, optional
  becomes truly optional. Requires a finalize trigger that doesn't depend
  on the agent (see #3).

Recommend B.

### 3. session_done not enforced → no final audit

Same root cause: agent often doesn't reach `session_done`. Options:

- **A. Re-enable Stop hook gate** (revert 0.8.1 muting). Loses the reason we
  muted it.
- **B. Trigger finalize on a non-agent event** — git commit, branch push,
  task-completion signal from CLI. Bind metrics to commits, not to "agent
  saying done". Big shift in recorder model — not 0.8.x material.
- **C. Run audit unconditionally at SessionEnd hook**, no Stop gate, no
  enforcement, just collect signals. Combines well with #2/B: snapshot
  + audit happen automatically when Claude Code closes the session.

Recommend C as the smallest move that recovers the data without bringing
back the Stop gate.

## Suggested ordering

1. Implement #2/B + #3/C together — both depend on a "session-finalize on
   SessionEnd event, no agent involvement" mechanism. One PR.
2. Pick a test-integration approach (#1). Probably B or C — A is too noisy.
3. Once test signal is real, revisit `score_breakdown` to upgrade
   `"no_data"` → richer warnings (e.g. "tests skipped", "tests stale").
