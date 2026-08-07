---
name: shared-plan-storage
description: Resolve and maintain implementation plans by stable ID in separately selected private plan hubs.
---

# Shared Plan Storage

## Select the hub

The public client checkout contains software only. Select private plan-hub checkouts explicitly; never infer plan storage from the installed skill or client location. Multiple hubs may be registered by name in `~/.config/plans-hub/roots.json`; `planctl roots` lists the effective root set with each root's validity.

- **Explicit hub mention wins.** If the user names a hub by configured name or path, resolve it with `--root NAME` (or `--root PATH`) and proceed — no prompt, no scan.
- **Saving a new plan:** run `planctl roots`. If the effective set has more than one valid root and the user did not name a hub, ask which root to use before `planctl allocate`; never default silently. With exactly one root, proceed without asking.
- **Resolving an existing bare ID:** run `planctl locate <ID>` to scan every configured hub. On `unique`, operate on the returned hub. On `ambiguous`, ask the user which hub. On `not-found`, report the hubs searched.

Single-hub commands resolve exactly one root: `--root PATH|NAME` beats a single-root registry; a multi-root registry never guesses and asks for `--root NAME`. When a bare ID fails in the resolved hub and multiple hubs are configured, planctl suggests `planctl locate <ID>`.

Before changing state, read the selected hub's `AGENTS.md`, `README.md`, and `ORCHESTRATION.md`; read `RESEARCH.md` when present before a research mutation. Use `planctl scan` for a read-only inventory; raw or malformed Markdown remains inactive until it satisfies the public client's managed-plan contract. Resolve an existing plan with `planctl show <ID>` and use its returned path. Run `planctl ready <ID>` and claim eligible work before implementation. Do not implement blocked, planning, or claimed work.

## Save durable research before a plan

Research is a first-class artifact, not a plan status or temporary `findings/` directory. Save it only after an explicit request; never capture every conversation automatically, infer a missing project, cancel it on conversion, or delete it when a plan is created. Managed records live in the selected hub's `research/` directory and are indexed by the optional `RESEARCH.md` registry. They use hub-local `RES-###` IDs, scopes `project`, `cross-project`, or `unknown`, and lifecycle `open -> converted|cancelled -> archived`.

Resolve the hub with the same roots policy as plans. Ask for a project when `project` scope omits one; preserve explicit cross-project and unknown scope. Allocate, validate, and return the absolute path without creating a plan, claim, dependency, or readiness state:

```text
Save these findings as project-scoped research for `demo-project`, titled “Checkout flow investigation”. Do not create a plan yet.

Save these findings as cross-project research titled “Authentication options”. Do not create a plan yet.

Save this as unscoped research titled “API investigation”. Do not infer a project or create a plan.

Append these findings to research `<research-id>`; leave it open.

Create a plan from research `<research-id>` for `demo-project`.
```

Use `planctl research allocate`, `planctl research show`, `planctl research status`, and `planctl research link` for policy-aware operations. A plan created with `--from-research` or an explicit link records both sides of the relationship, changes open research to `converted`, and retains the research path and decision context. Linking is explicit; merely seeing a research file never converts it. Cross-project or unknown research may be converted for a selected project, while project-scoped research must match that project.

## Synchronization and safety

Before lookup or mutation, fetch and fast-forward the private hub's coordination branch. Validate and commit each completed storage change, then push without force. If a push is rejected, fetch and re-evaluate the plan's readiness, dependencies, and claim; do not blindly replay or force-push a claim.

`planctl locate` is a local read-only scan and may be stale: after resolving a hub, fetch and fast-forward the **resolved hub only** before any claim or mutation, exactly as above. Never fetch every hub on each lookup. If `show` or `claim` then fails because the plan moved or disappeared after the sync, re-run `planctl locate <ID>` to rediscover its current location.

Use `planctl init` to bootstrap a synthetic empty hub. Allocation, dependency, claim, release, and lifecycle changes must use `planctl`, followed by validation and a related commit. After creating a plan, include its absolute filesystem path in the result or final response so the user can open it directly; `planctl allocate` prints this absolute path. `planctl repair` is dry-run by default, applies only automatic-safe proposals when explicitly requested, and never commits or pushes; `repair --llm` is a review-only structured handoff. Preserve unrelated work. Before `done`, fold durable findings into the plan, remove the findings directory/link, release the claim, validate, and commit.
