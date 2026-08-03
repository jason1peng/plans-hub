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

Before changing state, read the selected hub's `AGENTS.md`, `README.md`, and `ORCHESTRATION.md`. Use `planctl scan` for a read-only inventory; raw or malformed Markdown remains inactive until it satisfies the public client's managed-plan contract. Resolve an existing plan with `planctl show <ID>` and use its returned path. Run `planctl ready <ID>` and claim eligible work before implementation. Do not implement blocked, planning, or claimed work.

## Synchronization and safety

Before lookup or mutation, fetch and fast-forward the private hub's coordination branch. Validate and commit each completed storage change, then push without force. If a push is rejected, fetch and re-evaluate the plan's readiness, dependencies, and claim; do not blindly replay or force-push a claim.

`planctl locate` is a local read-only scan and may be stale: after resolving a hub, fetch and fast-forward the **resolved hub only** before any claim or mutation, exactly as above. Never fetch every hub on each lookup. If `show` or `claim` then fails because the plan moved or disappeared after the sync, re-run `planctl locate <ID>` to rediscover its current location.

Use `planctl init` to bootstrap a synthetic empty hub. Allocation, dependency, claim, release, and lifecycle changes must use `planctl`, followed by validation and a related commit. After creating a plan, include its absolute filesystem path in the result or final response so the user can open it directly; `planctl allocate` prints this absolute path. `planctl repair` is dry-run by default, applies only automatic-safe proposals when explicitly requested, and never commits or pushes; `repair --llm` is a review-only structured handoff. Preserve unrelated work. Before `done`, fold durable findings into the plan, remove the findings directory/link, release the claim, validate, and commit.
