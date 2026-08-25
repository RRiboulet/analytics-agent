---

name: yagni-coding

description: Behavioral coding discipline that reduces common LLM mistakes before they happen. Load before implementing or editing code, especially when the task is vague, multi-step, or touches an existing codebase. Bias toward YAGNI (you aren't gonna need it); minimal code, no speculative abstractions, surgical diffs, and verified goals over optimism.

license: MIT

---

# YAGNI Coding Discipline

Apply these guidelines before and during implementation. They bias toward caution over speed; for genuinely trivial tasks use your judgment and do not manufacture ceremony.

## When to use

* Any implementation or edit, regardless of size.
* Any task tagged as a refactor, "improve", "clean up", or "make robust".
* Any task where the requirement has more than one plausible reading.

## Core principles

### 1. Think before coding

* State important assumptions before writing code.
* If multiple interpretations materially affect the result, present them rather than choosing silently.
* If a simpler approach exists, say so.
* Push back when the requested approach appears unnecessarily complex.
* If something is unclear, determine whether it is actually blocking.

Ask the user before proceeding when ambiguity affects:

* architecture
* security
* data integrity
* public interfaces
* significant scope
* irreversible decisions

For ordinary implementation details with a reasonable conventional solution, make the simplest reasonable choice, state the assumption briefly, and proceed.

### 2. Simplicity first

Write the minimum code that solves the problem. Nothing speculative.

* No features beyond what was asked.
* No abstractions for single-use code.
* No "flexibility" or "configurability" that was not requested.
* No error handling for impossible scenarios.
* If a solution can be 50 lines instead of 200, prefer the simpler solution.
* Ask: *"Would a senior engineer call this overcomplicated?"* If yes, simplify.

Do not implement future milestones merely because the architecture may eventually need them.

### 3. Surgical changes

Touch only what you must.

* Do not improve adjacent code, comments, or formatting while working in a file.
* Do not refactor code that is not broken or required by the task.
* Match the existing style.
* If you notice unrelated dead code, mention it; do not delete it unless asked.
* When your changes create orphans, remove imports/variables/functions made unused by your changes.
* Do not remove pre-existing dead code unless asked.

The test:

> Every changed line should trace directly to the user's request or to a necessary consequence of the implementation.

### 4. Goal-driven execution

Turn tasks into verifiable goals, then loop until verified.

| Vague ask                     | Verifiable form                                                                  |
| ----------------------------- | -------------------------------------------------------------------------------- |
| "Add validation"              | "Write tests for invalid inputs, then make them pass"                            |
| "Fix the bug"                 | "Write a test that reproduces it, then make it pass"                             |
| "Refactor X"                  | "Ensure tests pass before and after"                                             |
| "Add database initialization" | "Start the database from a clean state and verify expected tables/data exist"    |
| "Add an agent workflow"       | "Run a representative task and verify the expected state transitions and result" |

For multi-step tasks, write a short plan with a verify check per step:

```text
1. [Step] → verify: [concrete check]
2. [Step] → verify: [concrete check]
3. [Step] → verify: [concrete check]
```

### 5. Preserve existing behavior

Before changing an existing component:

* understand how it currently works
* identify its callers and tests
* preserve existing behavior unless the task explicitly changes it
* avoid replacing working implementations merely because another design is preferred

For architectural changes, prefer incremental migration over large rewrites.

## Working definition

These guidelines are working when:

* diffs contain fewer unnecessary changes
* existing functionality remains stable
* assumptions are surfaced before they become bugs
* clarifying questions come before consequential mistakes
* implementation is verified rather than assumed
* future complexity is not implemented prematurely

## Merge with project specifics

Before coding in a repo, read its context files if present.

For this project:

* `AGENTS.md` contains development environment and project rules.
* `PLAN.md` contains the current architecture, milestones, decisions, and project status.
* The `code-review` skill defines the review standard.
* The `verification` skill defines the expected evidence for claiming work is complete.
* The `architecture-planning` skill should be used when a change affects multiple components or system boundaries.

Follow the repository's established conventions rather than importing your own.

## Layout

* `SKILL.md` — this file.
* `references/PROJECT.md` — project-specific integration points and size/scope norms.
