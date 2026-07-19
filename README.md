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

If the push is rejected, fetch and fast-forward again, then re-run `show`/`ready` and inspect the current claim and dependencies. Do not blindly replay the rejected claim and never force-push shared state. Concurrent allocation and claims across clones require manual coordination in this release.

## Development and release checks

```sh
python3 -m unittest discover -s scripts -p 'test_*.py'
python3 -m compileall -q scripts
python3 scripts/check_public_release.py --history
```

The release guard enforces an allowlisted tree and rejects plan-status files, findings, generated artifacts, credential-bearing URLs, real project prefixes, every non-`DEMO` plan identifier, unapproved project-like kebab-case markers, and caller-supplied private fragments across the working tree, index, and every reachable commit. Build releases only from fresh public history; never copy a private hub's `.git` directory or rewrite its history for publication.

Licensed under the MIT License.
