---
name: shared-plan-storage
description: Resolve and maintain implementation plans by stable ID in a separately selected private plan hub.
---

# Shared Plan Storage

## Select the hub

The public client checkout contains software only. Select the private plan-hub checkout explicitly with `--root PATH` or `PLANS_ROOT`; `--root` takes precedence. Never infer plan storage from the installed skill or client location.

Before changing state, read the selected hub's `AGENTS.md`, `README.md`, and `ORCHESTRATION.md`. Use `planctl scan` for a read-only inventory; raw or malformed Markdown remains inactive until it satisfies the public client's managed-plan contract. Resolve an existing plan with `planctl show <ID>` and use its returned path. Run `planctl ready <ID>` and claim eligible work before implementation. Do not implement blocked, planning, or claimed work.

## Synchronization and safety

Before lookup or mutation, fetch and fast-forward the private hub's coordination branch. Validate and commit each completed storage change, then push without force. If a push is rejected, fetch and re-evaluate the plan's readiness, dependencies, and claim; do not blindly replay or force-push a claim.

Use `planctl init` to bootstrap a synthetic empty hub. Allocation, dependency, claim, release, and lifecycle changes must use `planctl`, followed by validation and a related commit. `planctl repair` is dry-run by default, applies only automatic-safe proposals when explicitly requested, and never commits or pushes; `repair --llm` is a review-only structured handoff. Preserve unrelated work. Before `done`, fold durable findings into the plan, remove the findings directory/link, release the claim, validate, and commit.
