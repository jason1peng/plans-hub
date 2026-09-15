# plans-hub

Public tooling for maintaining implementation plans in a **separate private Git repository**. This repository contains the client, validator, installation skill, synthetic templates, and tests; it must not contain real plans or findings.

## Install

Clone this repository and install the skill:

```sh
git clone https://github.com/jason1peng/plans-hub.git
cd plans-hub
scripts/install-skill.sh              # Pi and Codex
# scripts/install-skill.sh --with-claude
PLANCTL="$HOME/.agents/skills/shared-plan-storage/bin/planctl"
"$PLANCTL" roots
```

The installer links the client skill but does not add `planctl` to `PATH`; invoke the installed wrapper as shown above or use `scripts/planctl.py` from this checkout. The installed skill locations (`~/.agents/skills/shared-plan-storage`, `~/.pi/agent/skills/shared-plan-storage`, or optional `~/.claude/skills/shared-plan-storage`) are client locations, not plan hubs, and are never used as `--root` values. The installer does not select or create private storage. When the roots registry below names exactly one existing hub, the installer also validates that hub and safely upgrades the legacy `shared-plan-storage` link from that hub's former `skills/` directory; with zero or multiple registered hubs it skips detection rather than guessing, and unrelated links and files are never replaced.

## Create or select a private hub

Create a new private repository checkout and initialize it from synthetic templates:

```sh
scripts/planctl.py --root ../private-hub init
```

Then register the hub in the roots registry at `~/.config/plans-hub/roots.json` (create the file if it is missing):

```json
{
  "schema_version": 1,
  "roots": [
    { "name": "private", "path": "/absolute/path/to/private-hub" }
  ]
}
```

With exactly one registered root, the CLI can resolve a single-hub command without a flag. Agents must retain the resolved absolute path as `HUB_ROOT` and pass it explicitly to every subsequent single-hub command:

```sh
HUB_ROOT=/absolute/path/to/private-hub  # path from `roots --json`
scripts/planctl.py --root "$HUB_ROOT" validate
```

`--root PATH|NAME` overrides the registry for one invocation. There is intentionally no fallback to the public client checkout, and no environment variable selects a hub: the registry is the only ambient root source. Keep the hub remote private and use credential helpers or SSH; do not put tokens in remote URLs or committed files.

## Multiple plan hubs

One client can serve several hubs (for example a team hub and a personal hub, each its own private Git repository). Register them by name in the roots registry, kept outside this repository so private paths never enter public history. The registry lives at the fixed path `~/.config/plans-hub/roots.json`:

```json
{
  "schema_version": 1,
  "roots": [
    { "name": "team",     "path": "/path/to/team-hub" },
    { "name": "personal", "path": "/path/to/personal-hub" }
  ]
}
```

Root names are unique lowercase kebab-case; paths are unique absolute paths resolved at load. A malformed registry (bad JSON, duplicates, missing fields, unknown `schema_version`) is a hard error, never silently ignored. Edit the file directly; there are no registry management subcommands.

Single-hub commands (`show`, `ready`, `allocate`, `claim`, `release`, `depends`, `status`, `validate`, `scan`, `repair`, `init`, `list-ready`) keep operating on exactly one hub, resolved by precedence:

1. `--root PATH|NAME` — an existing filesystem path wins; otherwise the value resolves as a registry root name (an unrecognized value stays a path, so `init` into a new directory keeps working);
2. a registry with exactly one root is used;
3. a multi-root registry never guesses: pass `--root NAME`;
4. with no registry or an empty registry, single-hub commands fail and ask for `--root PATH|NAME` or a roots registry.

Two read-only commands operate on the effective root *set* (`--root` if passed, else every registry root) and never require a single hub:

```sh
scripts/planctl.py roots              # name, path, source, and validity of every configured hub
scripts/planctl.py locate DEMO-001    # find which hub manages an ID: unique | not-found | ambiguous
```

Both support `--json` with `schema_version`, and `locate` exits 0 for every outcome — agents consume the `result` field, not exit codes.

Agent workflow policy lives in the installed skill: skill-discovery directories and the public client checkout are never storage roots. When saving a new plan with multiple valid roots and no named hub, the agent asks which hub to use; when a bare plan ID is referenced without a hub, `planctl locate` scans every configured hub, and only the resolved hub is fetched and fast-forwarded before any mutation. After `roots` or `locate` resolves a hub, the agent carries its absolute `path`/`hub` as `HUB_ROOT` and passes `--root "$HUB_ROOT"` to every subsequent single-hub `planctl` command. Plan ID prefixes are independent namespaces per hub — the same ID may legitimately exist in two hubs, in which case `locate` reports `ambiguous` and the user picks one.

## Plan lifecycle and delivery workflow

Approval, lifecycle, start eligibility, and ownership are separate steps. Keep their owners and effects distinct:

| Step | Owner | Command | Effect |
| --- | --- | --- | --- |
| Transition an approved plan | Plan author or approver | `planctl --root "$HUB_ROOT" status <ID> ready` | State-changing `planning` → `ready` transition. Run only after explicit approval; it does not run the start gate or claim the plan. |
| Check start eligibility | Worker | `planctl --root "$HUB_ROOT" ready <ID>` | **Read-only** start gate. Confirms the managed plan is ready, unclaimed, and has completed dependencies; it changes no files or claims. |
| Claim work | Worker | `planctl --root "$HUB_ROOT" claim <ID> <agent>` | Records ownership after the read-only gate; it is neither approval nor a lifecycle transition. Validate, commit, and synchronize this state change. |

Approval is a human or team decision; `planctl` does not infer approval from plan text, a successful sync, or a `ready` result. For a plan in `planning`, the approver must explicitly run `status ... ready` before a worker runs the read-only gate. A worker must pass `ready <ID>` and then claim before implementation. If the gate fails, stop and resolve its reported validation, dependency, or claim issue rather than bypassing it.

### Multi-root and approved-plan handoff

Use this path when a new plan may belong to one of several hubs, or when approval and implementation happen in different sessions. Run `planctl roots --json` to choose a valid root for a new plan; for an existing bare ID, run `planctl locate <ID> --json` and require a unique result. Root discovery is the only cross-root step; after choosing a hub, carry its absolute path as `HUB_ROOT` and keep the explicit root on every single-hub command.

For a new plan, allocate, write, validate, and publish the plan before asking for approval. `allocate` prints the stable ID and absolute path; keep those values, edit that returned file, and use the corresponding relative path when staging:

```sh
PLANCTL="$HOME/.agents/skills/shared-plan-storage/bin/planctl"
"$PLANCTL" roots --json
HUB_ROOT=/absolute/path/of-the-selected-root

git -C "$HUB_ROOT" fetch origin
git -C "$HUB_ROOT" merge --ff-only origin/main
"$PLANCTL" --root "$HUB_ROOT" allocate DEMO approved-plan
# Edit the absolute path printed by allocate, then set its values here.
PLAN_ID=DEMO-001
PLAN_PATH=demo-project/planning--DEMO-001--approved-plan.md
"$PLANCTL" --root "$HUB_ROOT" validate
git -C "$HUB_ROOT" add -- "$PLAN_PATH" ORCHESTRATION.md
git -C "$HUB_ROOT" commit -m 'plans(DEMO-001): add plan'
git -C "$HUB_ROOT" push origin main

# After explicit approval, the approver runs the lifecycle transition.
"$PLANCTL" --root "$HUB_ROOT" status "$PLAN_ID" ready
PLAN_PATH=demo-project/ready--DEMO-001--approved-plan.md
"$PLANCTL" --root "$HUB_ROOT" validate
git -C "$HUB_ROOT" add -- "$PLAN_PATH"
git -C "$HUB_ROOT" commit -m 'plans(DEMO-001): mark ready'
git -C "$HUB_ROOT" push origin main
```

For an existing bare ID, run the read-only cross-root lookup first, choose its unique hit, and use its returned hub/path instead of allocating. If `show` already reports `ready`, do not repeat the transition; continue with the worker gate and claim:

```sh
"$PLANCTL" locate DEMO-001 --json
HUB_ROOT=/absolute/path/from-the-unique-hit
"$PLANCTL" --root "$HUB_ROOT" show DEMO-001
# If status is planning, run this after explicit approval (skip it when already ready):
"$PLANCTL" --root "$HUB_ROOT" status DEMO-001 ready
PLAN_PATH=path/printed-by-status
"$PLANCTL" --root "$HUB_ROOT" validate
git -C "$HUB_ROOT" add -- "$PLAN_PATH"
git -C "$HUB_ROOT" commit -m 'plans(DEMO-001): mark ready'
git -C "$HUB_ROOT" push origin main
```

When the existing plan was already `ready`, skip the status-change commit and continue with the worker block below. When there is more than one valid root, ask which hub to use; never allocate or mutate an unselected root. The receiving worker synchronizes the resolved hub again, then performs the independent gate and claim:

```sh
git -C "$HUB_ROOT" fetch origin
git -C "$HUB_ROOT" merge --ff-only origin/main
"$PLANCTL" --root "$HUB_ROOT" ready DEMO-001       # read-only; stop if this fails
"$PLANCTL" --root "$HUB_ROOT" claim DEMO-001 worker-name
"$PLANCTL" --root "$HUB_ROOT" validate
git -C "$HUB_ROOT" add ORCHESTRATION.md
git -C "$HUB_ROOT" commit -m 'plans(DEMO-001): claim'
git -C "$HUB_ROOT" push origin main
```

Only the cross-session handoff is conditional. Synchronization, explicit approval, the read-only readiness gate, claim, validation, and non-force Git publication remain required. After claiming, implement from a fresh worktree created from the latest `main` of the implementation repository—not from the plan hub, public client checkout, or a planning branch—and follow the delivery state machine's implementation, verification, review, and close gates. A plan claim does not replace those delivery gates.

### Single-repository path without a worker handoff

When the same agent handles approval and implementation, omit `locate` and intercom, but do not omit lifecycle or safety steps. Resolve one hub, synchronize it, and use this order:

```sh
HUB_ROOT=/absolute/path/to/private-hub
PLANCTL="$HOME/.agents/skills/shared-plan-storage/bin/planctl"

git -C "$HUB_ROOT" fetch origin
git -C "$HUB_ROOT" merge --ff-only origin/main
"$PLANCTL" --root "$HUB_ROOT" show DEMO-001
# After explicit approval:
"$PLANCTL" --root "$HUB_ROOT" status DEMO-001 ready
# Refresh PLAN_PATH with the ready path printed by status before staging.
PLAN_PATH=path/printed-by-status
"$PLANCTL" --root "$HUB_ROOT" validate
git -C "$HUB_ROOT" add -- "$PLAN_PATH"
git -C "$HUB_ROOT" commit -m 'plans(DEMO-001): mark ready'
git -C "$HUB_ROOT" push origin main
# The same agent still runs the read-only gate before claiming.
"$PLANCTL" --root "$HUB_ROOT" ready DEMO-001
"$PLANCTL" --root "$HUB_ROOT" claim DEMO-001 agent-name
"$PLANCTL" --root "$HUB_ROOT" validate
git -C "$HUB_ROOT" add ORCHESTRATION.md
git -C "$HUB_ROOT" commit -m 'plans(DEMO-001): claim'
git -C "$HUB_ROOT" push origin main
```

Create the implementation worktree from the latest implementation-repository `main` and run the delivery state machine in that worktree. Intercom is unnecessary on this path; the readiness gate and claim are still mandatory.

### Reusable intercom handoff template

For a separate worker session, send a completed handoff as part of dispatch after approval has been recorded. Intercom is transport only, not a planctl operation: the recipient must verify the path, synchronize the selected hub, run the read-only readiness gate, and claim independently, and an intercom receipt is not completion evidence. Intercom reachability, a live session, and a GitLab/MR link are never prerequisites for planctl validation, readiness, or claim; if dispatch requires a handoff and intercom is unavailable, use the delivery coordinator's approved fallback rather than bypassing a gate. Carry only bounded handoff metadata and opaque artifact references, never credentials, cookies, raw logs, or private plan content.

Copy and complete this message without placing private plan content in public documentation:

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
- Work from a fresh worktree based on the latest implementation-repository main.
- Follow the delivery state machine and preserve approval, sync, validation, dependency, claim, and intercom boundaries.
Return artifacts:
- <artifact paths, commit/MR details when applicable, test evidence, and clean-worktree status>
```

## Datastore contract and scanning

The private repository is a passive Git-backed datastore; this client owns the managed-plan protocol. A managed plan:

- is directly inside one registered lowercase kebab-case project folder;
- uses `<status>--<ID>--<name>.md`, where status is `planning`, `ready`, `verifying`, or `done`;
- has matching, unique `ID:` and `Status:` metadata;
- uses a registered prefix and has one consistent row in `ORCHESTRATION.md`;
- has valid dependencies, claim fields, and findings lifecycle state.

Other Markdown deposited by any authoring tool is raw, unmanaged input. It remains in the datastore but is inactive: it cannot become ready, satisfy a dependency, receive a claim, or influence ID allocation. A raw file that mentions an active or duplicate ID is reported as ambiguous and blocks policy-aware operations until reviewed; unrelated raw input does not block managed work.

Scan without changing any file. After resolving a hub, carry its absolute path in `HUB_ROOT` and keep the explicit root on each command:

```sh
scripts/planctl.py --root "$HUB_ROOT" scan
scripts/planctl.py --root "$HUB_ROOT" scan --json
```

Structured output has `schema_version`, project, `managed_plans`, `managed_research`, unmanaged-file, diagnostic, and clean-state fields. Diagnostics cover filename/metadata disagreement, malformed or duplicate IDs, lifecycle mismatches, missing or stale orchestration rows, dependency targets and cycles, claims, findings links, and research registry/backlink state. Consumers must use fields rather than scrape human-readable prose.

## First-class research artifacts

A private hub can preserve investigation and requirements discussions before an implementation plan exists. Research is separate from plan lifecycle, readiness, claims, dependencies, and temporary `findings/` state. Managed records use hub-local `RES-###` IDs under `research/` and are indexed by `RESEARCH.md` when research is first allocated. A fresh `planctl init` includes the empty registry; older hubs without it remain valid until research is created.

Each record declares `Scope: project`, `Scope: cross-project`, or `Scope: unknown`, with project names recorded separately. Project scope requires an explicit registered project; cross-project and unknown scope may have no project. Records move `open` to `converted` or `cancelled`, then to `archived`. Conversion retains the original record and records every resulting plan on both sides. Research never enters the plan dependency graph, ready list, claim state, or plan ID allocator.

Use the explicit namespace commands (and return the absolute path printed by the client):

```sh
scripts/planctl.py --root private research allocate --scope project --project demo-project --title 'Checkout flow investigation'
scripts/planctl.py --root private research show <research-id>
scripts/planctl.py --root private research status <research-id> archived
scripts/planctl.py --root private research link <research-id> DEMO-001
scripts/planctl.py --root private allocate DEMO checkout --from-research <research-id>
```

The installed agent skill documents this prompt contract:

```text
Save these findings as project-scoped research for `demo-project`, titled “Checkout flow investigation”. Do not create a plan yet.

Save these findings as cross-project research titled “Authentication options”. Do not create a plan yet.

Save this as unscoped research titled “API investigation”. Do not infer a project or create a plan.

Append these findings to research `<research-id>`; leave it open.

Create a plan from research `<research-id>` for `demo-project`.
```

A missing scope is clarified rather than inferred. Saving research creates no plan or implementation state, and merely seeing a research file never converts it. Do not auto-cancel or delete durable research when conversion completes.

## Reviewable repair proposals

`repair` is dry-run by default. After resolving a hub, pass the carried `HUB_ROOT` explicitly. It classifies proposals as `automatic-safe`, `approval-required`, or `unsupported` and never commits or pushes:

```sh
scripts/planctl.py --root "$HUB_ROOT" repair --json
scripts/planctl.py --root "$HUB_ROOT" repair --apply --json   # automatic-safe proposals only
scripts/planctl.py --root "$HUB_ROOT" repair --llm --json     # provider-agnostic proposal handoff
```

Automatic repair is restricted to structural facts whose status and ID agree, such as normalizing only a plan-name slug. Registering any raw input in orchestration always requires explicit approval, and `--apply` never activates raw input. `--apply` refuses while any semantic or ambiguous diagnostic remains. The LLM handoff contains structured diagnostics and explicit constraints; it can propose a patch only. ID assignment, registration, lifecycle, dependencies, claims, deletion, conflict resolution, application, commits, and pushes always remain explicit host/user actions.

## Team synchronization workflow

The first release uses explicit, low-contention Git synchronization rather than claiming atomic distributed scheduling. This claim sequence assumes an approver has already run the explicit `status ... ready` transition; the worker still runs the read-only `ready` gate immediately before claiming:

```sh
PLANCTL="$HOME/.agents/skills/shared-plan-storage/bin/planctl"
HUB_ROOT=/path/to/private-hub                 # absolute path from roots/locate

git -C "$HUB_ROOT" fetch origin
git -C "$HUB_ROOT" merge --ff-only origin/main
"$PLANCTL" --root "$HUB_ROOT" list-ready
"$PLANCTL" --root "$HUB_ROOT" ready DEMO-001       # read-only start gate
"$PLANCTL" --root "$HUB_ROOT" claim DEMO-001 agent-name
"$PLANCTL" --root "$HUB_ROOT" validate
git -C "$HUB_ROOT" add ORCHESTRATION.md
git -C "$HUB_ROOT" commit -m 'plans(DEMO-001): claim'
git -C "$HUB_ROOT" push origin main       # never force
```

If the push is rejected because the remote advanced, do not try another fast-forward merge: the rejected local claim commit and remote branch have already diverged. Confirm the rejected claim commit is the unpublished `HEAD` and the worktree has no unrelated changes, then discard only that rejected commit and re-evaluate the remote state:

```sh
git -C "$HUB_ROOT" fetch origin
git -C "$HUB_ROOT" reset --keep origin/main
"$PLANCTL" --root "$HUB_ROOT" show DEMO-001
"$PLANCTL" --root "$HUB_ROOT" ready DEMO-001
```

Inspect the current claim and dependencies before deciding whether to make a new claim. If `reset --keep` refuses because of local changes, stop and preserve/reconcile them manually; do not use `--hard`, blindly replay the rejected claim, or force-push shared state. Concurrent allocation and claims across clones require manual coordination in this release.

## Development and release checks

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts -p 'test_*.py'
PYTHONPYCACHEPREFIX="$(mktemp -d)" python3 -m compileall -q scripts
python3 scripts/check_public_release.py --history
```

The release guard enforces an allowlisted tree and rejects plan-status files, findings, generated artifacts, credential-bearing URLs, every non-`DEMO` plan identifier, and caller-supplied private fragments across tracked, untracked, and ignored working-tree files, the index, reachable commit trees, and reachable commit metadata/messages. Normal project, branch, and worktree names require no global configuration; release builders can pass repeated `--forbid FRAGMENT` arguments for private names specific to their environment. Build releases only from fresh public history; never copy a private hub's `.git` directory or rewrite its history for publication.

Licensed under the MIT License.
