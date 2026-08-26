---
name: comprehensive-testing
description: Guides an agent to design and run meaningful risk-based tests and coverage analysis for new or changed behavior without weakening the test suite.
---

# Comprehensive Testing

## Purpose

Provide evidence that changed behavior works across relevant nominal, boundary, failure, and integration conditions. Use coverage to expose unexercised code, while recognizing that coverage alone does not prove correctness.

## When to use

Use while implementing behavior, after a defect fix or refactor, when a regression needs protection, and before final quality validation. Repeat after any later code change.

## Inputs and project discovery

Inputs include acceptance criteria, risk analysis, final or evolving diff, existing tests and fixtures, public contracts, defect reproduction details, and project quality configuration.

Before acting, discover and read repository-local instructions and the configured commands relevant to this skill. Inspect `config/quality.yaml`, `.agent/quality.yaml`, or another path declared by the host integration when available. Repository-specific instructions override generic defaults unless they weaken an explicit user requirement or create unsafe behavior. Treat commands as project data: never invent one. If a needed command cannot be discovered, report it as `unavailable` rather than pretending it ran. Never report `passed` unless the command actually ran and returned success.

Inspect test organization, naming, helpers, fixture conventions, isolation strategy, configured test/coverage scope, justified exclusions, and available focused/full commands. Prefer established project patterns unless they conflict with explicit requirements or safety.

## Procedure

1. Map each acceptance criterion and material risk to observable test evidence. Test behavior and contracts, not merely changed lines.
2. Add or update tests for relevant nominal cases, boundaries, invalid inputs, error paths, state transitions, externally visible side effects, regression scenarios, and integration seams.
3. Make tests deterministic, isolated, readable, and diagnostic. Control time, randomness, ordering, shared state, network, filesystem, and concurrency where relevant using project-approved patterns.
4. Assert meaningful outcomes rather than implementation trivia. Avoid coupling to private structure unless that structure is itself the required contract.
5. Default to 100% line and 100% branch coverage for new or changed executable code. Apply a configured broader repository scope or equivalent ecosystem-specific metrics when required.
6. Treat exclusions only as justified exceptions, such as generated code or genuinely unreachable defensive paths. Every exclusion needs a documented rationale allowed by project policy.
7. Never game coverage with empty assertions, execute-only tests, excessive exclusions, or tests that simply duplicate implementation logic.
8. Run the project-defined focused test command when available, then the full test and coverage commands when available. Do not invent a runner or flags. Record exact command, status, and concise result.
9. Investigate failures. Fix product or test defects at their root; do not delete, weaken, or skip a valid test merely to pass. Repeat affected checks after changes.
10. Report coverage scope, metrics, uncovered relevant paths, exclusions, and residual behavioral risks. Coverage is evidence, not proof of correctness.

## Non-negotiable rules

- Tests must be meaningful, deterministic, isolated, and behavior-focused.
- Do not weaken, delete, skip, or replace valid tests merely to obtain a passing result.
- Do not reduce thresholds without explicit user instruction, silently suppress diagnostics, or add unjustified exclusions.
- Never invent commands. Distinguish `passed`, `failed`, `not_run`, and `unavailable`; never claim success without an actual successful run.
- Do not make unrelated production or test edits.
- Never claim the tested software is bug-free. Explicitly report residual risks and untested conditions.

## Completion criteria

- Acceptance criteria and material risks have corresponding tests or a documented reason they cannot be tested.
- Relevant nominal, boundary, invalid, failure, state, side-effect, regression, and integration cases are covered.
- Tests are maintainable and prove outcomes rather than execution alone.
- The configured coverage target is met, or the exact shortfall and justified exclusions are reported.
- Focused, full, and coverage commands were run where configured and possible, with exact statuses.
- No test or threshold was improperly weakened to make the result pass.

## Output/report format

```text
Test changes: <behaviors and risks covered>
Focused tests: <command/check> — passed|failed|not_run|unavailable — <result/reason>
Full tests: <command/check> — passed|failed|not_run|unavailable — <result/reason>
Coverage: <command/check> — passed|failed|not_run|unavailable
Coverage evidence: scope=<changed_code|repository|configured>; line=<value>; branch=<value>
Exclusions/uncovered risk: <rationale and relevant paths>
Residual risks: <limits not disproved by tests>
```
