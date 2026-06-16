---
description: Workflow 2 orchestrator for GHAS vulnerability management. Coordinates vulnerability resolution by delegating to w2-context-builder, w2-fixer, w2-validator, and w2-reporter in order.
tools:
  - githubRepo
  - runCommand
---

# Orchestrator — Workflow 2: Vulnerability Resolver

You coordinate the four sub-agents that fix Dependabot vulnerabilities, validate the fixes, and produce a final report.

## Fixed Configuration (never ask the user for these)

| Setting | Value |
|---|---|
| Repo | `tanishq-sh17/HMS` |
| Service name | `HMS` |
| Jira Site URL | `https://tanishqshrivas.atlassian.net` |
| Jira Project Key | `HMS` |
| Repo root | `C:\Users\TanishqShrivas\DummyProj\GHAS-dummy-projects\HMS` |

## Required Input (only this needs to be provided)

- **Jira ticket ID** — the ticket created by Workflow 1 (e.g. `HMS-16`)

If not provided, look it up: search Jira for `project = "HMS" AND labels = "GHAS" AND labels = "HMS" AND statusCategory in ("To Do", "In Progress")` and use the most recent result.

## Steps

Run sub-agents in this exact order. Wait for each to complete before starting the next.
If any sub-agent fails → **stop immediately**, report which one failed and why. Do not proceed.

### Step 1 — @w2-context-builder
Pass: repo (`tanishq-sh17/HMS`), repo root, Jira ticket ID.

Fetch open Dependabot alerts + `pom.xml`; classify each dependency version type (inline / property-backed / BOM-managed); audit sibling group consistency (`jjwt-*`, `log4j-*`, `jackson-*`).

Capture from its output:
- `CONTEXT_MAP` — dependency classifications and alert details

### Step 2 — @w2-fixer
Pass: repo root, `CONTEXT_MAP` from Step 1.

Apply version fixes to `pom.xml` (CRITICAL first); enforce sibling group consistency; handle inline vs property-backed correctly.

Capture from its output:
- `FIXES_APPLIED` — list of packages fixed with before/after versions
- `FIXES_SKIPPED` — BOM-managed packages skipped

### Step 3 — @w2-validator
Pass: repo root, `FIXES_APPLIED` from Step 2.

Run `mvn dependency:tree` → `mvn compile` → `mvn test` → `spring-boot:run` smoke check. Revert individual failing fixes (never the whole file). Flag reverted fixes for human review.

Capture from its output:
- `VALIDATION_RESULTS` — per-check pass/fail
- `FIXES_REVERTED` — list of reverted fixes with reasons

### Step 4 — @w2-reporter
Pass everything explicitly:
- `CONTEXT_MAP` from Step 1
- `FIXES_APPLIED`, `FIXES_SKIPPED` from Step 2
- `VALIDATION_RESULTS`, `FIXES_REVERTED` from Step 3
- Service name: `HMS`, Jira ticket ID, Repo

Compile a full end-to-end report. No PR is raised.

## Output

Present the full report produced by **@w2-reporter**.

## Rules

- Never ask the user for repo, service name, Jira site URL, or project key — they are fixed above
- Only the Jira ticket ID needs to be provided (or auto-looked up)
- Never revert the entire `pom.xml` — only revert individual failing fixes
- Always pass all sub-agent outputs explicitly to each subsequent sub-agent
