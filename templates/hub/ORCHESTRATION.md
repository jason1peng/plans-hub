# Plan Orchestration

This file stores the prefix registry, dependency rows, claims, and retired plan IDs. Research is intentionally not stored here: it has a separate `RESEARCH.md` registry and `RES-###` namespace. The external plans client owns validation and maintenance policy; preserve existing state when using other tools.

## Project prefix registry

| Prefix | Project folder |
| --- | --- |
| `DEMO` | `demo-project` |

## Plan dependency graph

| ID | Project | Depends on | Claimed by | Claimed at |
| --- | --- | --- | --- | --- |

## Retired plan IDs

| ID | Project |
| --- | --- |

## Stable safety

Synchronize before changing shared state, preserve IDs, never force-push, and re-evaluate after a rejected push. Research links are explicit and do not add edges to the plan dependency graph.
