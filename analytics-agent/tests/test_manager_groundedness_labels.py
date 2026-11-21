"""Regression checks: period labels and string cells are not measurements.

Groundedness extraction used to count the digits inside date/period labels
(``2018-04``, ``2018-04-07``, ``September 2018``) and evidence string cells
(``19484c79abc``) as report numbers. A report that merely quoted a label was
then rejected as fabricated.

The rule: labels and evidence strings are not measurements; a real number is
still extracted even when a unit or a label sits next to it.

Proof: this file fails at ``ce50a49`` (before ``2479ab9``) and passes at
``d7872e5`` (after ``50a8260``); see ``docs/proof/groundedness-labels.md``.
"""

from app.manager.evidence import EvidenceRecord
from app.manager.synthesize import extract_report_numbers, groundedness_violation


def _evidence(rows: list[dict] | None) -> EvidenceRecord:
    return EvidenceRecord(sub_index=0, sub_question="q", rows=rows)


def test_iso_month_label_is_not_a_measurement() -> None:
    assert extract_report_numbers("period 2018-04 saw growth") == []


def test_iso_date_range_is_not_a_measurement() -> None:
    assert extract_report_numbers("from 2018-04-07 to 2018-05-01") == []


def test_month_year_labels_are_not_measurements() -> None:
    assert extract_report_numbers("September 2018 order activity") == []
    assert extract_report_numbers("results from Jan 2018 through Dec 2018") == []


def test_evidence_string_cells_do_not_create_numbers() -> None:
    evidence = [_evidence([{"seller": "19484c79abc", "orders": 5}])]
    assert groundedness_violation("Seller 19484c79abc had 5 orders.", evidence) is None


def test_labels_do_not_hide_real_measurements() -> None:
    assert extract_report_numbers("2018-04 revenue was 999.99") == [999.99]
    assert extract_report_numbers("September 2018 revenue was 999.99") == [999.99]

    evidence = [_evidence([{"revenue": 10.5}])]
    assert groundedness_violation("Period 2018-04 revenue was 999.99.", evidence) is not None


def test_units_do_not_hide_real_measurements() -> None:
    assert extract_report_numbers("revenue was 999.99USD") == [999.99]
