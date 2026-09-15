---
name: shared-plan-storage
description: Resolve and maintain implementation plans by stable ID in separately selected private plan hubs.
---

# Shared Plan Storage

## Skill location is not storage

The public client checkout and the installed skill contain software and instructions only. Pi/Codex/Claude discovery locations such as `~/.agents/skills/shared-plan-storage`, `~/.pi/agent/skills/shared-plan-storage`, `~/.claude/skills/shared-plan-storage`, and this checkout's `skills/shared-plan-storage/` are **not** plan hubs. Never pass one of those directories (or its parent `skills/` directory) to `--root`, use it as a `git -C` target, or inspect it for plans. Do not infer storage from the current directory, client checkout, skill link, or installation location.

A plan hub can only be selected by an explicit `--root PATH|NAME` or by an entry in the fixed roots registry at `~/.config/plans-hub/roots.json`. There is no fallback to the public client checkout and no environment variable selects a hub. `planctl roots` reports configured roots; use its `path` values, not skill-discovery paths, as storage locations.

## Select and carry the hub

Resolve the hub once, retain its absolute path as `HUB_ROOT`, and pass `--root "$HUB_ROOT"` to **every subsequent single-hub `planctl` command**. Do not drop the flag because a registry currently has one root: a bare command can select a different hub later or fail when multiple roots are configured. The initial root-set commands `planctl roots` and `planctl locate <ID>` are the only discovery exceptions.

- **Explicit hub mention wins.** If the user names a hub by path, set `HUB_ROOT` to that path. If they name a configured root by name, run `planctl roots --json`, select the matching valid entry's absolute `path`, and set `HUB_ROOT` to that value. Proceed without a prompt or cross-root scan.
- **Saving a new plan:** run `planctl roots --json`. If there is exactly one valid root and the user did not name a hub, set `HUB_ROOT` to that root's absolute `path`. If more than one valid root exists, ask which root to use before allocating; never default silently. With no valid root, require an explicit path or a corrected registry.
- **Resolving an existing bare ID:** run `planctl locate <ID> --json` to scan every configured hub. On `unique`, set `HUB_ROOT` to the hit's `hub` absolute path. On `ambiguous`, ask the user which hub. On `not-found`, report the hubs searched. Never fetch every hub just because lookup was cross-root.

After `HUB_ROOT` is set, use the same value for all planctl operations, for example:

```sh
HUB_ROOT=/absolute/path/to/private-hub
planctl --root "$HUB_ROOT" scan
planctl --root "$HUB_ROOT" show <ID>
planctl --root "$HUB_ROOT" ready <ID>
planctl --root "$HUB_ROOT" claim <ID> agent-name
planctl --root "$HUB_ROOT" validate
```

Before changing state, read `$HUB_ROOT/AGENTS.md`, `$HUB_ROOT/README.md`, and `$HUB_ROOT/ORCHESTRATION.md`; read `$HUB_ROOT/RESEARCH.md` when present before a research mutation. Use `planctl --root "$HUB_ROOT" scan` for a read-only inventory; raw or malformed Markdown remains inactive until it satisfies the public client's managed-plan contract. Resolve an existing plan with `planctl --root "$HUB_ROOT" show <ID>` and use its returned path.

## Plan lifecycle and delivery workflow

Approval, lifecycle, start eligibility, and ownership are distinct:

- **Transition after approval (plan author/approver):** `planctl --root "$HUB_ROOT" status <ID> ready` is the state-changing `planning` → `ready` transition. Run it only after explicit human or team approval. It does not check start eligibility or claim the plan.
- **Readiness gate (worker):** `planctl --root "$HUB_ROOT" ready <ID>` is read-only. It verifies managed `ready` state, an empty claim, and completed dependencies (along with normal validation), and changes no files or claims. If the plan is already `ready`, skip the planning-to-ready transition and start with this gate.
- **Claim (worker):** `planctl --root "$HUB_ROOT" claim <ID> <agent>` records ownership only after the readiness gate. It is neither approval nor a lifecycle transition; validate, commit, and synchronize the claim.

Do not infer approval from plan text, synchronization, `show`, or a successful readiness check. For a planning plan, the approver records the transition first; a worker then runs the read-only gate and claims before implementation. A failed gate is a stop condition, not permission to bypass dependencies, validation, or an existing claim.

For a new plan, the selected-root owner allocates it, writes the plan at the absolute path printed by `allocate`, validates it, and commits/pushes the plan file plus its orchestration row before requesting approval. After approval, the lifecycle transition and claim are separate commits. Preserve the returned ID and path across sessions.

### Multi-root and approved-plan path

For a new plan with multiple valid roots, run `planctl roots --json`, ask which hub to use, and set `HUB_ROOT` to the selected absolute `path`; never allocate into an unselected root. For an existing bare ID, run `planctl locate <ID> --json`, require a unique result, and carry that result's absolute `hub`. Fetch and fast-forward only the resolved hub before any lookup or mutation:

```sh
planctl roots --json
planctl locate <ID> --json                 # existing bare ID only
HUB_ROOT=/absolute/path/from-roots-or-locate

git -C "$HUB_ROOT" fetch origin
git -C "$HUB_ROOT" merge --ff-only origin/main
# For a new plan, allocate only after selecting and syncing this root; retain its ID/path.
# planctl --root "$HUB_ROOT" allocate DEMO approved-plan
# For an existing plan, use the unique locate result and its returned path.
planctl --root "$HUB_ROOT" show <ID>
# After explicit approval, the approver runs:
planctl --root "$HUB_ROOT" status <ID> ready
planctl --root "$HUB_ROOT" validate
# Commit and push only the intended plan-status change, without force.
```

The receiving worker synchronizes the same `HUB_ROOT`, re-checks the independent gate, and records the claim:

```sh
git -C "$HUB_ROOT" fetch origin
git -C "$HUB_ROOT" merge --ff-only origin/main
planctl --root "$HUB_ROOT" ready <ID>       # read-only; stop if this fails
planctl --root "$HUB_ROOT" claim <ID> <agent>
planctl --root "$HUB_ROOT" validate
# Commit and push only ORCHESTRATION.md, without force.
```

### Single-repository path without a worker handoff

If one agent handles approval and implementation, omit cross-root lookup and intercom only. Resolve one hub, synchronize it, run `status <ID> ready` after approval, validate and publish that change, then run the read-only `ready <ID>` gate and claim. The same sync, validation, dependency, claim, fresh-worktree, and delivery-state-machine requirements still apply.

```sh
HUB_ROOT=/absolute/path/to/private-hub
git -C "$HUB_ROOT" fetch origin
git -C "$HUB_ROOT" merge --ff-only origin/main
planctl --root "$HUB_ROOT" show <ID>
planctl --root "$HUB_ROOT" status <ID> ready
planctl --root "$HUB_ROOT" validate
# Commit and push the status change, without force.
planctl --root "$HUB_ROOT" ready <ID>
planctl --root "$HUB_ROOT" claim <ID> <agent>
planctl --root "$HUB_ROOT" validate
# Commit and push the claim change, without force.
```

Implement from a fresh worktree based on the latest `main` of the implementation repository, not from the plan hub, public client checkout, or planning branch. Follow the delivery state machine's implementation, verification, review, and close gates; a plan claim does not replace those gates.

### Reusable intercom handoff template

For a separate worker session, send a completed handoff as part of dispatch after approval is recorded. Intercom is transport only, not a lifecycle operation: the recipient must read the plan at its returned path, synchronize, run `ready <ID>`, and claim independently, and an intercom receipt is not completion evidence. Intercom reachability, a live session, and GitLab/MR reporting are never prerequisites for planctl validation, readiness, or claim; if dispatch requires a handoff and intercom is unavailable, use the delivery coordinator's approved fallback rather than bypassing a gate. Carry only bounded handoff metadata and opaque artifact references, never credentials, cookies, raw logs, or private plan content.

```text
Approved plan handoff
Handoff version: 1
Plan: <ID>
Path: <absolute plan path returned by planctl show>
Owner: <receiving session/agent name; use the same value for claim>
Phase: <delivery phase, e.g. IMPLEMENT>
Scope:
- <in-scope behavior, files, or boundaries>
Non-goals:
- <explicitly excluded behavior or files>
Acceptance evidence:
- <tests, checks, or observable evidence required>
Delivery requirements:
- Use a fresh worktree based on the latest implementation-repository main.
- Follow the delivery state machine and preserve approval, sync, validation, dependency, claim, and intercom boundaries.
Return artifacts:
- <artifact paths, commit/MR details when applicable, test evidence, and clean-worktree status>
```

Do not implement blocked or planning work, or plans claimed by another agent. After your own successful readiness gate and claim, implementation may begin.

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

Use `planctl --root "$HUB_ROOT" research allocate`, `planctl --root "$HUB_ROOT" research show`, `planctl --root "$HUB_ROOT" research status`, and `planctl --root "$HUB_ROOT" research link` for policy-aware operations. A plan created with `--from-research` or an explicit link records both sides of the relationship, changes open research to `converted`, and retains the research path and decision context. Linking is explicit; merely seeing a research file never converts it. Cross-project or unknown research may be converted for a selected project, while project-scoped research must match that project.

## Synchronization and safety

Before lookup or mutation, fetch and fast-forward the private hub's coordination branch. Validate and commit each completed storage change, then push without force. If a push is rejected, fetch and re-evaluate the plan's readiness, dependencies, and claim; do not blindly replay or force-push a claim.

`planctl locate` is a local read-only scan and may be stale: after resolving a hub, fetch and fast-forward the **resolved hub only** before any claim or mutation, exactly as above. Use `git -C "$HUB_ROOT"` for those Git operations. Never fetch every hub on each lookup. If `show` or `claim` then fails because the plan moved or disappeared after the sync, re-run `planctl locate <ID>` to rediscover its current location, then replace `HUB_ROOT` with the newly resolved `hub` path.

Use `planctl --root "$HUB_ROOT" init` to bootstrap a synthetic empty hub when its path is intentionally new. Allocation, dependency, claim, release, and lifecycle changes must use `planctl --root "$HUB_ROOT"`, followed by validation and a related commit. After creating a plan, include its absolute filesystem path in the result or final response so the user can open it directly; `planctl --root "$HUB_ROOT" allocate` prints this absolute path. `planctl --root "$HUB_ROOT" repair` is dry-run by default, applies only automatic-safe proposals when explicitly requested, and never commits or pushes; `repair --llm` is a review-only structured handoff. Preserve unrelated work. Before `done`, fold durable findings into the plan, remove the findings directory/link, release the claim, validate, and commit.
