---
name: yagni-coding
description: Behavioral coding discipline that reduces common LLM mistakes before they happen. Load before implementing or editing code, especially when the task is vague, multi-step, or touches an existing codebase. Bias toward YAGNI (you aren't gonna need it): minimal code, no speculative abstractions, surgical diffs, and verified goals over optimism.
license: MIT
---

# YAGNI Coding Discipline

Apply these guidelines before and during implementation. They bias toward caution over speed; for genuinely trivial tasks use your judgment and do not manufacture ceremony.

## When to use

- Any implementation or edit, regardless of size.
- Any task tagged as a refactor, "improve", "clean up", or "make robust".
- Any task where the requirement has more than one plausible reading.

## Core principles

### 1. Think before coding

- State assumptions explicitly before writing code. If uncertain, ask.
- If multiple interpretations exist, **present them** — do not pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, **stop**. Name what is confusing. Ask.

### 2. Simplicity first

Write the minimum code that solves the problem. Nothing speculative.

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that was not requested.
- No error handling for impossible scenarios.
- If a solution can be 50 lines instead of 200, rewrite it to 50.
- Ask: *"Would a senior engineer call this overcomplicated?"* If yes, simplify.

### 3. Surgical changes

Touch only what you must. Clean up only your own mess.

- Do not improve adjacent code, comments, or formatting while you are in the file.
- Do not refactor things that are not broken.
- Match the existing style, even if you'd do it differently.
- If you notice unrelated dead code, **mention it** — do not delete it.
- When your changes create orphans, remove imports/variables/functions that **your** changes made unused; do not remove pre-existing dead code unless asked.
- The test: every changed line should trace directly to the user's request.

### 4. Goal-driven execution

Turn tasks into verifiable goals, then loop until verified.

| Vague ask | Verifiable form |
|-----------|-----------------|
| "Add validation" | "Write tests for invalid inputs, then make them pass" |
| "Fix the bug" | "Write a test that reproduces it, then make it pass" |
| "Refactor X" | "Ensure tests pass before and after" |

For multi-step tasks, write a short plan with a verify check per step:

```text
1. [Step]       → verify: [concrete check]
2. [Step]       → verify: [concrete check]
3. [Step]       → verify: [concrete check]
```

Weak criteria ("make it work") require constant clarification — tighten them before starting.

## Working definition

These guidelines are working when:

- diffs contain fewer unnecessary changes
- rewrites from overcomplication happen less often
- clarifying questions come *before* implementation, not after mistakes

## Merge with project specifics

Before coding in a repo, read its context file if present. For this project it is
[references/PROJECT.md](references/PROJECT.md) (architecture + invariants) and
the [code-review skill](../code-review/SKILL.md) for the definition of "done".
Follow the repo's established conventions rather than importing your own.

## Layout

- `SKILL.md` — this file.
- `references/PROJECT.md` — project-specific integration points and size/`scope norms.