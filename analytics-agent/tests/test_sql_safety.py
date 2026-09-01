import pytest

from app.sql_safety import UnsafeQueryError, validate_and_bound_query


def test_select_gets_default_limit() -> None:
    assert validate_and_bound_query("SELECT * FROM machines", 100).endswith("LIMIT 100")


def test_existing_limit_is_preserved() -> None:
    assert validate_and_bound_query("SELECT * FROM machines LIMIT 5", 100).endswith("LIMIT 5")


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
