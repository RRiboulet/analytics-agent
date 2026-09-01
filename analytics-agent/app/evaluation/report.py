"""Aggregate evaluation records into metrics and a markdown report.

Pass rates are reported overall and per difficulty/category; structural
metrics (attempts, latency, tool calls, failure rate, table relevance)
summarize agent behavior across the benchmark.
"""

from collections import Counter
from typing import Any

from app.evaluation.runner import CaseResult


def _rate(passed: int, total: int) -> float:
    return round(passed / total, 4) if total else 0.0


def _by_group(results: list[CaseResult], key: str) -> dict[str, Any]:
    grouped: dict[str, list[CaseResult]] = {}
    for result in results:
        group = getattr(result, key)
        grouped.setdefault(group, []).append(result)
    return {
        group: {"passed": sum(1 for r in rows if r.passed), "total": len(rows)}
        for group, rows in sorted(grouped.items())
    }


def aggregate(results: list[CaseResult]) -> dict[str, Any]:
    """Compute benchmark-level metrics from per-case records."""
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failures = [r.case_id for r in results if not r.passed]
    attempts = Counter(r.attempts for r in results)
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": _rate(passed, total),
        "failure_rate": _rate(total - passed, total),
        "by_difficulty": _by_group(results, "difficulty"),
        "by_category": _by_group(results, "category"),
        "attempts_distribution": {
            str(attempt): count for attempt, count in sorted(attempts.items())
        },
        "avg_attempts": round(sum(r.attempts for r in results) / total, 3) if total else 0.0,
        "avg_latency_seconds": round(sum(r.latency_seconds for r in results) / total, 3)
        if total
        else 0.0,
        "total_tool_calls": sum(sum(r.tool_calls.values()) for r in results),
        "cases_with_retries": sum(1 for r in results if r.attempts > 1),
        "cases_missing_tables": sum(1 for r in results if r.missing_tables),
        "failed_case_ids": failures,
    }


def render_markdown(summary: dict[str, Any]) -> str:
    """Render the aggregate summary as a small markdown report."""
    lines = [
        "# Evaluation report",
        "",
        f"- Cases: {summary['total']}",
        f"- Passed: {summary['passed']} ({summary['pass_rate']:.1%})",
        f"- Failed: {summary['failed']} ({summary['failure_rate']:.1%})",
        f"- Avg attempts: {summary['avg_attempts']} (retried: {summary['cases_with_retries']})",
        f"- Avg latency: {summary['avg_latency_seconds']}s",
        f"- Total tool calls: {summary['total_tool_calls']}",
        f"- Cases missing expected tables: {summary['cases_missing_tables']}",
        "",
        "| Difficulty | Passed | Total | Pass rate |",
        "|---|---|---|---|",
    ]
    for difficulty, counts in summary["by_difficulty"].items():
        rate = _rate(counts["passed"], counts["total"])
        lines.append(f"| {difficulty} | {counts['passed']} | {counts['total']} | {rate:.1%} |")
    lines.extend(["", "| Category | Passed | Total | Pass rate |", "|---|---|---|---|"])
    for category, counts in summary["by_category"].items():
        rate = _rate(counts["passed"], counts["total"])
        lines.append(f"| {category} | {counts['passed']} | {counts['total']} | {rate:.1%} |")
    if summary["failed_case_ids"]:
        lines.extend(["", "## Failed cases", ""])
        lines.extend(f"- {case_id}" for case_id in summary["failed_case_ids"])
    return "\n".join(lines)
