"""Bounded follow-up decision for the manager (M7.5).

The inspect stage is the manager's single conditional edge before synthesis
(D009, Stage B): the model reviews the accumulated sub-analysis evidence and
may request at most 2 follow-up questions (anomaly investigation or
drill-down) for exactly one follow-up round. Parsing happens here, outside
the model path, so the decision — and the hard caps — are deterministic and
testable.

Unlike decomposition, an empty result is a *valid* outcome here: "no
follow-up needed" is the correct response for a well-covered request, so the
parser returns ``[]`` instead of raising. Unusable output (more questions
than the cap allows) raises ``FollowUpError``; the workflow treats that as no
follow-up and records the error, because the sub-analysis evidence is already
complete and this optional stage must never block the report.
"""

from app.manager.decompose import _clean_line

# Hard caps from D009 (Stage B): at most 2 follow-up questions per round.
MAX_FOLLOW_UP_QUESTIONS = 2

# Explicit "no follow-up" markers the inspect prompt instructs the model to
# emit; matched case-insensitively after stripping trailing punctuation.
_NO_FOLLOW_UP = {"NONE", "N/A", "NO FOLLOW-UP", "NO FOLLOW UP"}


class FollowUpError(Exception):
    """The inspect output ignored the follow-up hard caps (unusable output)."""


def parse_follow_up_questions(raw: str) -> list[str]:
    """Parse raw inspect output into 0..2 deduplicated follow-up questions.

    Empty output or an explicit no-follow-up marker (``NONE``/``N/A``) is a
    valid outcome returning ``[]``. More than ``MAX_FOLLOW_UP_QUESTIONS``
    valid questions raise ``FollowUpError``: the model ignored the hard cap
    (D009), and guessing which questions to keep is not the manager's job.
    """
    questions: list[str] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        line = _clean_line(line)
        if line is None or line in seen:
            continue
        if line.upper().strip(" .:-") in _NO_FOLLOW_UP:
            continue
        seen.add(line)
        questions.append(line)
    if len(questions) > MAX_FOLLOW_UP_QUESTIONS:
        raise FollowUpError(
            f"The model requested {len(questions)} follow-up questions; "
            f"the hard cap is {MAX_FOLLOW_UP_QUESTIONS} (D009)."
        )
    return questions
