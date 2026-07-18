# plans-hub

Public tooling for maintaining implementation plans in a **separate private Git repository**. This repository contains the client, validator, installation skill, synthetic templates, and tests; it must not contain real plans or findings.

## Install

Clone this repository, then install the skill:

```sh
git clone https://github.com/jason1peng/plans-hub.git
cd plans-hub
scripts/install-skill.sh              # Pi and Codex
scripts/install-skill.sh --with-claude
```

The installer links the client skill. It does not select or create private storage.

## Create or select a private hub

Create a new private repository checkout and initialize it from synthetic templates:

```sh
scripts/planctl.py --root ../private-hub init
export PLANS_ROOT="$PWD/../private-hub"
scripts/planctl.py validate
```

`--root PATH` overrides `PLANS_ROOT`. There is intentionally no fallback to the public client checkout. Keep the hub remote private and use credential helpers or SSH; do not put tokens in remote URLs or committed files.

## Team synchronization workflow

The first release uses explicit, low-contention Git synchronization rather than claiming atomic distributed scheduling:

```sh
git -C "$PLANS_ROOT" fetch origin
git -C "$PLANS_ROOT" merge --ff-only origin/main
scripts/planctl.py list-ready
scripts/planctl.py claim DEMO-001 agent-name
scripts/planctl.py validate
git -C "$PLANS_ROOT" add ORCHESTRATION.md
git -C "$PLANS_ROOT" commit -m 'plans(DEMO-001): claim'
git -C "$PLANS_ROOT" push origin main       # never force
```

If the push is rejected, fetch and fast-forward again, then re-run `show`/`ready` and inspect the current claim and dependencies. Do not blindly replay the rejected claim and never force-push shared state. Concurrent allocation and claims across clones require manual coordination in this release.

## Development and release checks

```sh
python3 -m unittest discover -s scripts -p 'test_*.py'
python3 -m compileall -q scripts
python3 scripts/check_public_release.py --history
```

The release guard enforces an allowlisted tree and rejects plan-status files, findings, generated artifacts, credential-bearing URLs, and caller-supplied private fragments across the index and every reachable commit. Build releases only from fresh public history; never copy a private hub's `.git` directory or rewrite its history for publication.

Licensed under the MIT License.
