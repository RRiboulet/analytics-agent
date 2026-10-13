"""Run the M7.6 management-level evaluation benchmark end-to-end."""

import argparse
import asyncio
import json
from pathlib import Path

from app.evaluation.dataset import DatasetError
from app.evaluation.manager_dataset import (
    load_manager_dataset,
    select_manager_cases,
)
from app.evaluation.manager_runner import ManagerEvaluationRunner
from app.evaluation.report import aggregate, render_markdown

DEFAULT_DATASET = "data/evaluation/manager_v1.yaml"


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run the manager evaluation benchmark.")
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--case", metavar="ID")
    parser.add_argument("--from", dest="first", type=int, metavar="N")
    parser.add_argument("--to", dest="last", type=int, metavar="N")
    parser.add_argument("--category", metavar="CAT")
    parser.add_argument("--difficulty", choices=("easy", "medium", "hard"))
    parser.add_argument("--out", default="manager_evaluation_results")
    return parser.parse_args(argv)


def _record(result):
    return {
        "case_id": result.case_id,
        "question": result.question,
        "difficulty": result.difficulty,
        "category": result.category,
        "status": result.status,
        "passed": result.passed,
        "attempts": result.attempts,
        "latency_seconds": result.latency_seconds,
        "failure_reason": result.failure_reason,
        "comparison_detail": result.comparison_detail,
        "sub_questions": result.sub_questions,
        "follow_up_questions": result.follow_up_questions,
        "answer": result.answer,
    }


def _record_writer(out_dir, summary, records):
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out_dir / "results.json").write_text(
        json.dumps([_record(r) for r in records], indent=2), encoding="utf-8"
    )
    (out_dir / "report.md").write_text(render_markdown(summary), encoding="utf-8")


async def _main(argv=None):
    args = _parse_args(argv)
    try:
        cases = select_manager_cases(
            load_manager_dataset(args.dataset),
            case_id=args.case,
            first=args.first,
            last=args.last,
            category=args.category,
            difficulty=args.difficulty,
        )
    except DatasetError as error:
        raise SystemExit(f"case selection failed: {error}") from error
    out_dir = Path(args.out)
    runner = ManagerEvaluationRunner(out_dir=str(out_dir / "artifacts"))
    records = []
    def on_result(result):
        records.append(result)
        _record_writer(out_dir, aggregate(records), records)
    results = await runner.run_all(cases, on_result=on_result)
    summary = aggregate(results)
    _record_writer(out_dir, summary, results)
    print(render_markdown(summary))


if __name__ == "__main__":
    asyncio.run(_main())
