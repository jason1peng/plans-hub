# plans-hub

Public tooling for maintaining implementation plans in a **separate private Git repository**. This repository contains the client, validator, installation skill, synthetic templates, and tests; it must not contain real plans or findings.

## Install

Clone this repository, select an existing private hub before installation, then install the skill:

```sh
export PLANS_ROOT=/path/to/private-hub # set first when upgrading an existing installation
git clone https://github.com/jason1peng/plans-hub.git
cd plans-hub
scripts/install-skill.sh              # Pi and Codex
# scripts/install-skill.sh --with-claude
PLANCTL="$HOME/.agents/skills/shared-plan-storage/bin/planctl"
"$PLANCTL" validate
```

The installer links the client skill but does not add `planctl` to `PATH`; invoke the installed wrapper as shown above or use `scripts/planctl.py` from this checkout. It does not select or create private storage. When `PLANS_ROOT` selects an existing hub before installation, the installer safely upgrades the legacy `shared-plan-storage` link from that hub's former `skills/` directory; unrelated links and files are never replaced. For a new hub that does not exist yet, install without `PLANS_ROOT`, initialize it below, then export `PLANS_ROOT`.

## Create or select a private hub

Create a new private repository checkout and initialize it from synthetic templates:

```sh
scripts/planctl.py --root ../private-hub init
export PLANS_ROOT="$PWD/../private-hub"
scripts/planctl.py validate
```

`--root PATH` overrides `PLANS_ROOT`. There is intentionally no fallback to the public client checkout. Keep the hub remote private and use credential helpers or SSH; do not put tokens in remote URLs or committed files.

## Datastore contract and scanning

The private repository is a passive Git-backed datastore; this client owns the managed-plan protocol. A managed plan:

- is directly inside one registered lowercase kebab-case project folder;
- uses `<status>--<ID>--<name>.md`, where status is `planning`, `ready`, `verifying`, or `done`;
- has matching, unique `ID:` and `Status:` metadata;
- uses a registered prefix and has one consistent row in `ORCHESTRATION.md`;
- has valid dependencies, claim fields, and findings lifecycle state.

Other Markdown deposited by any authoring tool is raw, unmanaged input. It remains in the datastore but is inactive: it cannot become ready, satisfy a dependency, receive a claim, or influence ID allocation. A raw file that mentions an active or duplicate ID is reported as ambiguous and blocks policy-aware operations until reviewed; unrelated raw input does not block managed work.

Scan without changing any file:

```sh
scripts/planctl.py --root "$PLANS_ROOT" scan
scripts/planctl.py --root "$PLANS_ROOT" scan --json
```

Structured output has `schema_version`, project, managed-plan, unmanaged-file, diagnostic, and clean-state fields. Diagnostics cover filename/metadata disagreement, malformed or duplicate IDs, lifecycle mismatches, missing or stale orchestration rows, dependency targets and cycles, claims, and findings links. Consumers must use fields rather than scrape human-readable prose.

## Reviewable repair proposals

`repair` is dry-run by default. It classifies proposals as `automatic-safe`, `approval-required`, or `unsupported` and never commits or pushes:

```sh
scripts/planctl.py --root "$PLANS_ROOT" repair --json
scripts/planctl.py --root "$PLANS_ROOT" repair --apply --json  # automatic-safe proposals only
scripts/planctl.py --root "$PLANS_ROOT" repair --llm --json    # provider-agnostic proposal handoff
```

Automatic repair is restricted to structural facts whose status and ID agree, such as normalizing only a plan-name slug. Registering any raw input in orchestration always requires explicit approval, and `--apply` never activates raw input. `--apply` refuses while any semantic or ambiguous diagnostic remains. The LLM handoff contains structured diagnostics and explicit constraints; it can propose a patch only. ID assignment, registration, lifecycle, dependencies, claims, deletion, conflict resolution, application, commits, and pushes always remain explicit host/user actions.

## Team synchronization workflow

The first release uses explicit, low-contention Git synchronization rather than claiming atomic distributed scheduling:

```sh
git -C "$PLANS_ROOT" fetch origin
git -C "$PLANS_ROOT" merge --ff-only origin/main
scripts/planctl.py --root "$PLANS_ROOT" list-ready
scripts/planctl.py --root "$PLANS_ROOT" claim DEMO-001 agent-name
scripts/planctl.py --root "$PLANS_ROOT" validate
git -C "$PLANS_ROOT" add ORCHESTRATION.md
git -C "$PLANS_ROOT" commit -m 'plans(DEMO-001): claim'
git -C "$PLANS_ROOT" push origin main       # never force
```

If the push is rejected because the remote advanced, do not try another fast-forward merge: the rejected local claim commit and remote branch have already diverged. Confirm the rejected claim commit is the unpublished `HEAD` and the worktree has no unrelated changes, then discard only that rejected commit and re-evaluate the remote state:

```sh
git -C "$PLANS_ROOT" fetch origin
git -C "$PLANS_ROOT" reset --keep origin/main
scripts/planctl.py --root "$PLANS_ROOT" show DEMO-001
scripts/planctl.py --root "$PLANS_ROOT" ready DEMO-001
```

Inspect the current claim and dependencies before deciding whether to make a new claim. If `reset --keep` refuses because of local changes, stop and preserve/reconcile them manually; do not use `--hard`, blindly replay the rejected claim, or force-push shared state. Concurrent allocation and claims across clones require manual coordination in this release.

## Development and release checks

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts -p 'test_*.py'
PYTHONPYCACHEPREFIX="$(mktemp -d)" python3 -m compileall -q scripts
python3 scripts/check_public_release.py --history
```

The release guard enforces an allowlisted tree and rejects plan-status files, findings, generated artifacts, credential-bearing URLs, real project prefixes, every non-`DEMO` plan identifier, unapproved project-like kebab-case markers, and caller-supplied private fragments across tracked, untracked, and ignored working-tree files, the index, reachable commit trees, and reachable commit metadata/messages. Build releases only from fresh public history; never copy a private hub's `.git` directory or rewrite its history for publication.

Licensed under the MIT License.
