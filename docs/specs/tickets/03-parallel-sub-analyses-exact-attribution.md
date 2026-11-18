# 03: Sub-analyses run in parallel and every call is still attributed

**Blocked by:** 01
**Status:** ready

## What to build
A management request with four sub-questions starts all four analyst runs at
once instead of one after another, bounded by a configurable limit (default 4),
and still reports exactly which calls each sub-run spent. Evidence stays in
sub-question order no matter which run finishes first; the follow-up round fans
out the same way.

## Scope
- The manager's analyst-runner contract gains the capability surface for that
  sub-run; the manager wraps the shared capability in a fresh counting
  decorator per run, replacing snapshot/delta attribution (which is exact only
  while runs are serial).
- Bounded `asyncio` fan-out for sub-analyses and for the follow-up round:
  outcomes collected with exceptions captured, everything settled, then the
  first exception re-raised so a crash still propagates without orphans.
- `MANAGER_MAX_PARALLELISM` setting (default 4, minimum 1).
- Unchanged: decomposition validation and the 4-question cap, the single
  follow-up round and its 2-question cap, the shared retry budget, partial
  failure recorded, all-failed fails the manager, evidence `is_follow_up`
  marking and numbering.

## Acceptance criteria
- [ ] Four sub-questions produce overlapping analyst runs: the stub runner
      proves at least two are in flight simultaneously (a serial loop
      deadlocks/times out on the same stub).
- [ ] Evidence indices follow sub-question order for any completion order.
- [ ] Per-record call counts sum to the run total with zero unattributed calls
      under fan-out, and each record still shows a flat four calls.
- [ ] A raising sub-run settles its siblings, then propagates; completed
      siblings are not left running.
- [ ] `MANAGER_MAX_PARALLELISM=1` reproduces the old serial behavior.
- [ ] The rest of the suite passes; the only edits to existing tests are the
      stub runner signatures.

## Validation
New cases in the manager graph tests plus the existing attribution invariant
test. Covers spec R1–R4 and R11 (the one intentional contract break).
