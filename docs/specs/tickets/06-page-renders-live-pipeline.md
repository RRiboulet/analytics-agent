# 06: The page renders the pipeline live

**Blocked by:** 05
**Status:** ready

## What to build
While a management request runs, the page shows the pipeline as it happens:
the decomposed sub-questions appearing first, then one card per analysis with
its question, executed SQL, result rows, answer (or error) and call counts, the
follow-up round marked apart from the initial analyses, the groundedness
verdict, and finally the report. A degraded inspect stage or a rejected report
is visible, not silent.

## Scope
- Client-side rendering of every event type defined by ticket 04, plus the
  analyst stage line for each running analysis.
- Result rows rendered as a table, truncated to the row cap the server applies,
  with the truncation visible rather than implied.
- Distinct visual treatment for follow-up evidence, failures, a degraded
  inspect stage and a groundedness rejection (with the offending attempt shown
  as rejected, never as the report).
- Collapsible evidence cards so a six-analysis run stays readable.
- Not in scope: history, persistence, authentication UI.

## Acceptance criteria
- [ ] Each event type has a visible effect; an unknown event type is ignored
      without breaking the stream.
- [ ] A completed run shows, for every evidence record: question, SQL, answer
      or error, and call counts, with its result rows backfilled from the
      terminal payload's evidence records (live events carry row counts only).
- [ ] A follow-up record is visually distinguishable from an initial analysis.
- [ ] A groundedness rejection shows the verdict and the rejected text, and the
      page does not present it as the answer.
- [ ] A run with one failed sub-analysis still renders the successful ones and
      the report.
- [ ] The served page contains the hooks the event tests assert on, so a
      rename cannot silently disconnect rendering from the stream.

## Validation
ASGI tests asserting every event type is served and the page carries its
render hooks, plus the manual checklist in this ticket (analyst and management
runs on the hosted provider). Covers spec R6 in full.
