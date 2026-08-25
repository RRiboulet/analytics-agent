---

name: architecture-planning

description: Lightweight architectural planning for coding-agent tasks that affect multiple components, boundaries, data flows, or external integrations. Encourages explicit boundaries, incremental implementation, reversible decisions, and verification without creating unnecessary design ceremony.

license: MIT

---

# Architecture Planning

Use lightweight architectural reasoning before implementing changes that affect multiple components or system boundaries.

The goal is not to produce a large design document. The goal is to understand the change well enough to implement it safely and incrementally.

## When to use

* Changes affecting multiple modules or services.
* New external integrations.
* Database or schema changes.
* Agent orchestration changes.
* MCP/API boundary changes.
* Docker/infrastructure changes.
* Refactors that alter ownership or data flow.
* Any milestone in `PLAN.md` explicitly requiring architectural planning.

For isolated, local changes, use judgment and do not manufacture a design exercise.

## Core principles

### 1. Understand before designing

Before proposing a new architecture:

* inspect the existing implementation
* identify what already works
* identify existing boundaries
* identify callers and dependencies
* read the relevant project context

Do not design against an imagined codebase.

### 2. Define boundaries

For each significant change, identify:

* which component owns the behavior
* how components communicate
* what data crosses the boundary
* which component validates it
* which component is responsible for failures

Prefer one clear owner over duplicated responsibility.

### 3. Prefer incremental changes

Break architectural work into small, independently verifiable steps.

For each step identify:

```text
Change:
Why:
Affected components:
Verification:
```

Avoid implementing the entire future architecture when only one milestone is required.

### 4. Prefer reversible decisions

When multiple reasonable designs exist:

* prefer the simpler design
* prefer existing project dependencies
* prefer existing infrastructure
* prefer changes that can be replaced later
* avoid introducing infrastructure solely for hypothetical future requirements

Call out decisions that are difficult or expensive to reverse.

### 5. Consider alternatives

For consequential decisions, briefly consider at least one simpler alternative.

For example:

```text
Option A:
Use the existing PostgreSQL instance with pgvector.

Option B:
Introduce a separate vector database.

Preferred:
Option A because it satisfies the current requirement with less infrastructure.
```

Do not produce exhaustive architecture comparisons for minor decisions.

### 6. Respect project milestones

`PLAN.md` defines the intended sequence of work.

Do not implement future architecture prematurely.

For example, while implementing database ingestion:

* do not build the LangGraph agent
* do not build evaluation infrastructure
* do not introduce agent-specific abstractions

unless the current milestone genuinely requires them.

### 7. Identify risks

For meaningful architectural changes, explicitly check:

* security
* data integrity
* failure modes
* lifecycle/resource management
* backwards compatibility
* testing strategy
* observability
* operational complexity

Only discuss risks relevant to the proposed change.

## Planning output

For a substantial architectural task, produce a concise plan containing:

1. **Current state** — what exists and can be reused.
2. **Target change** — what needs to be introduced or modified.
3. **Boundaries** — which components own which responsibilities.
4. **Implementation steps** — small, ordered steps.
5. **Verification** — how each step will be proven.
6. **Risks/decisions** — only consequential items.
7. **Open questions** — only questions that block or materially affect the implementation.

Do not write production code during the planning phase unless explicitly asked.

## Working definition

Architecture planning is working when:

* the implementation has clear ownership boundaries
* unnecessary components are rejected
* consequential decisions are explicit
* the implementation can proceed incrementally
* each major step has a verification strategy
* future possibilities have not leaked into the current scope

## Merge with project specifics

Before planning in this repo:

* read `AGENTS.md`
* read `PLAN.md`
* inspect the current implementation
* use the `yagni-coding` skill for scope discipline
* use the `verification` skill to define evidence for the proposed steps

The `code-review` skill should be used after implementation when the resulting diff warrants review.

## Layout

* `SKILL.md` — this file.
* `references/PROJECT.md` — project-specific architecture and integration points loaded on demand.
