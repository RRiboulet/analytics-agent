---
name: understand-and-plan
description: Guides an agent to investigate a coding task, assess repository impact and risk, establish a baseline, and produce a safe implementation and validation plan before editing.
---

# Understand and Plan

## Purpose

Build enough evidence to change the right behavior with minimal risk. Convert the request and repository context into explicit scope, assumptions, acceptance criteria, dependencies, and a concise plan that includes testing and validation.

## When to use

Use this skill at the start of any code change, defect fix, refactor, migration, review-led repair, or other task whose impact must be understood. Repeat it when implementation or test evidence invalidates an assumption or reveals broader impact.

## Inputs and project discovery

Inputs may include the user request, issue text, acceptance criteria, repository contents, current diff, and host-provided context.

Before acting, discover and read repository-local instructions and the configured commands relevant to this skill. Inspect `config/quality.yaml`, `.agent/quality.yaml`, or another path declared by the host integration when available. Repository-specific instructions override generic defaults unless they weaken an explicit user requirement or create unsafe behavior. Treat commands as project data: never invent one. If a needed command cannot be discovered, report it as `unavailable` rather than pretending it ran. Never report `passed` unless the command actually ran and returned success.

Locate and inspect, as relevant:

- instructions, contribution guidance, architecture notes, and ownership boundaries;
- affected source, nearby patterns, existing tests, fixtures, configuration, schemas, and documentation;
- dependency direction, callers, callees, data flow, side effects, and integration seams;
- public interfaces, compatibility commitments, persisted data, migrations, and generated/vendor boundaries;
- configured baseline, focused, full, coverage, build, analysis, and final-gate commands.

## Procedure

1. Restate the requested outcome and derive observable acceptance criteria. Separate explicit requirements from assumptions.
2. Trace the relevant behavior through source, tests, configuration, dependencies, and call sites. Identify public or externally observable behavior.
3. Assess constraints and risks: edge and invalid cases, error paths, security and privacy, sensitive data, authorization, concurrency or state, resource lifetime, compatibility, performance, and migration impact where relevant.
4. Identify the smallest plausible change surface and files that should remain untouched, including generated and vendor content.
5. Discover existing project-defined checks. When practical and safe, run applicable baseline checks before editing so pre-existing failures can be distinguished from regressions. Record exact command, status, and concise result.
6. Resolve ambiguity with a safe, stated assumption when it is reversible, low risk, and consistent with repository evidence. Ask only when no safe assumption exists or the choice would materially alter behavior, security, compatibility, or scope.
7. Produce a concise ordered plan covering implementation, tests, coverage, focused validation, full validation, documentation/config changes, and rollback or migration concerns when relevant.
8. Do not edit code in this skill unless the host task explicitly combines planning and implementation.

## Non-negotiable rules

- Do not invent project behavior, commands, results, or acceptance criteria.
- Do not claim a check passed unless it actually ran successfully; distinguish `passed`, `failed`, `not_run`, and `unavailable`.
- Do not make unrelated edits or propose broad rewrites without a requirement-backed reason.
- Do not plan silent diagnostic suppression, test deletion/weakening/skipping merely to pass, or threshold reduction without explicit user instruction.
- Never describe the planned result as literally bug-free. State evidence needs and residual risks.
- Escalate unsafe or irreconcilable instruction conflicts instead of weakening a higher-priority requirement.

## Completion criteria

- The outcome, scope, acceptance criteria, assumptions, and unresolved questions are explicit.
- Relevant instructions, code, tests, config, dependencies, interfaces, risks, and boundaries were inspected.
- Baseline checks were run when practical, or each omission is classified and explained.
- The plan is minimal, ordered, actionable, and includes meaningful tests and final validation.
- No code was edited unless planning and implementation were explicitly combined.

## Output/report format

```text
Scope: <requested outcome and affected behavior>
Acceptance criteria: <concise list>
Repository evidence: <instructions, files, interfaces, dependencies>
Assumptions/questions: <safe assumptions and blockers>
Risks: <edge, security/data, compatibility, state, performance>
Baseline checks:
- <command or check>: passed|failed|not_run|unavailable — <result/reason>
Plan:
1. <implementation step>
2. <test/coverage step>
3. <quality/final-validation step>
Residual planning risk: <what remains uncertain>
```
