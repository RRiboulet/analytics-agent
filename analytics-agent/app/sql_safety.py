import re

import sqlparse
from sqlparse.sql import Statement
from sqlparse.tokens import DML, Comment, Keyword, Whitespace


class UnsafeQueryError(ValueError):
    """Raised when a query is not a single bounded read operation."""


def validate_and_bound_query(sql: str, max_rows: int) -> str:
    query = sql.strip()
    if not query:
        raise UnsafeQueryError("The SQL query must not be blank.")
    if len(query) > 10_000:
        raise UnsafeQueryError("The SQL query is too long.")

    statements = [statement for statement in sqlparse.parse(query) if str(statement).strip()]
    if len(statements) != 1:
        raise UnsafeQueryError("Only one SQL statement is allowed.")

    statement: Statement = statements[0]
    meaningful = [token for token in statement.tokens if token.ttype not in (Whitespace, Comment)]
    if not meaningful:
        raise UnsafeQueryError("The SQL query must not be blank.")

    first = meaningful[0]
    # "WITH" is tokenized by sqlparse as Keyword.CTE, a subtype of Keyword
    # (SELECT is Keyword.DML). Check the keyword family (DML, Keyword,
    # Keyword.CTE) explicitly, then verify the actual word so a quoted
    # identifier or column literally named "with"/"select" is not accepted.
    if first.ttype not in (DML, Keyword, Keyword.CTE) or first.value.upper() not in {
        "SELECT",
        "WITH",
    }:
        raise UnsafeQueryError("Only SELECT queries are allowed.")

    if re.search(
        r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|CALL|COPY)\b",
        query,
        re.IGNORECASE,
    ):
        raise UnsafeQueryError("The query contains a disallowed SQL operation.")

    bounded = query.rstrip().rstrip(";").rstrip()
    if re.search(r"\bLIMIT\s+\d+\b", bounded, re.IGNORECASE):
        return bounded
    return f"{bounded}\nLIMIT {max_rows}"
