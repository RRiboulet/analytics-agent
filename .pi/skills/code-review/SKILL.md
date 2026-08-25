---

name: code-review

description: Structured, severity-based code review for MCP/agent/Postgres and pi/agent workflows. Load before reviewing any uncommitted, staged, or committed changes, PRs, or refactors so the review is consistent and checks the right thing. Also usable as a general-purpose code review skill with the included checklist.

license: MIT

---

# Code Review

Review changes with a reproducible, severity-based method instead of an ad-hoc opinion. The skill applies to whole diffs, single files, or a PR against an explicitly identified base.

## When to use

* Any uncommitted or staged work (`git diff` / `git diff --cached`) before commit.
* A branch or PR before it is merged.
* A refactor or middleware/architecture change where the surface is larger than a few lines.
* Agent, MCP, database, or infrastructure changes with meaningful behavioral impact.

## Setup (first use)

Confirm the repository root and project layout:

```bash
pwd
git status
git diff
git diff --cached
```

Do not assume a hard-coded repository path.

If reviewing committed work, name the base explicitly:

```bash
git diff main...HEAD
```

If the relevant base branch differs, use the actual base rather than assuming `main`.

## Review process

Follow this order. Do not skip ahead.

### 1. Scope the change

* What files changed, and are they all intentional?
* Are there stray files (`.pyc`, logs, `.env`, build artifacts) that should not ship?
* Is untracked work deliberately untracked?
* Does the diff contain unrelated refactoring or cleanup?

### 2. Read the diff — not just the headline

Read the actual changed hunks plus the surrounding context:

* imports
* callers
* tests
* related files
* configuration
* interfaces
* error handling

Never review only the summary.

### 3. Apply the checklist

For each changed unit, evaluate against the relevant rows of `references/CHECKLIST.md`.

The checklist covers:

* correctness and behaviour
* error handling
* concurrency/lifecycle
* security
* API/contract stability
* test coverage
* performance and resource bounds
* docs and naming consistency

For agentic/LLM functionality, also check:

* agent state transitions
* bounded retries and termination
* tool-call correctness
* tool input validation
* generated SQL correctness
* SQL safety boundaries
* schema/table hallucination risk
* fabricated results or unsupported conclusions
* correct handling of tool failures
* correct propagation of tool results into agent state
* observability of important agent steps
* unnecessary agent autonomy or complexity

### 4. Classify findings

Every issue is one of:

| Severity     | Meaning                                                                 | Action                                   |
| ------------ | ----------------------------------------------------------------------- | ---------------------------------------- |
| **Blocker**  | Must fix before merge (data loss, security, crash, silent wrong result) | Fix or block the merge                   |
| **Should**   | Correctness or quality that will bite soon                              | Fix in this PR unless strongly justified |
| **Nit**      | Style/preference with no functional impact                              | Optional, no hold-up                     |
| **Question** | Something the author must clarify (not necessarily a change)            | Reply on the PR                          |

### 5. Write the review

Structure every review the same way:

1. **Scope** — files and diff stat, stated base and target.
2. **Verdict** — Approve / Approve-with-nits / Request changes / Blocked, plus a one-line rationale. Default to **Request changes** if there is one Blocker.
3. **Findings** — grouped by severity. Each finding: file, relevant lines, why it matters, and a concrete suggested fix.
4. **Tests** — tests added/updated and verification performed. If important coverage is missing, say so explicitly.
5. **Good** — what is genuinely well done.

Keep findings specific and actionable. Prefer quoting the exact code over paraphrasing.

Do not pad the review with invented concerns or generic praise.

## Rule for the author of the change

If a review is requested on work produced in the same session, apply **skeptical distance**.

Re-read the diff as if an unknown engineer wrote it.

Do not lower the review bar because you produced the code.

## Quoting style

References to code use:

```text
path/to/file.py:LINE
```

where useful.

Copy the exact symbol, query, branch, or configuration under discussion.

## Layout

* `SKILL.md` — this file.
* `references/CHECKLIST.md` — the full review checklist.
* `references/PROJECT.md` — project-specific context, architecture, and conventions loaded on demand.
