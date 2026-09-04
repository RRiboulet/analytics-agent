import pytest

from app.sql_safety import UnsafeQueryError, validate_and_bound_query


def test_select_gets_default_limit() -> None:
    assert validate_and_bound_query("SELECT * FROM machines", 100).endswith("LIMIT 100")


def test_existing_limit_is_preserved() -> None:
    assert validate_and_bound_query("SELECT * FROM machines LIMIT 5", 100).endswith("LIMIT 5")


def test_with_cte_passes_and_gets_default_limit() -> None:
    sql = "WITH t AS (SELECT 1 AS x) SELECT x FROM t"
    bounded = validate_and_bound_query(sql, 100)
    assert bounded.endswith("LIMIT 100")
    assert "WITH t AS (SELECT 1 AS x) SELECT x FROM t" in bounded


def test_with_cte_existing_limit_is_preserved() -> None:
    sql = "WITH t AS (SELECT 1 AS x) SELECT x FROM t LIMIT 5"
    assert validate_and_bound_query(sql, 100).endswith("LIMIT 5")


def test_lowercase_with_cte_passes() -> None:
    assert validate_and_bound_query("with t AS (SELECT 1 AS x) SELECT x FROM t", 100).endswith(
        "LIMIT 100"
    )


def test_case_expression_within_select_passes() -> None:
    sql = "SELECT CASE WHEN x > 1 THEN 1 ELSE 0 END AS flag FROM machines"
    assert validate_and_bound_query(sql, 100).endswith("LIMIT 100")


def test_with_cte_gets_bounded_limit_applied() -> None:
    bounded = validate_and_bound_query("WITH t AS (SELECT 1 AS x) SELECT x FROM t LIMIT 5", 100)
    assert bounded.count("LIMIT") == 1
    assert bounded.endswith("LIMIT 5")


def test_quoted_identifier_named_with_is_rejected() -> None:
    with pytest.raises(UnsafeQueryError):
        validate_and_bound_query('"WITH" AS x SELECT 1', 100)


def test_multi_statement_with_is_rejected() -> None:
    with pytest.raises(UnsafeQueryError):
        validate_and_bound_query(
            "WITH t AS (SELECT 1 AS x) SELECT x FROM t; DROP TABLE machines", 100
        )


@pytest.mark.parametrize(
    "query",
    [
        "",
        "UPDATE machines SET status = 'idle'",
        "SELECT 1; SELECT 2",
        "DROP TABLE machines",
    ],
)
def test_unsafe_queries_are_rejected(query: str) -> None:
    with pytest.raises(UnsafeQueryError):
        validate_and_bound_query(query, 100)
