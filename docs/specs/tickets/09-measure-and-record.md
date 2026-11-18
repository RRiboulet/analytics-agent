# 09: The speedup and the surface are measured and recorded

**Blocked by:** 03, 06
**Status:** ready

## What to build
The Milestone's claims become recorded evidence: both benchmarks re-run on the
same hosted provider as the M7.6 baseline, with before/after numbers for
management-scenario latency, per-analyst-run call counts and analyst pass rate,
plus the project docs updated to match what now exists — including the amended
orchestration decision and the new demo surface.

## Scope
- Re-run the analyst and manager benchmarks on the same provider and model
  recorded in the M7.6 baseline; capture p50/p95 per scenario, per-run call
  counts and pass rates.
- `PLAN.md`: a new M8 section (goal, completed items, the before/after table,
  the exact commands that produced it) and an in-place amendment of D009's
  sequential rule, with the reason and what did not change (discrete calls,
  budgets, groundedness).
- Update the READMEs for the web surface: how to run it, loopback-only
  exposure, hosted-provider default and the local-path caveat (parallelism is a
  hosted-model property).
- Record the honest limits: no local-speedup claim, no token/dollar accounting,
  no history.
- Not in scope: writing new benchmarks or changing the evaluation dataset.

## Acceptance criteria
- [ ] `PLAN.md` contains the before/after table with the commands used, and no
      estimated number presented as measured.
- [ ] D009 is amended in place, and the unchanged parts of the decision are
      still stated.
- [ ] The READMEs describe how to start the surface and what a visitor sees.
- [ ] The benchmark pass rates are reported as they came out, including any
      regression, with the cause or an open question.
- [ ] The stated latency improvement is derived from the two runs on the same
      provider and model, not from extrapolation.

## Validation
The two benchmark outputs (kept untracked as today), a diff review of the docs,
and a manual end-to-end run of the page. Covers spec R12 and D9.
