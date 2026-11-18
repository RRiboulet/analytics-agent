# 07: One question answers inside the same page

**Blocked by:** 05
**Status:** ready

## What to build
The same page answers a single analytical question in seconds, with no manager
stages shown, and the mode is visible before submitting. A visitor's first
interaction returns quickly; the management request remains the payoff.

## Scope
- A mode field on the run request (single question / management request);
  an unknown mode is rejected with a clear message.
- Analyst mode drives the analyst entrypoint and renders its events
  (stages, retries, SQL, rows, answer, failure) in the same layout, without
  decomposition/inspect/synthesis sections.
- Mode is displayed in the UI and carried in the run's first event.
- Not in scope: changing the manager's behavior or the analyst's budgets.

## Acceptance criteria
- [ ] An analyst-mode request returns the answer with its SQL, rows and call
      counts, and no manager stages are presented.
- [ ] A manager-mode request through the same page behaves as in ticket 06.
- [ ] An invalid mode is rejected with a clear error and no run is started.
- [ ] A failed analyst run (unsafe SQL, query error, exhausted retries) renders
      the failure and the last SQL, not an empty answer.

## Validation
ASGI tests for both modes and the invalid mode, plus a manual check. Covers spec
R7 and part of D5.
