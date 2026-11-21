# Proof: groundedness extraction treats period labels as non-measurements

## Rule

Groundedness extraction must not count digits inside period labels (`2018-04`,
`2018-04-07`, `September 2018`) or evidence string cells (`19484c79abc`) as
report numbers; a real measurement must still be extracted when a label or unit
is adjacent (`999.99USD`, `2018-04 revenue was 999.99`).

## Level

`test`. The class is semantic: whether a token is a measurement depends on the
report text and the evidence values, not on the shape of one line.

- Architecture: there is one extractor and one caller; no second path or
  duplicated state exists to remove, and forbidding labels in reports would
  change the product, not make the wrong code impossible.
- Type: report text is free-form `str`; no static type distinguishes a date
  label from a measurement, and the decision needs a runtime regex.
- Lint: the violation is only visible after executing the extractor over the
  report and comparing with evidence values; a line-local rule would have to
  guess at runtime semantics, which the skill's lint-locality test forbids.

## Revisions

- bad: `ce50a49` — parent of `2479ab9`, before any label handling.
- good: `d7872e5` — `dev` HEAD; `50a8260` is the last fix in the class.
- The three fixes: `2479ab9` (ISO month label and one rephrase retry),
  `7f3c5de` (full ISO-date spans, evidence string masking, first violating
  draft), `50a8260` (month + year labels).

## Commands and results

The command is the project's own runner and is the same both ways:

```bash
cd analytics-agent && uv run pytest tests/test_manager_groundedness_labels.py -q
```

Bad revision, in a clean worktree with this test file copied in and the
project venv reused through an ignored `.venv` symlink:

```bash
git worktree add --detach .worktrees/groundedness-check-ce50a49 ce50a49
ln -s ../../../analytics-agent/.venv \
   .worktrees/groundedness-check-ce50a49/analytics-agent/.venv
cp analytics-agent/tests/test_manager_groundedness_labels.py \
   .worktrees/groundedness-check-ce50a49/analytics-agent/tests/
cd .worktrees/groundedness-check-ce50a49
cd analytics-agent && uv run pytest tests/test_manager_groundedness_labels.py -q
# exit 1; 5 failed, 1 passed (the unit control is the only pass: the bad
# extractor still sees 999.99USD)
```

Good revision (this checkout, `d7872e5`):

```bash
cd analytics-agent && uv run pytest tests/test_manager_groundedness_labels.py -q
# exit 0; 6 passed
```

Intermediate fixes, same command and test file, for the ratchet:

| revision | result |
|---|---|
| `2479ab9` | exit 1; 4 failed, 2 passed (ISO month passes and the string identifier happens to pass because the boundary change hides its digits; full date, month-year, label control and unit control fail) |
| `7f3c5de` | exit 1; 2 failed, 4 passed (full date, explicit string masking and the unit control pass; month-year and label control still fail) |
| `d7872e5` | exit 0; 6 passed |

The check fails on every revision before the class was fully fixed, and passes
only after `50a8260`.

## Ratchet

None: the current tree has no pre-existing violations of this rule, so the
check is a new regression test and does not need a baseline.

## Suppressions

None. The test adds no `noqa`, `type: ignore`, or pytest marker.
