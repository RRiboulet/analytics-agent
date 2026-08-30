"""Run the M6 evaluation benchmark end-to-end.

Usage (from the project home, with Postgres and the LLM server running):

    uv run python -m scripts.run_evaluation [--case ID] [--from N --to M]
                                            [--category CAT] [--difficulty D]
                                            [--out DIR]

Loads the evaluation dataset, runs the analytics agent over the selected
cases (real LLM, real MCP read-only boundary, Langfuse tracing fail-open),
judges each case deterministically against the reference SQL, and writes a
JSON report plus a markdown summary. Per-case records are flushed to disk as
soon as they are judged, so an interrupted long run still leaves partial
results.

Case selection (filters compose):

    --case revenue-011      a single case by id
    --from 1 --to 10        a 1-based inclusive index range into the raw
                            dataset order (overall benchmark cases 1-10)
    --category revenue      one analytical theme (see the dataset YAML)
    --difficulty hard       easy | medium | hard
"""

import argparse
import asyncio
import json
from pathlib import Path

from app.agent.capabilities import MCPCapabilities
from app.agent.llm import LLMClient
from app.agent.tracing import AgentTracer
from app.config import get_settings
from app.evaluation.dataset import DIFFICULTIES, DatasetError, load_dataset, select_cases
from app.evaluation.report import aggregate, render_markdown
from app.evaluation.runner import CaseResult, EvaluationRunner

DEFAULT_DATASET = "data/evaluation/olist_v1.yaml"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the analytics agent over selected evaluation benchmark cases."
    )
    parser.add_argument("--dataset", default=DEFAULT_DATASET, help="path to the benchmark YAML")
    parser.add_argument("--case", metavar="ID", help="run a single case by id (e.g. revenue-011)")
    parser.add_argument(
        "--from",
        dest="first",
        type=int,
        metavar="N",
        help="first case index in the raw dataset order (1-based, inclusive)",
    )
    parser.add_argument(
        "--to",
        dest="last",
        type=int,
        metavar="N",
        help="last case index in the raw dataset order (1-based, inclusive)",
    )
    parser.add_argument(
        "--category", metavar="CAT", help="only cases of this category (e.g. revenue)"
    )
    parser.add_argument("--difficulty", choices=DIFFICULTIES, help="only cases of this difficulty")
    parser.add_argument("--out", default="evaluation_results", help="output directory")
    return parser.parse_args(argv)


def _record_writer(out_dir: Path, summary: dict, records: list[CaseResult]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out_dir / "results.json").write_text(
        json.dumps([record.to_record() for record in records], indent=2), encoding="utf-8"
    )
    (out_dir / "report.md").write_text(render_markdown(summary), encoding="utf-8")


async def _main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    try:
        cases = select_cases(
            load_dataset(args.dataset),
            case_id=args.case,
            first=args.first,
            last=args.last,
            category=args.category,
            difficulty=args.difficulty,
        )
    except DatasetError as error:
        raise SystemExit(f"case selection failed: {error}") from error

    settings = get_settings()
    llm = LLMClient()
    capabilities = MCPCapabilities()
    tracer = AgentTracer()
    runner = EvaluationRunner(
        llm=llm,
        capabilities=capabilities,
        max_attempts=settings.agent_max_attempts,
        max_rows=settings.max_rows,
    )
    out_dir = Path(args.out)
    records: list[CaseResult] = []

    def _on_result(result: CaseResult) -> None:
        records.append(result)
        # Flush partial state so an interrupted run still leaves usable data.
        _record_writer(out_dir, aggregate(records), records)

    try:
        results = await runner.run_all(cases, on_result=_on_result)
    finally:
        await capabilities.close()
        tracer.flush()

    summary = aggregate(results)
    _record_writer(out_dir, summary, results)
    print(render_markdown(summary))


if __name__ == "__main__":
    asyncio.run(_main())
