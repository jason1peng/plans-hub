# Agent Instructions

Treat this repository as company-internal plan data, not policy implementation. Read `README.md` and `ORCHESTRATION.md` (and `RESEARCH.md` when present), synchronize with the private remote before reading or writing shared state, and preserve existing stable IDs and unrelated files.

A compatible external client may scan or maintain managed plans. When using `plans-hub`, select this checkout explicitly, resolve IDs through the client, validate each state change, and commit only related files. Whenever creating a plan or research record, include its absolute filesystem path in the result or final response so it can be opened directly. Never force-push a claim; after a rejected push, fetch and re-evaluate current state rather than replaying blindly.

## Plan lifecycle and delivery handoff

Approval, lifecycle, start eligibility, and ownership are separate:

- The plan author or approver runs `planctl --root "$HUB_ROOT" status <ID> ready` only after explicit approval. This is the state-changing `planning` → `ready` transition; it does not run the readiness gate or claim the plan.
- The worker runs `planctl --root "$HUB_ROOT" ready <ID>` immediately before implementation. This is a **read-only** gate that checks managed ready state, dependencies, and an empty claim; it changes no files or claims. If the plan is already `ready`, skip the planning-to-ready transition and start with this gate.
- The worker then runs `planctl --root "$HUB_ROOT" claim <ID> <agent>` to record ownership. Claim is not approval and does not change lifecycle status. Validate, commit, and synchronize it.

For a new plan, allocate it in the selected hub, write it at the absolute path printed by `allocate`, validate it, and commit/push the plan file plus orchestration row before requesting approval. After approval, keep the status transition and claim as separate validated state changes.

For multiple roots, run `planctl roots --json` (or `planctl locate <ID> --json` for a bare existing ID), ask which valid hub to use when necessary, and carry the selected absolute path as `HUB_ROOT`. For a new plan, allocate only after selecting and synchronizing the chosen root; for an existing plan, use the unique locate result and its returned path. Synchronize that hub before lookup or mutation:

```sh
planctl roots --json
# For an existing bare ID, resolve a unique hit instead:
# planctl locate <ID> --json
HUB_ROOT=/absolute/path/from-roots-or-locate
git -C "$HUB_ROOT" fetch origin
git -C "$HUB_ROOT" merge --ff-only origin/main
# New-plan alternative: retain the ID/path printed by allocation.
# planctl --root "$HUB_ROOT" allocate DEMO approved-plan
# Existing-plan path:
planctl --root "$HUB_ROOT" show <ID>
# After approval, approver:
planctl --root "$HUB_ROOT" status <ID> ready
planctl --root "$HUB_ROOT" validate
# Commit and push only the intended status change, without force.
# Worker, after synchronizing again:
planctl --root "$HUB_ROOT" ready <ID>
planctl --root "$HUB_ROOT" claim <ID> <agent>
planctl --root "$HUB_ROOT" validate
# Commit and push only ORCHESTRATION.md, without force.
```

If the same agent handles approval and implementation in one repository, omit only the cross-session lookup and handoff; do not omit synchronization, the explicit transition, the read-only gate, claim, validation, or publication. Implement from a fresh worktree based on the latest implementation-repository `main` and follow the delivery state machine's implementation, verification, review, and close gates. A plan claim does not replace those delivery gates.

For a separate worker session, send a completed handoff as part of dispatch after approval is recorded. Intercom is transport only, not a planctl operation: the recipient must independently verify the plan path, synchronize, run the read-only gate, and claim; an intercom receipt is not completion evidence. Intercom reachability, a live session, or an MR link is never a prerequisite for planctl validation, readiness, or claim. Carry only bounded handoff metadata and opaque artifact references, never credentials, cookies, raw logs, or private plan content.

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

Research is durable decision context in `research/`, indexed by `RESEARCH.md`, and is separate from plan lifecycle and temporary findings. Save it only after an explicit request with an explicit `project`, `cross-project`, or `unknown` scope. A missing project for project scope must be clarified, not inferred. Explicit linking or plan creation with a research source records both backlinks and retains the research; do not auto-cancel or delete it.

Raw files from other tools may remain in the datastore as inactive input. Do not silently assign IDs, change lifecycle, dependencies, or claims, delete content, or commit repair proposals without explicit review.
