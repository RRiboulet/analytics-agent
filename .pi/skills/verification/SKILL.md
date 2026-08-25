---

name: verification

description: Evidence-based verification for coding-agent work. Load after or during implementation when changes affect application behavior, databases, services, integrations, agents, or infrastructure. Prevents claiming success based only on code inspection or partial tests.

license: MIT

---

# Verification

Verify behavior with concrete evidence instead of assuming that an implementation works because the code looks correct.

## When to use

* After implementing or modifying functionality.
* After changes involving multiple components or services.
* After database, Docker, MCP, agent, or infrastructure changes.
* Before declaring a milestone complete.
* Whenever a task has a meaningful runtime behavior.

For genuinely trivial changes, use judgment and do not manufacture unnecessary ceremony.

## Core principles

### 1. Verify the requested behavior

Determine what must be true for the task to be considered complete.

Prefer concrete checks such as:

* tests
* database queries
* MCP tool invocation
* application execution
* integration tests
* end-to-end workflows
* inspection of generated output

Do not rely solely on static inspection.

### 2. Use the smallest sufficient verification

Match verification depth to the change.

| Change                  | Typical verification                                   |
| ----------------------- | ------------------------------------------------------ |
| Pure function           | Unit tests                                             |
| Validation/safety logic | Positive and negative tests                            |
| Database schema         | Start database + inspect schema/data                   |
| MCP tool                | Unit/integration test + real invocation when practical |
| Agent node              | Unit test + representative workflow                    |
| LangGraph workflow      | End-to-end representative task                         |
| Docker/service change   | Start service + health/connectivity check              |
| External integration    | Real integration or explicit documented limitation     |

Do not perform expensive end-to-end verification when a focused test proves the behavior.

### 3. Verify boundaries

For changes involving multiple components, verify the boundaries between them.

Examples:

```text
Agent → MCP
MCP → PostgreSQL
Metadata retrieval → pgvector
LangGraph state → next node
Application → external service
```

A component passing its unit tests does not prove that its integration boundary works.

### 4. Verify failure paths

When the implementation includes error handling, verify meaningful failure paths.

For example:

* invalid SQL
* missing table/column
* empty query result
* unavailable service
* malformed tool input
* agent retry
* maximum-attempt termination

Do not test impossible failures merely for coverage.

### 5. Agent-specific verification

For agent workflows, verify more than the final answer.

Where practical, inspect:

* relevant metadata was retrieved
* expected tools were called
* generated SQL was valid
* SQL remained within the read-only boundary
* tool results reached the appropriate state
* retries occurred only when needed
* maximum attempts terminate the workflow
* final conclusions are supported by query results
* important workflow states are observable

A plausible final answer is not sufficient evidence that an analytics agent worked correctly.

### 6. Report evidence honestly

When reporting completion, distinguish between:

```text
Verified:
- pytest passes
- database starts
- representative query succeeds

Not verified:
- full end-to-end agent workflow
- production-like load
```

Never claim a behavior was verified when it was only inferred.

If verification cannot be performed, state why and identify the remaining risk.

## Goal-driven verification

For multi-step work:

```text
1. [Implementation step] → verify: [specific behavior]
2. [Integration step]   → verify: [specific boundary]
3. [Final behavior]     → verify: [representative workflow]
```

Keep verification proportional to the task.

## Definition of done

A change is not complete merely because:

* it compiles
* imports succeed
* no exception occurred during a single happy path
* unit tests pass when integration behavior is affected
* the implementation "should work"

The appropriate verification must provide evidence for the requested behavior.

## Merge with project specifics

Before verifying work in this repo:

* read `AGENTS.md`
* read the relevant section of `PLAN.md`
* inspect existing tests and project conventions

Use `uv`, pytest, ruff, Docker Compose, and the project's existing test infrastructure as appropriate.

The `code-review` skill defines the review standard after implementation.

## Layout

* `SKILL.md` — this file.
* `references/PROJECT.md` — project-specific verification commands and known integration boundaries, loaded on demand.
