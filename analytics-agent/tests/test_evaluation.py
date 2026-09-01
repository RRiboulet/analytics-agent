"""Deterministic unit tests for the M6 evaluation layer.

Dataset validation, result comparison, the runner (with a deterministic
FakeLLM and in-memory capabilities stub), and report aggregation are all
covered without any network, live model, or database.
"""

import json

import pytest

from app.agent.llm import FakeLLM
from app.evaluation.dataset import DatasetError, EvalCase, load_dataset, select_cases
from app.evaluation.judges import compare_result_sets, referenced_tables
from app.evaluation.report import aggregate, render_markdown
from app.evaluation.runner import CaseResult, CountingCapabilities, EvaluationRunner

DATASET_PATH = "data/evaluation/olist_v1.yaml"


def _case(**overrides) -> EvalCase:
    values = {
        "case_id": "c1",
        "question": "Q?",
        "difficulty": "easy",
        "category": "revenue",
        "reference_sql": "SELECT 1 AS reference",
        "expected_tables": ["t"],
        "ordered": False,
    }
    values.update(overrides)
    return EvalCase(**values)


def _result(**overrides) -> CaseResult:
    values = {
        "case_id": "c1",
        "question": "Q?",
        "difficulty": "easy",
        "category": "revenue",
        "status": "completed",
        "passed": True,
        "attempts": 1,
        "latency_seconds": 1.0,
        "tool_calls": {"query": 1},
    }
    values.update(overrides)
    return CaseResult(**values)


class StubCapabilities:
    """In-memory MCP capability stub for runner tests.

    Distinguishes the runner's reference query from the agent's own query by
    the ``AS reference`` alias marker in the reference SQL (see ``_case``).
    """

    def __init__(self, agent_entries, reference_entries, query_valid=True):
        self.agent_entries = agent_entries
        self.reference_entries = reference_entries
        self.query_valid = query_valid
        self.closed = False
        self.queries: list[str] = []

    async def call_tool(self, name, args=None):
        if name == "search_metadata":
            return {"valid": True, "entries": [{"entity_id": "table:orders"}]}
        if name == "list_tables":
            return {"valid": True, "entries": [{"table_name": "orders"}]}
        if name == "get_relationships":
            return {"valid": True, "entries": []}
        if name == "query":
            sql = args["sql"]
            self.queries.append(sql)
            if not self.query_valid:
                return {"valid": False, "message": "query failed", "entries": []}
            if "reference" in sql:
                return {"valid": True, "entries": self.reference_entries}
            return {"valid": True, "entries": self.agent_entries}
        raise AssertionError(f"unexpected tool: {name}")

    async def close(self):
        self.closed = True


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------


def test_load_dataset_validates_the_benchmark_file():
    cases = load_dataset(DATASET_PATH)
    assert len(cases) == 30
    assert {case.difficulty for case in cases} == {"easy", "medium", "hard"}
    assert len({case.case_id for case in cases}) == 30
    # Reference SQL must be read-only (already enforced at load time).
    assert all(case.reference_sql.strip().lower().startswith(("select", "with")) for case in cases)


def test_load_dataset_rejects_missing_file():
    with pytest.raises(DatasetError, match="not found"):
        load_dataset("does/not/exist.yaml")


def test_load_dataset_rejects_invalid_yaml(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("foo: [unclosed", encoding="utf-8")
    with pytest.raises(DatasetError, match="not valid YAML"):
        load_dataset(str(path))


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        ("just a string", "non-empty list"),
        ([], "non-empty list"),
        ([{"no": "id"}], "'id' must be a non-empty string"),
        (
            [
                {
                    "id": "a",
                    "question": "q",
                    "difficulty": "easy",
                    "category": "c",
                    "reference_sql": "SELECT 1",
                    "expected_tables": ["t"],
                },
                {
                    "id": "a",
                    "question": "q",
                    "difficulty": "easy",
                    "category": "c",
                    "reference_sql": "SELECT 1",
                    "expected_tables": ["t"],
                },
            ],
            "duplicate dataset id",
        ),
        (
            [
                {
                    "id": "a",
                    "question": "",
                    "difficulty": "easy",
                    "category": "c",
                    "reference_sql": "SELECT 1",
                    "expected_tables": ["t"],
                }
            ],
            "'question' must be a non-empty",
        ),
        (
            [
                {
                    "id": "a",
                    "question": "q",
                    "difficulty": "expert",
                    "category": "c",
                    "reference_sql": "SELECT 1",
                    "expected_tables": ["t"],
                }
            ],
            "'difficulty' must be one of",
        ),
        (
            [
                {
                    "id": "a",
                    "question": "q",
                    "difficulty": "easy",
                    "category": "",
                    "reference_sql": "SELECT 1",
                    "expected_tables": ["t"],
                }
            ],
            "'category' must be a non-empty",
        ),
        (
            [
                {
                    "id": "a",
                    "question": "q",
                    "difficulty": "easy",
                    "category": "c",
                    "reference_sql": "",
                    "expected_tables": ["t"],
                }
            ],
            "'reference_sql' must be a non-empty",
        ),
        (
            [
                {
                    "id": "a",
                    "question": "q",
                    "difficulty": "easy",
                    "category": "c",
                    "reference_sql": "SELECT 1",
                    "expected_tables": [],
                }
            ],
            "'expected_tables' must be a non-empty list",
        ),
        (
            [
                {
                    "id": "a",
                    "question": "q",
                    "difficulty": "easy",
                    "category": "c",
                    "reference_sql": "SELECT 1",
                    "expected_tables": ["t", ""],
                }
            ],
            "every entry of 'expected_tables'",
        ),
        (
            [
                {
                    "id": "a",
                    "question": "q",
                    "difficulty": "easy",
                    "category": "c",
                    "reference_sql": "SELECT 1",
                    "expected_tables": ["t"],
                    "ordered": "yes",
                }
            ],
            "'ordered' must be a boolean",
        ),
        (
            [
                {
                    "id": "a",
                    "question": "q",
                    "difficulty": "easy",
                    "category": "c",
                    "reference_sql": "DROP TABLE orders",
                    "expected_tables": ["t"],
                }
            ],
            "read-only validation",
        ),
    ],
)
def test_load_dataset_rejects_malformed_entries(tmp_path, raw, match):
    path = tmp_path / "cases.yaml"
    path.write_text(json.dumps(raw), encoding="utf-8")  # JSON is valid YAML
    with pytest.raises(DatasetError, match=match):
        load_dataset(str(path))


# ---------------------------------------------------------------------------
# Result comparison (judge)
# ---------------------------------------------------------------------------


def test_compare_result_sets_matches_exact_rows():
    reference = [{"revenue": 100.0}, {"revenue": 50.0}]
    agent = [{"total": 50.0}, {"total": 100.0}]  # aliases ignored, order swapped
    assert compare_result_sets(agent, reference).matches is True


def test_compare_result_sets_matches_within_numeric_tolerance():
    reference = [{"revenue": 1_000_000.0}]
    agent = [{"revenue": 1_000_000.0 + 0.5}]
    assert compare_result_sets(agent, reference).matches is True


def test_compare_result_sets_rejects_row_count_difference():
    assert compare_result_sets([{"a": 1.0}], [{"a": 1.0}, {"a": 2.0}]).matches is False


def test_compare_result_sets_rejects_value_mismatch():
    result = compare_result_sets([{"a": 1.0}], [{"a": 2.0}])
    assert result.matches is False
    assert "value mismatch" in result.detail


def test_compare_result_sets_rejects_column_count_difference():
    result = compare_result_sets([{"a": 1.0, "b": 2.0}], [{"a": 1.0}])
    assert result.matches is False
    assert "column count" in result.detail


def test_compare_result_sets_respects_ordered_flag():
    reference = [{"rank": 1.0}, {"rank": 2.0}]
    agent = [{"rank": 2.0}, {"rank": 1.0}]
    assert compare_result_sets(agent, reference, ordered=True).matches is False
    assert compare_result_sets(agent, reference, ordered=False).matches is True


def test_compare_result_sets_empty_sets_match():
    assert compare_result_sets([], []).matches is True


def test_compare_result_sets_distinguishes_types():
    assert compare_result_sets([{"a": None}], [{"a": 0.0}]).matches is False
    assert compare_result_sets([{"a": True}], [{"a": 1.0}]).matches is False
    assert compare_result_sets([{"a": "1.0"}], [{"a": 1.0}]).matches is False
    assert compare_result_sets([{"a": None}], [{"a": None}]).matches is True
    assert compare_result_sets([{"a": "x"}], [{"a": "x"}]).matches is True


def test_referenced_tables_uses_word_boundaries():
    sql = "SELECT * FROM orders o JOIN order_items oi ON oi.order_id = o.order_id"
    assert referenced_tables(sql, ["orders", "order_items", "customers"]) == [
        "orders",
        "order_items",
    ]


# ---------------------------------------------------------------------------
# Case selection
# ---------------------------------------------------------------------------


def _cases() -> list[EvalCase]:
    return [
        _case(
            case_id=f"c{i}",
            difficulty=("easy", "medium", "hard")[i % 3],
            category=("revenue", "time")[i % 2],
        )
        for i in range(1, 7)
    ]


def test_select_single_case_by_id():
    selected = select_cases(_cases(), case_id="c3")
    assert [case.case_id for case in selected] == ["c3"]


def test_select_unknown_case_id_raises():
    with pytest.raises(DatasetError, match="unknown case id: nope"):
        select_cases(_cases(), case_id="nope")


def test_select_by_category():
    selected = select_cases(_cases(), category="revenue")
    assert all(case.category == "revenue" for case in selected)


def test_select_by_difficulty():
    selected = select_cases(_cases(), difficulty="hard")
    assert all(case.difficulty == "hard" for case in selected)


def test_select_invalid_difficulty_raises():
    with pytest.raises(DatasetError, match="difficulty must be one of"):
        select_cases(_cases(), difficulty="expert")


def test_select_range_addresses_raw_dataset_order():
    # Overall positions 1..3 are c1, c2, c3 — the range is raw-dataset based,
    # not "the first 3 hard cases": difficulty only narrows the slice.
    selected = select_cases(_cases(), difficulty="hard", first=1, last=3)
    assert [case.case_id for case in selected] == ["c2"]


def test_select_range_is_inclusive_on_both_bounds():
    selected = select_cases(_cases(), first=2, last=4)
    assert [case.case_id for case in selected] == ["c2", "c3", "c4"]


def test_select_range_with_only_first_bound():
    assert [case.case_id for case in select_cases(_cases(), first=5)] == ["c5", "c6"]
    assert [case.case_id for case in select_cases(_cases(), last=2)] == ["c1", "c2"]


def test_select_inverted_or_out_of_bounds_range_raises():
    with pytest.raises(DatasetError, match="inverted or outside"):
        select_cases(_cases(), first=4, last=2)
    with pytest.raises(DatasetError, match="inverted or outside"):
        select_cases(_cases(), first=0, last=3)
    with pytest.raises(DatasetError, match="inverted or outside"):
        select_cases(_cases(), first=2, last=99)


def test_select_filters_compose_on_top_of_the_raw_slice():
    revenue = select_cases(_cases(), category="revenue")  # c2, c4, c6
    assert len(revenue) == 3
    selected = select_cases(_cases(), category="revenue", first=2, last=3)
    # Overall positions 2..3 are c2, c3; the category filter keeps c2 only.
    assert [case.case_id for case in selected] == ["c2"]
    assert all(case.category == "revenue" for case in selected)


def test_select_by_first_ten_overall_benchmark_cases():
    # "The overall tests 1-10" against the real 30-case benchmark file.
    cases = load_dataset(DATASET_PATH)
    selected = select_cases(cases, first=1, last=10)
    assert [case.case_id for case in selected] == [
        "revenue-001",
        "orders-002",
        "customers-003",
        "sellers-004",
        "products-005",
        "reviews-006",
        "revenue-007",
        "orders-008",
        "payments-009",
        "reviews-010",
    ]
    assert all(case.difficulty == "easy" for case in selected)


def test_select_empty_result_raises():
    with pytest.raises(DatasetError, match="selection matched no cases"):
        select_cases(_cases(), category="payments")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_case_passes_when_results_match():
    caps = StubCapabilities(agent_entries=[{"total": 5.0}], reference_entries=[{"total": 5.0}])
    runner = EvaluationRunner(
        llm=FakeLLM(sql="SELECT SUM(x) FROM t", answer="5"),
        capabilities=caps,
        max_attempts=3,
        max_rows=100,
    )
    result = await runner.run_case(_case(expected_tables=["t"]))
    assert result.passed is True
    assert result.status == "completed"
    assert result.attempts == 1
    assert result.tool_calls == {
        "search_metadata": 1,
        "list_tables": 1,
        "get_relationships": 1,
        "query": 1,
    }
    assert result.missing_tables == []
    assert "reference" in caps.queries[1]


@pytest.mark.asyncio
async def test_run_case_fails_on_result_mismatch():
    caps = StubCapabilities(agent_entries=[{"total": 4.0}], reference_entries=[{"total": 5.0}])
    runner = EvaluationRunner(
        llm=FakeLLM(sql="SELECT SUM(x) FROM t", answer="4"),
        capabilities=caps,
        max_attempts=3,
        max_rows=100,
    )
    result = await runner.run_case(_case())
    assert result.passed is False
    assert "value mismatch" in result.comparison_detail


@pytest.mark.asyncio
async def test_run_case_fails_when_agent_run_fails():
    # Make the agent's own query fail so the run ends in `failed`.
    failing = StubCapabilities(agent_entries=[], reference_entries=[], query_valid=False)
    runner = EvaluationRunner(
        llm=FakeLLM(sql="SELECT broken", answer="x"),
        capabilities=failing,
        max_attempts=1,
        max_rows=100,
    )
    result = await runner.run_case(_case())
    assert result.passed is False
    assert result.status == "failed"
    assert result.failure_reason != ""


@pytest.mark.asyncio
async def test_run_case_fails_when_reference_sql_cannot_execute():
    class ReferenceBroken(StubCapabilities):
        async def call_tool(self, name, args=None):
            if name == "query" and "reference" in (args or {}).get("sql", ""):
                return {"valid": False, "message": "boom", "entries": []}
            return await super().call_tool(name, args)

    caps = ReferenceBroken(agent_entries=[{"total": 5.0}], reference_entries=[])
    runner = EvaluationRunner(
        llm=FakeLLM(sql="SELECT 1", answer="x"), capabilities=caps, max_attempts=3, max_rows=100
    )
    result = await runner.run_case(_case())
    assert result.passed is False
    assert "reference SQL failed to execute" in result.failure_reason


@pytest.mark.asyncio
async def test_run_case_flags_missing_expected_tables():
    caps = StubCapabilities(agent_entries=[{"total": 5.0}], reference_entries=[{"total": 5.0}])
    runner = EvaluationRunner(
        llm=FakeLLM(sql="SELECT 1 FROM elsewhere", answer="x"),
        capabilities=caps,
        max_attempts=3,
        max_rows=100,
    )
    result = await runner.run_case(_case(expected_tables=["orders", "customers"]))
    assert result.missing_tables == ["orders", "customers"]


@pytest.mark.asyncio
async def test_run_case_uses_ordered_comparison_when_configured():
    reference = [{"rank": 1.0}, {"rank": 2.0}]
    caps = StubCapabilities(
        agent_entries=[{"rank": 2.0}, {"rank": 1.0}], reference_entries=reference
    )
    runner = EvaluationRunner(
        llm=FakeLLM(sql="SELECT x ORDER BY y", answer="x"),
        capabilities=caps,
        max_attempts=3,
        max_rows=100,
    )
    ordered = await runner.run_case(_case(ordered=True))
    unordered = await runner.run_case(_case(ordered=False))
    assert ordered.passed is False
    assert unordered.passed is True


@pytest.mark.asyncio
async def test_run_all_reports_results_incrementally():
    caps = StubCapabilities(agent_entries=[{"total": 5.0}], reference_entries=[{"total": 5.0}])
    runner = EvaluationRunner(
        llm=FakeLLM(sql="SELECT 1", answer="x"), capabilities=caps, max_attempts=3, max_rows=100
    )
    seen: list[str] = []
    results = await runner.run_all(
        [_case(case_id="a"), _case(case_id="b")], on_result=lambda r: seen.append(r.case_id)
    )
    assert [r.case_id for r in results] == ["a", "b"]
    assert seen == ["a", "b"]


@pytest.mark.asyncio
async def test_run_all_without_callback():
    caps = StubCapabilities(agent_entries=[{"total": 5.0}], reference_entries=[{"total": 5.0}])
    runner = EvaluationRunner(
        llm=FakeLLM(sql="SELECT 1", answer="x"), capabilities=caps, max_attempts=3, max_rows=100
    )
    assert len(await runner.run_all([_case()])) == 1


@pytest.mark.asyncio
async def test_counting_capabilities_forwards_unknown_attributes():
    caps = StubCapabilities(agent_entries=[{"total": 5.0}], reference_entries=[{"total": 5.0}])
    counting = CountingCapabilities(caps)
    assert counting.closed is False
    await counting.close()
    assert caps.closed is True


@pytest.mark.asyncio
async def test_case_result_serializes_every_field():
    result = _result()
    record = result.to_record()
    assert record["case_id"] == "c1"
    assert record["passed"] is True
    assert record["tool_calls"] == {"query": 1}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def test_aggregate_computes_benchmark_metrics():
    summary = aggregate(
        [
            _result(case_id="a", passed=True, attempts=1, latency_seconds=1.0),
            _result(
                case_id="b",
                passed=False,
                attempts=2,
                latency_seconds=3.0,
                difficulty="medium",
                tool_calls={"query": 2},
            ),
            _result(
                case_id="c", passed=True, attempts=3, latency_seconds=2.0, missing_tables=["orders"]
            ),
        ]
    )
    assert summary["total"] == 3
    assert summary["passed"] == 2
    assert summary["pass_rate"] == 0.6667
    assert summary["failure_rate"] == 0.3333
    assert summary["by_difficulty"] == {
        "easy": {"passed": 2, "total": 2},
        "medium": {"passed": 0, "total": 1},
    }
    assert summary["by_category"] == {"revenue": {"passed": 2, "total": 3}}
    assert summary["attempts_distribution"] == {"1": 1, "2": 1, "3": 1}
    assert summary["avg_attempts"] == 2.0
    assert summary["avg_latency_seconds"] == 2.0
    assert summary["total_tool_calls"] == 4
    assert summary["cases_with_retries"] == 2
    assert summary["cases_missing_tables"] == 1
    assert summary["failed_case_ids"] == ["b"]


def test_aggregate_handles_empty_results():
    summary = aggregate([])
    assert summary["total"] == 0
    assert summary["pass_rate"] == 0.0
    assert summary["avg_latency_seconds"] == 0.0


def test_render_markdown_includes_tables_and_failures():
    summary = aggregate([_result(), _result(case_id="b", passed=False)])
    markdown = render_markdown(summary)
    assert "# Evaluation report" in markdown
    assert "| Difficulty |" in markdown
    assert "| Category |" in markdown
    assert "- b" in markdown


def test_render_markdown_omits_failure_section_when_all_passed():
    markdown = render_markdown(aggregate([_result()]))
    assert "## Failed cases" not in markdown
