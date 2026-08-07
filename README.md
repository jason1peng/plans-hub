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

Structured output has `schema_version`, project, managed-plan, unmanaged-file, diagnostic, and clean-state fields. Diagnostics cover filename/metadata disagreement, malformed or duplicate IDs, lifecycle mismatches, missing or stale orchestration rows, dependency targets and cycles, claims, and findings links. Consumers must use fields rather than scrape human-readable prose.

## Reviewable repair proposals

`repair` is dry-run by default. After resolving a hub, pass the carried `HUB_ROOT` explicitly. It classifies proposals as `automatic-safe`, `approval-required`, or `unsupported` and never commits or pushes:

```sh
scripts/planctl.py --root "$HUB_ROOT" repair --json
scripts/planctl.py --root "$HUB_ROOT" repair --apply --json   # automatic-safe proposals only
scripts/planctl.py --root "$HUB_ROOT" repair --llm --json     # provider-agnostic proposal handoff
```

Automatic repair is restricted to structural facts whose status and ID agree, such as normalizing only a plan-name slug. Registering any raw input in orchestration always requires explicit approval, and `--apply` never activates raw input. `--apply` refuses while any semantic or ambiguous diagnostic remains. The LLM handoff contains structured diagnostics and explicit constraints; it can propose a patch only. ID assignment, registration, lifecycle, dependencies, claims, deletion, conflict resolution, application, commits, and pushes always remain explicit host/user actions.

## Team synchronization workflow

The first release uses explicit, low-contention Git synchronization rather than claiming atomic distributed scheduling:

```sh
PLANCTL="$HOME/.agents/skills/shared-plan-storage/bin/planctl"
HUB_ROOT=/path/to/private-hub                 # absolute path from roots/locate

git -C "$HUB_ROOT" fetch origin
git -C "$HUB_ROOT" merge --ff-only origin/main
"$PLANCTL" --root "$HUB_ROOT" list-ready
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
