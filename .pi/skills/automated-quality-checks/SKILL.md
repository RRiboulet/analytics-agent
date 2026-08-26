---
name: automated-quality-checks
description: Guides an agent to discover, execute, and accurately report the repository's configured automated checks during development and before completion.
---

# Automated Quality Checks

## Purpose

Use the project's own automated quality controls as reproducible evidence. Run applicable checks without inventing commands, substituting tools, masking findings, or expanding the change unnecessarily.

## When to use

Use during implementation for narrow feedback and after code, tests, docs, or configuration are final for the complete configured gate. Repeat affected checks after every relevant edit.

## Inputs and project discovery

Inputs include the current diff, repository instructions, project quality configuration, package/build metadata, automation definitions, baseline evidence, and test results.

Before acting, discover and read repository-local instructions and the configured commands relevant to this skill. Inspect `config/quality.yaml`, `.agent/quality.yaml`, or another path declared by the host integration when available. Repository-specific instructions override generic defaults unless they weaken an explicit user requirement or create unsafe behavior. Treat commands as project data: never invent one. If a needed command cannot be discovered, report it as `unavailable` rather than pretending it ran. Never report `passed` unless the command actually ran and returned success.

Discover applicable project-defined commands for formatter verification, lint/static analysis, type or contract checks, build/compile, security, dependency review, generated-artifact verification, focused/full tests, coverage, documentation, packaging, and any other validation or final gate. Empty config lists mean not configured, not permission to invent commands.

## Procedure

1. Determine which configured checks apply to the changed files and behavior. Observe required flags, working directories, environment declarations, timeouts, and setup steps.
2. Prefer narrow configured checks during development for fast feedback. After all edits, run the full configured gate in the required order.
3. Run commands exactly as configured or discovered. Do not silently replace a missing command, tool, argument, or gate with an alternative.
4. For each check, record its exact command or stable configured name, status, and concise output summary. Use only `passed`, `failed`, `not_run`, or `unavailable`.
5. Diagnose failures and fix root causes. Keep formatter-driven changes scoped to intended files and inspect resulting diffs.
6. Use a diagnostic suppression only when project policy permits it, the diagnostic is not usefully fixable, and the suppression is narrowly scoped with an explicit rationale.
7. Distinguish failures introduced by the change from baseline or environment failures. Report both; baseline status never hides a new regression.
8. Re-run every affected check after fixes. A prior successful result is stale if later edits can affect it.
9. If a required command cannot run because of missing configuration, tools, credentials, services, permissions, dependencies, or environment support, mark it `unavailable` or `not_run` as appropriate and state the blocker and residual risk.

## Non-negotiable rules

- Never invent commands, flags, or successful results, and never silently substitute a different tool.
- A check is `passed` only if it actually ran and returned success.
- Do not silently suppress diagnostics or ignore a required failure.
- Do not delete, weaken, or skip tests merely to pass, and do not reduce thresholds without explicit user instruction.
- Make no unrelated changes; formatter changes must remain scoped.
- Do not declare software bug-free. Automated checks are evidence with limits.

## Completion criteria

- All applicable configured checks have an exact status and concise result.
- The full configured gate was run after all relevant edits, or blockers are explicit.
- New failures are fixed or reported; baseline/environment failures remain visible and differentiated.
- Required failures are not hidden, suppressions are narrow and justified, and no threshold or test was improperly weakened.
- The working diff remains scoped after automated fixes.

## Output/report format

```text
Quality checks:
- <name>: <exact command> — passed|failed|not_run|unavailable — <concise result>
Failure attribution: <change-caused, baseline, environment, or unknown>
Fixes/re-runs: <root cause addressed and affected checks repeated>
Suppressions: <none, or narrow rationale and location>
Gate status: passed|failed|not_run|unavailable
Residual risks: <limitations and unresolved findings>
```
