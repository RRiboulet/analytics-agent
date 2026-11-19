# 04: A management run streams its pipeline as events

**Blocked by:** 02
**Status:** ready

## What to build
A management request reports its whole pipeline while it runs: the decomposed
sub-questions, each analysis starting and finishing with its SQL, rows and
answer, the inspect decision (follow-up questions, or none, or a degraded stage
with its reason), synthesis starting, the groundedness verdict, and the terminal
outcome with the report. Each nested analyst run's events are labelled with the
sub-question they belong to.

## Scope
- Manager services gain an optional sink; the manager graph emits the pipeline
  events above.
- Each sub-run gets a child sink carrying its index, question and follow-up
  flag, derived from the parent sink, so analyst events are attributable.
- `run_manager` gains an optional sink argument (additive); the CLI, artifacts
  and `ManagerRunResult` are unchanged.
- Emission stays fail-open (R10), including events emitted from nested runs.
- Not in scope: HTTP, the page, the concurrency cap.

## Acceptance criteria
- [ ] A full run emits, in stage order: request accepted, decomposed
      sub-questions, per-analysis start/finish (in any order, each carrying its
      index, SQL, row count, answer and error), inspect decision, synthesis,
      groundedness verdict, terminal status with report.
- [ ] An unusable inspect output emits a degraded event carrying the reason and
      the run still completes.
- [ ] A groundedness violation emits the rejection; the report is not exposed
      on any event as the run's report.
- [ ] Failures (decomposition error, exhausted retries, all sub-analyses
      failed) emit a terminal failure with a reason.
- [ ] A raising sink leaves status, report, error and artifacts unchanged; the
      CLI output is byte-identical for the same run.
- [ ] The recording fake proves the sequence; existing tests pass unchanged
      except where ticket 03 already adapted them.

## Validation
Recording-sink tests for the manager graph and the entrypoint. Covers spec R5
(manager portion) and R10; defines the event schema ticket 05 serves.
