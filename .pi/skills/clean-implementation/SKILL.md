---
name: clean-implementation
description: Guides an agent to implement the smallest complete, maintainable change after scope is understood, while preserving project conventions and compatibility.
---

# Clean Implementation

## Purpose

Implement the requested behavior with the smallest complete and maintainable change, preserving established architecture, contracts, compatibility, and repository hygiene.

## When to use

Use after the task is understood and planned, and whenever implementation must be corrected in response to test, analysis, or review findings.

## Inputs and project discovery

Inputs include the approved outcome and acceptance criteria, repository evidence, implementation plan, existing baseline, and relevant source/tests/configuration.

Before acting, discover and read repository-local instructions and the configured commands relevant to this skill. Inspect `config/quality.yaml`, `.agent/quality.yaml`, or another path declared by the host integration when available. Repository-specific instructions override generic defaults unless they weaken an explicit user requirement or create unsafe behavior. Treat commands as project data: never invent one. If a needed command cannot be discovered, report it as `unavailable` rather than pretending it ran. Never report `passed` unless the command actually ran and returned success.

Reconfirm the affected architecture, local style, ownership boundaries, public contracts, generated/vendor paths, compatibility expectations, and focused checks before editing. If the prior plan is stale or contradicted by the repository, return to understanding and planning.

## Procedure

1. Confirm the smallest coherent change that satisfies every acceptance criterion. Avoid touching unrelated files.
2. Follow existing architecture and idioms. Prefer readable, cohesive units; clear names; explicit contracts; appropriate types where the language supports them; low duplication; and deterministic behavior.
3. Handle errors deliberately. Preserve useful failure context, avoid swallowed failures, validate trust boundaries, protect sensitive data, and clean up resources on every relevant path.
4. Preserve compatibility unless change is explicitly required. Account for public APIs, command-line behavior, configuration, serialization, schemas, persisted data, and callers as applicable.
5. Avoid speculative abstractions, broad rewrites, dead code, hidden behavior, and unjustified dependencies. Add a dependency only when repository policy permits it and its value, maintenance, security, and compatibility costs are justified.
6. Keep generated and vendor files untouched unless the task requires them and repository configuration defines the correct regeneration or update process.
7. Update documentation, examples, configuration, contracts, or migration notes only where changed behavior requires it.
8. Reinspect the diff repeatedly for scope creep, duplication, accidental formatting churn, secrets, binaries, and incomplete paths.
9. Run discoverable focused checks during implementation. Fix root causes rather than masking diagnostics. If findings change scope or assumptions, repeat planning before continuing.

## Non-negotiable rules

- Make no unrelated edits and no broad cleanup disguised as implementation.
- Never invent commands or results. Classify checks as `passed`, `failed`, `not_run`, or `unavailable`; `passed` requires actual successful execution.
- Do not silently suppress diagnostics, swallow failures, or hide behavior.
- Do not delete, weaken, or skip tests merely to make a check pass.
- Do not reduce coverage or quality thresholds without explicit user instruction.
- Do not modify generated/vendor artifacts unless required and supported by the configured process.
- Do not claim the implementation is bug-free; report evidence and residual risks.

## Completion criteria

- The smallest complete change satisfies the known acceptance criteria.
- The implementation matches repository architecture and style and has clear contracts, errors, cleanup, security, compatibility, and deterministic behavior where relevant.
- Required docs/config/examples are updated without unrelated churn.
- The diff has been reinspected and contains no known accidental changes.
- Focused checks were run when configured, with exact statuses and unresolved failures disclosed.
- The change is ready for comprehensive testing, not merely assumed correct.

## Output/report format

```text
Implemented: <behavior and key files>
Design/compatibility notes: <contracts, errors, data, security>
Diff scope: <intended files; generated/vendor status>
Focused checks:
- <command or check>: passed|failed|not_run|unavailable — <result/reason>
Follow-up needed: <tests, docs, migration, unresolved item>
Residual risks: <known limits; never “bug-free”>
```
