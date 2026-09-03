# Reusable Coding Skills

A portable bundle of five broad workflow skills for coding agents. The skills are deliberately agent-, language-, framework-, build-system-, and project-agnostic. Each skill tells the agent to discover local policies and configured commands rather than assume a particular toolchain.

## Workflow

Use the skills as an iterative sequence:

1. **understand-and-plan** — inspect the repository, clarify requirements, assess impact and risk, establish a baseline, and make a concise implementation and validation plan.
2. **clean-implementation** — make the smallest complete change that fits the project's architecture, conventions, contracts, and compatibility requirements.
3. **comprehensive-testing** — design risk-based tests for behavior, boundaries, failures, regressions, and integration seams, with coverage evidence.
4. **automated-quality-checks** — run only repository-defined or configured format, analysis, build, security, test, documentation, packaging, and related checks.
5. **final-review-and-validation** — compare the final diff with the requirements, inspect for accidental changes, run the final gate, and report exact evidence and residual risk.

The sequence is not strictly linear. Later skills may return to planning, implementation, or testing when evidence reveals a gap. A host agent may invoke the skills individually or apply all five to one task.

## Core operating rules

- Discover repository-local instructions, relevant configuration, and configured commands before acting.
- When present, use `config/quality.yaml`, `.agent/quality.yaml`, or another quality-config path declared by the host integration.
- Commands are project data. Never invent a command, silently substitute another tool, or imply a command ran when it did not.
- Report every relevant check as exactly one of: `passed`, `failed`, `not_run`, or `unavailable`.
- Do not claim software is literally “bug-free.” Report evidence and residual risks.
- Make no unrelated edits. Do not weaken or delete tests merely to pass, silently suppress diagnostics, or reduce quality thresholds without explicit user instruction.
- Repository-specific instructions override generic skill defaults unless they weaken an explicit user requirement or create unsafe behavior.

## Installation and host discovery

Copy or link each directory under `skills/` into the skills location recognized by the host coding agent, or point the host agent to this bundle if it supports bundle discovery. Consult the host's current documentation for exact paths, naming, loading, and discovery rules. This bundle does not assume that any particular release of pi, GitHub Copilot, or another agent uses a fixed path.

Each skill follows the common Agent Skills shape: one directory containing a `SKILL.md` file with YAML front matter.

## Optional per-project quality configuration

1. Copy `config/quality.example.yaml` into the target repository as either `.agent/quality.yaml` or `config/quality.yaml`.
2. Replace placeholders with commands and paths that are genuinely defined for that project.
3. Remove command entries that do not apply. An empty list means **not configured**; it is not permission for an agent to invent a command.
4. Commit the project-local file if the team wants the same quality contract applied consistently.

A host integration may declare another config path. If more than one file exists, follow repository or host instructions to choose one and report the selected source.

## Precedence

Apply constraints in this order, from highest to lowest:

1. Explicit user or task requirements.
2. Repository policies and instructions.
3. Project quality configuration.
4. Defaults in these skills.

A lower-priority setting cannot weaken a higher-priority constraint. Safety requirements always apply. If instructions conflict in a way that cannot be resolved safely, stop and ask for clarification.

## Coverage policy

The default target is **100% line coverage and 100% branch coverage for new or changed executable code**. Project configuration may broaden the scope, define equivalent ecosystem-specific metrics, or document justified exclusions. Coverage is evidence, not proof of correctness or absence of defects; meaningful assertions, risk-based cases, review, and other checks remain necessary.

## Example invocation

> Use the five reusable coding skills for this task and enforce the project quality config.

## Bundle layout

```text
reusable-coding-skills/
  README.md
  skills/
    understand-and-plan/SKILL.md
    clean-implementation/SKILL.md
    comprehensive-testing/SKILL.md
    automated-quality-checks/SKILL.md
    final-review-and-validation/SKILL.md
  config/
    quality.example.yaml
```
