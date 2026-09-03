"""Deterministic decomposition of a management request (M7.1).

The decompose model call returns raw text; parsing and validation happen here,
outside the model path, so the manager's plan is deterministic and testable:
non-empty, order-preserving deduplication, and the hard cap of 4 sub-questions
locked by D009. Unusable model output raises a typed ``DecompositionError``
instead of being guessed into a plan; model-call failures (``LLMError``)
propagate unchanged so the workflow can treat them as a separate failure kind.
"""

import re

from app.manager.llm import ManagerLLM

# Hard cap from D009: at most 4 sub-analyses per management request.
MAX_SUB_QUESTIONS = 4

# Leading markdown bullets ("-", "*", "+") or list numbering ("1.", "1)").
_BULLET_PREFIX = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
# A lone list marker with nothing after it (the bullet regex requires
# trailing whitespace, so "1." on its own survives the prefix strip).
_ORPHAN_NUMBERING = re.compile(r"^\d+[.)]?$")


class DecompositionError(Exception):
    """The decompose output could not be validated into a usable plan."""


def _clean_line(line: str) -> str | None:
    """Strip markup from one output line; ``None`` if it carries no question."""
    line = line.strip()
    if not line or line.startswith("```"):
        return None
    line = _BULLET_PREFIX.sub("", line).strip()
    if (
        not line
        # Drop punctuation-only leftovers (e.g. a stray "-").
        or not any(ch.isalnum() for ch in line)
        or _ORPHAN_NUMBERING.fullmatch(line)
    ):
        return None
    return line


def parse_sub_questions(raw: str) -> list[str]:
    """Parse and validate raw model output into 1..4 deduplicated sub-questions.

    Tolerant parsing: code-fence lines, blank lines and bullet/numbering
    prefixes are stripped; exact duplicates are dropped preserving order.
    Strict validation: an empty result or more than ``MAX_SUB_QUESTIONS``
    questions raises ``DecompositionError``.
    """
    questions: list[str] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        line = _clean_line(line)
        if line is None or line in seen:
            continue
        seen.add(line)
        questions.append(line)
    if not questions:
        raise DecompositionError("The model returned no sub-questions.")
    if len(questions) > MAX_SUB_QUESTIONS:
        raise DecompositionError(
            f"The model returned {len(questions)} sub-questions; "
            f"the hard cap is {MAX_SUB_QUESTIONS} (D009)."
        )
    return questions


async def decompose_request(llm: ManagerLLM, request: str, table_names: list[str]) -> list[str]:
    """Run the decompose model call and validate its output.

    Raises ``LLMError`` (model/transport failure, propagates unchanged) or
    ``DecompositionError`` (the model returned unusable output).
    """
    raw = await llm.decompose(request, table_names)
    return parse_sub_questions(raw)
