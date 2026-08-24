---
name: code-review
description: Structured code review for the factory-mcp-demo project and pi/agent workflows. Load before reviewing any uncommitted or committed changes, PRs, or refactors so the review is consistent and checks the right thing. Also usable as a general-purpose code review skill with the included checklist.
license: MIT
---

# Code Review

Review changes with a reproducible, severity-based method instead of an ad-hoc opinion. The skill applies to whole diffs, single files, or a PR against `main`/`mcp-server`.

## When to use

- Any uncommitted or staged work (`git diff` / `git diff --cached`) before commit.
- A branch or PR before it is merged.
- A refactor or middleware/architecture change where the surface is larger than a few lines.

## Setup (first use)

Skills must not pretend. Confirm the project layout once:

```bash
cd /workspace/mcp-server-demo-main
git status
git diff                      # uncommitted work
git diff --cached             # staged work
```

If reviewing committed work instead, name the base explicitly:

```bash
git diff main...HEAD
```

## Review process

Follow this order. Do not skip ahead.

### 1. Scope the change

- What files changed, and are they all intentional?
- Are there stray files (`.pyc`, logs, `.env`, build artifacts) that should not ship?
- Is untracked work (missing from a commit) deliberately so?

### 2. Read the diff — not just the headline

Read the actual changed hunks plus the surrounding context (imports, callers, related files). Never review only the summary.

### 3. Apply the checklist

For each changed unit, evaluate against the relevant rows of [the checklist](references/CHECKLIST.md). The checklist covers:

- correctness and behaviour
- error handling (raise vs swallow, consistent error surfaces)
- concurrency/lifecycle (pools, contexts, timeouts)
- security (SQL safety, injection, secrets, auth)
- API/contract stability (tool schemas, response shapes)
- test coverage (are the new paths actually asserted?)
- performance and resource bounds
- docs and naming consistency

### 4. Classify findings

Every issue is one of:

| Severity | Meaning | Action |
|----------|---------|--------|
| **Blocker** | Must fix before merge (data loss, security, crash, silent wrong result) | Fix or block the merge |
| **Should** | Correctness or quality that will bite soon | Fix in this PR unless strongly justified |
| **Nit** | Style/preference with no functional impact | Optional, no hold-up |
| **Question** | Something the author must clarify (not necessarily a change) | Reply on the PR |

### 5. Write the review

Structure every review the same way so the author can scan it:

1. **Scope** — files and diff stat, stated base and target.
2. **Verdict** — Approve / Approve-with-nits / Request changes / Blocked, plus a one-line rationale. Default to **Request changes** if there is one Blocker.
3. **Findings** — grouped by severity. Each finding: file, the relevant lines, why it matters, and a concrete suggested fix.
4. **Tests** — were tests added/updated to cover this change? If not, say so explicitly.
5. **Good** — what is genuinely well done (helps the author learn and sets a positive tone).

Keep findings specific and actionable. Prefer quoting the exact code over paraphrasing. Do not pad with filler praise or invented "nice-to-haves" that are not grounded in the diff.

## Rule for the author of the change

If a review is requested on work the same session produced, apply **skeptical distance**: re-read the diff as if an unknown engineer wrote it, and do not lower the bar because it is your own code.

## Quoting style

- References to code use `path/to/file.py:LINE` where useful.
- Copy the exact symbol, query, or branch under discussion.

## Layout

- `SKILL.md` — this file.
- `references/CHECKLIST.md` — the full review checklist.
- `references/PROJECT.md` — project-specific context, architecture, and conventions loaded on demand.