"""Load and validate the version-controlled evaluation dataset.

Each dataset entry pairs a natural-language analytical question with a
reference SQL statement executed against the same fixed Olist seed. Reference
SQL is validated through the existing read-only SQL safety boundary at load
time so an unsafe statement can never enter the evaluation process.
"""

import yaml

from app.sql_safety import UnsafeQueryError, validate_and_bound_query

DIFFICULTIES = ("easy", "medium", "hard")


class DatasetError(ValueError):
    """The evaluation dataset is missing, malformed, or internally inconsistent."""


class EvalCase:
    """One benchmark question with its reference answer definition."""

    __slots__ = (
        "case_id",
        "category",
        "difficulty",
        "expected_tables",
        "ordered",
        "question",
        "reference_sql",
    )

    def __init__(
        self,
        case_id: str,
        question: str,
        difficulty: str,
        category: str,
        reference_sql: str,
        expected_tables: list[str],
        ordered: bool,
    ) -> None:
        self.case_id = case_id
        self.question = question
        self.difficulty = difficulty
        self.category = category
        self.reference_sql = reference_sql
        self.expected_tables = expected_tables
        self.ordered = ordered


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DatasetError(message)


def _parse_entry(raw: dict, index: int) -> EvalCase:
    where = f"dataset entry #{index}"
    _require(isinstance(raw, dict), f"{where}: expected a mapping")
    case_id = raw.get("id")
    question = raw.get("question")
    difficulty = raw.get("difficulty")
    category = raw.get("category")
    reference_sql = raw.get("reference_sql")
    expected_tables = raw.get("expected_tables")
    ordered = raw.get("ordered", False)

    _require(
        isinstance(case_id, str) and case_id.strip(), f"{where}: 'id' must be a non-empty string"
    )
    _require(
        isinstance(question, str) and question.strip(),
        f"{where} ({case_id}): 'question' must be a non-empty string",
    )
    _require(
        difficulty in DIFFICULTIES,
        f"{where} ({case_id}): 'difficulty' must be one of {DIFFICULTIES}",
    )
    _require(
        isinstance(category, str) and category.strip(),
        f"{where} ({case_id}): 'category' must be a non-empty string",
    )
    _require(
        isinstance(reference_sql, str) and reference_sql.strip(),
        f"{where} ({case_id}): 'reference_sql' must be a non-empty string",
    )
    _require(
        isinstance(expected_tables, list) and expected_tables,
        f"{where} ({case_id}): 'expected_tables' must be a non-empty list",
    )
    _require(
        all(isinstance(t, str) and t.strip() for t in expected_tables),
        f"{where} ({case_id}): every entry of 'expected_tables' must be a non-empty string",
    )
    _require(isinstance(ordered, bool), f"{where} ({case_id}): 'ordered' must be a boolean")
    return EvalCase(
        case_id=case_id,
        question=question,
        difficulty=difficulty,
        category=category,
        reference_sql=reference_sql.strip(),
        expected_tables=list(expected_tables),
        ordered=ordered,
    )


def select_cases(
    cases: list[EvalCase],
    *,
    case_id: str | None = None,
    first: int | None = None,
    last: int | None = None,
    category: str | None = None,
    difficulty: str | None = None,
) -> list[EvalCase]:
    """Select a subset of benchmark cases.

    The 1-based inclusive index range always addresses the *raw dataset*
    order (overall benchmark positions), so ``first=1, last=10`` selects the
    first ten benchmark cases regardless of any other filters. The id,
    category and difficulty filters then narrow that selection further
    ("revenue cases among overall cases 2 to 3"). Raises ``DatasetError``
    for an unknown id, an invalid difficulty, an inverted/out-of-bounds
    range, or an empty result.
    """
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
            raise DatasetError(f"unknown case id: {case_id}")
        cases = matching
    if category is not None:
        cases = [case for case in cases if case.category == category]
    if difficulty is not None:
        if difficulty not in DIFFICULTIES:
            raise DatasetError(f"difficulty must be one of {DIFFICULTIES}")
        cases = [case for case in cases if case.difficulty == difficulty]
    if not cases:
        raise DatasetError("selection matched no cases")
    return cases


def load_dataset(path: str) -> list[EvalCase]:
    """Load and fully validate the evaluation dataset from a YAML file."""
    try:
        with open(path, encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except FileNotFoundError as error:
        raise DatasetError(f"evaluation dataset not found: {path}") from error
    except yaml.YAMLError as error:
        raise DatasetError(f"evaluation dataset is not valid YAML: {path}") from error

    _require(isinstance(raw, list) and raw, f"{path}: dataset must be a non-empty list of cases")
    cases = [_parse_entry(entry, index) for index, entry in enumerate(raw)]

    seen: set[str] = set()
    for case in cases:
        _require(
            case.case_id not in seen,
            f"duplicate dataset id: {case.case_id}",
        )
        seen.add(case.case_id)
        # Same read-only boundary as the agent: unsafe reference SQL must be
        # rejected at load time, before any execution is attempted.
        try:
            validate_and_bound_query(case.reference_sql, max_rows=1)
        except UnsafeQueryError as error:
            raise DatasetError(
                f"case {case.case_id}: reference SQL failed read-only validation: {error}"
            ) from error
    return cases
