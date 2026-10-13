"""Load and validate the management-level evaluation dataset (M7.6).

Management scenarios are graded structurally rather than against a reference
SQL result set: decomposition validity, groundedness, completion, and the
D009 hard budgets are the grading criteria.
"""

from dataclasses import dataclass

import yaml

from app.evaluation.dataset import DIFFICULTIES, DatasetError, _require


@dataclass
class ManagerEvalCase:
    """One management-level scenario with no reference SQL.

    Structural grading only — the request is graded on decomposition
    validity, groundedness, completion and budget adherence.
    """

    case_id: str
    request: str
    difficulty: str
    category: str


def _parse_entry(raw: dict, index: int) -> ManagerEvalCase:
    where = f"manager dataset entry #{index}"
    _require(isinstance(raw, dict), f"{where}: expected a mapping")
    case_id = raw.get("id")
    request = raw.get("request")
    if not isinstance(request, str) or not request.strip():
        request = raw.get("question")
    difficulty = raw.get("difficulty")
    category = raw.get("category")
    _require(
        isinstance(case_id, str) and case_id.strip(), f"{where}: 'id' must be a non-empty string"
    )
    _require(
        isinstance(request, str) and request.strip(),
        f"{where} ({case_id}): 'request' (or 'question') must be a non-empty string",
    )
    _require(
        difficulty in DIFFICULTIES,
        f"{where} ({case_id}): 'difficulty' must be one of {DIFFICULTIES}",
    )
    _require(
        isinstance(category, str) and category.strip(),
        f"{where} ({case_id}): 'category' must be a non-empty string",
    )
    return ManagerEvalCase(
        case_id=case_id.strip(),
        request=request.strip(),
        difficulty=difficulty,
        category=category.strip(),
    )


def load_manager_dataset(path: str) -> list[ManagerEvalCase]:
    """Load and validate a management-level evaluation dataset from YAML."""
    try:
        with open(path, encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except FileNotFoundError as error:
        raise DatasetError(f"evaluation dataset not found: {path}") from error
    except yaml.YAMLError as error:
        raise DatasetError(f"evaluation dataset is not valid YAML: {path}") from error

    _require(
        isinstance(raw, list) and raw,
        f"{path}: manager dataset must be a non-empty list of cases",
    )
    cases = [_parse_entry(entry, index) for index, entry in enumerate(raw)]

    seen: set[str] = set()
    for case in cases:
        _require(
            case.case_id not in seen,
            f"duplicate manager case id: {case.case_id}",
        )
        seen.add(case.case_id)
    return cases


def select_manager_cases(
    cases: list[ManagerEvalCase],
    *,
    case_id: str | None = None,
    first: int | None = None,
    last: int | None = None,
    category: str | None = None,
    difficulty: str | None = None,
) -> list[ManagerEvalCase]:
    if first is not None or last is not None:
        start = first if first is not None else 1
        end = last if last is not None else len(cases)
        if start < 1 or end < start or end > len(cases):
            raise DatasetError(
                f"case range [{start}, {end}] is inverted or outside 1..{len(cases)}"
            )
        cases = cases[start - 1 : end]
    if case_id is not None:
        matching = [case for case in cases if case.case_id == case_id]
        if not matching:
            raise DatasetError(f"unknown manager case id: {case_id}")
        cases = matching
    if category is not None:
        cases = [case for case in cases if case.category == category]
    if difficulty is not None:
        if difficulty not in DIFFICULTIES:
            raise DatasetError(f"difficulty must be one of {DIFFICULTIES}")
        cases = [case for case in cases if case.difficulty == difficulty]
    if not cases:
        raise DatasetError("selection matched no manager cases")
    return cases
