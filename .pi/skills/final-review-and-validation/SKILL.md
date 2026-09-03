---
name: final-review-and-validation
description: Guides an agent to review the final diff against requirements, run the final project-defined gate, detect accidental changes, and report evidence and residual risks.
---

# Final Review and Validation

## Purpose

Perform a final evidence-based review of requirements, implementation, tests, repository state, and automated gates before declaring the task complete.

## When to use

Use after implementation and testing appear complete, before presenting a final answer, patch, pull request, or handoff. If the review reveals a gap, return to an earlier skill and repeat final validation after all edits.

## Inputs and project discovery

Inputs include the user request, acceptance criteria, repository instructions, project quality config, final diff/status, implementation and test changes, coverage evidence, command results, and known baseline limitations.

Before acting, discover and read repository-local instructions and the configured commands relevant to this skill. Inspect `config/quality.yaml`, `.agent/quality.yaml`, or another path declared by the host integration when available. Repository-specific instructions override generic defaults unless they weaken an explicit user requirement or create unsafe behavior. Treat commands as project data: never invent one. If a needed command cannot be discovered, report it as `unavailable` rather than pretending it ran. Never report `passed` unless the command actually ran and returned success.

Re-read relevant requirements and local policies. Identify the configured final gate and repository-state inspection mechanism. Use version-control diff/status or the host equivalent; do not assume a specific version-control tool exists.

## Procedure

1. Compare every requirement and acceptance criterion with the final diff and observable behavior. Identify omissions, scope creep, unsupported assumptions, and stale documentation.
2. Review correctness and edge cases. Check security and privacy, sensitive data, authorization, compatibility, error handling, cleanup, concurrency/state, performance risks, and data/schema/migration implications where relevant.
3. Review externally visible interfaces, including public APIs, command-line behavior, configuration, files/formats, messages, and integration contracts. Confirm required documentation and examples match behavior.
4. Look for unnecessary complexity, speculative abstractions, dead code, duplication, hidden behavior, swallowed failures, or unjustified dependencies.
5. Confirm tests meaningfully cover acceptance criteria and risks rather than merely executing lines. Verify configured coverage scope and targets, including the default 100% line and branch target for changed executable code when no stronger project rule applies.
6. Inspect final version-control diff/status or the host equivalent for accidental, unrelated, generated, vendor, secret, credential, binary, lockfile, or formatting-only changes. Investigate every unexpected item.
7. After all edits, run the final project-defined quality gate and any required checks not included in it. Do not invent or substitute commands. Record exact statuses.
8. Do not declare completion with known relevant failures. If environment or dependency limitations prevent a gate, mark it `not_run` or `unavailable`, explain the blocker, and state the residual risk.
9. Produce the final report with change summary, tests, coverage, quality checks, and residual risks. Do not claim the software is bug-free.

## Non-negotiable rules

- Do not approve unrelated, accidental, secret, unexplained binary, or improperly generated changes.
- Do not claim a command passed unless it actually ran successfully. Use exactly `passed`, `failed`, `not_run`, or `unavailable`.
- Do not hide relevant failures, including baseline and environment failures.
- Do not silently suppress diagnostics, weaken/delete/skip tests merely to pass, or reduce thresholds without explicit user instruction.
- Do not declare complete while a known relevant required check is failing.
- State residual risks explicitly; never assert literal absence of defects.

## Completion criteria

- Every requirement and acceptance criterion is satisfied or explicitly identified as unresolved.
- The final diff is scoped, reviewed, and free of known accidental or unsafe content.
- Relevant correctness, interface, security/privacy, compatibility, state, performance, data, migration, and documentation concerns were reviewed.
- Tests are meaningful and coverage evidence meets configured targets or the exact gap is reported.
- The final configured gate ran after all edits, or its inability to run is precisely classified with residual risk.
- The final report contains all required sections and exact statuses, with no unsupported completion claim.

## Output/report format

```text
Change summary: <what changed and why>
Requirements review: <met items and any unresolved item>
Tests:
- <command/check>: passed|failed|not_run|unavailable — <result/reason>
Coverage:
- <command/check>: passed|failed|not_run|unavailable — scope/line/branch/exclusions
Quality checks:
- <command/check>: passed|failed|not_run|unavailable — <result/reason>
Diff/status review: passed|failed|not_run|unavailable — <unexpected/generated/secret/binary findings>
Residual risks: <environment limits, untested conditions, known uncertainty>
Completion: passed|failed|not_run|unavailable — <evidence-based conclusion>
```
