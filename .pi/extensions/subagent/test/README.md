# Subagent extension tests

Unit and behaviour tests for `.pi/extensions/subagent/`. Node runs these `.ts`
files directly (native type stripping) — there is no build step and no compiler
in the loop, so type errors are not caught here.

## Running

```bash
# 1. Link the globally installed pi packages into node_modules (idempotent)
.pi/extensions/subagent/test/setup-deps.sh

# 2. Run the suite from the repository root
node --test --test-force-exit .pi/extensions/subagent/test/*.test.ts
```

`--test-force-exit` is required: a `watchTick` already in flight during
`session_shutdown` clears the timer map and then re-arms a timer nothing will
clear again. That is a real (small) leak in the extension, recorded as local
patch 10 in its header — the flag only stops the test runner waiting on it.

The suite is safe to run from inside a pi subagent child: `createHarness` clears
`PI_TMUX_SUBAGENT_CHILD` / `PI_TMUX_SUBAGENT_RESULT`, which the extension factory
otherwise branches on, and `lifecycle.test.ts` guards that with a regression
test.

## Layout

| File | Covers |
|---|---|
| `export.test.ts` | the `__test__` block in `index.ts` still exists (guards a future re-vendor) |
| `pure.test.ts` | `shellQuote`, `PI_SUBAGENT_*` env parsing, `isSameOrDescendant`, `resolveModel`, `validateCwd` |
| `tmux.test.ts` | tmux session/socket naming, command construction, `--attach-subagent` parsing |
| `status.test.ts` | `runSummary`, `isTerminal`, `formatDuration`, `trimPane`, `truncateToolText`, `textFromAssistant` |
| `usage.test.ts` | child session usage/cost accounting |
| `lifecycle.test.ts` | launch, concurrency queueing, finalisation, failure detection, cancel, wait, status, clean |
| `helpers.ts` | env/temp-dir isolation and polling helpers |

`lifecycle.test.ts` drives the real extension factory with a fake
`ExtensionAPI`; all tmux access goes through `pi.exec`, so no tmux server or
child pi process is required. `PI_CODING_AGENT_DIR` is redirected to a temp
directory, so the tests never touch the real `~/.pi/agent` tree.

## Not covered here

Roughly 28% of `index.ts` lines are still untested. The gaps, in rough order of
size:

- **the `/subagents` dashboard** (`index.ts` ~1061-1176) — the handler is never
  invoked, because the harness stubs `registerCommand`. Layout, selection and
  the refresh interval are not covered.
- **`registerChildReporter`** (~250-314) — the entire child branch: atomic
  result writing, `agent_settled` handling, the shutdown fallback. Exercising it
  needs a real child pi process.
- **`attachToSubagentAndExit`** (~179-222) — ends in `process.exit`, so it needs
  a real terminal; the legacy `v1.` target decode is untested.
- **`subagent_clean` `all_sessions` / `delete_files`** (~1009-1024) — only the
  in-session path is covered.
- **long-poll behaviour** — the fake always reports `pane_dead = "1"`, so
  "pane still alive, keep waiting" is untested; `pi.exec`'s `timeout` option is
  ignored by the fake, so no timeout or abort path runs.

Anything requiring a live child pi process — real context handoff on resume, key
delivery for `subagent_interrupt`, and liveness when a child hangs — needs an
opt-in integration harness with model credentials, which this devcontainer does
not have.