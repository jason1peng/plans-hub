#!/usr/bin/env python3
"""Fail when a public release contains private hub state or unexpected files."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ALLOWED_TOP_LEVEL = {
    ".github",
    ".gitignore",
    "LICENSE",
    "README.md",
    "pyproject.toml",
    "scripts",
    "skills",
    "templates",
}
PRIVATE_PATH = re.compile(
    r"(^|/)(?:findings)(?:/|$)|(^|/)(?:planning|ready|verifying|done)--[A-Z][A-Z0-9]*-[0-9]{3,}--"
)
GENERATED_PATH = re.compile(r"(^|/)(?:\.pi-subagents|__pycache__)(?:/|$)|\.py[co]$")
CREDENTIAL_URL = re.compile(r"https?://[^/\s:@]+:[^/\s@]+@")
PLAN_IDENTIFIER = re.compile(r"(?<![A-Z0-9])([A-Z][A-Z0-9]*)-[0-9]{3,}(?![A-Z0-9])")
SYNTHETIC_PLAN_PREFIXES = {"DEMO"}
KEBAB_MARKER = re.compile(r"(?<![a-z0-9])([a-z0-9]+(?:-[a-z0-9]+)+)(?![a-z0-9])")
ALLOWED_KEBAB_MARKERS = {
    "0-9", "a-z0-9", "after-retired", "agent-name", "caller-supplied", "ci-plan",
    "completed-agent", "completed-dependency", "credential-bearing", "cycle-peer", "demo-001",
    "demo-project", "dependency-agent", "downstream-agent", "fast-forward", "fetch-depth", "ff-only",
    "first-plan", "force-push", "git-common-dir", "initialized-hub", "install-skill", "kebab-case",
    "line-length", "list-ready", "low-contention", "ls-files", "ls-tree", "name-only", "non-empty",
    "non-synthetic", "pi-subagents", "plan-hub", "plan-state", "plan-status", "plans-hub",
    "post-migration", "private-hub", "project-like", "python-version", "re-evaluate", "re-run", "rev-list", "rev-parse",
    "runs-on", "sample-plan", "setup-python", "shared-plan", "shared-plan-storage", "test-agent",
    "top-level", "ubuntu-latest", "unknown-skill", "upstream-agent", "utf-8", "with-claude",
}


def tracked_files(root: Path, revision: str | None = None) -> list[str]:
    command = ["git", "-C", str(root)]
    command += ["ls-tree", "-r", "--name-only", revision] if revision else ["ls-files"]
    result = subprocess.run(command, text=True, capture_output=True, check=True)
    return [line for line in result.stdout.splitlines() if line]


def content_at(root: Path, path: str, revision: str | None) -> bytes:
    object_name = f"{revision}:{path}" if revision else f":{path}"
    result = subprocess.run(
        ["git", "-C", str(root), "show", object_name],
        capture_output=True,
        check=True,
    )
    return result.stdout


def revisions(root: Path, include_history: bool) -> list[str | None]:
    if not include_history:
        return [None]
    result = subprocess.run(
        ["git", "-C", str(root), "rev-list", "--all"],
        text=True,
        capture_output=True,
        check=True,
    )
    return [None, *result.stdout.splitlines()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--history", action="store_true", help="scan every reachable commit as well as the index")
    parser.add_argument("--forbid", action="append", default=[], help="literal private content fragment to reject")
    args = parser.parse_args()
    root = args.root.resolve()
    errors: list[str] = []

    for revision in revisions(root, args.history):
        label = revision or "index"
        for path in tracked_files(root, revision):
            top = path.split("/", 1)[0]
            if top not in ALLOWED_TOP_LEVEL:
                errors.append(f"{label}: unexpected top-level path: {path}")
            if PRIVATE_PATH.search(path):
                errors.append(f"{label}: private plan-state path: {path}")
            if GENERATED_PATH.search(path):
                errors.append(f"{label}: generated path: {path}")
            data = content_at(root, path, revision)
            text = data.decode("utf-8", errors="ignore")
            if CREDENTIAL_URL.search(text):
                errors.append(f"{label}: credential-bearing URL in {path}")
            for match in PLAN_IDENTIFIER.finditer(text):
                if match.group(1) not in SYNTHETIC_PLAN_PREFIXES:
                    errors.append(f"{label}: non-synthetic plan identifier in {path}: {match.group(0)!r}")
            for match in KEBAB_MARKER.finditer(text):
                if match.group(1) not in ALLOWED_KEBAB_MARKERS:
                    errors.append(f"{label}: unapproved project-like marker in {path}: {match.group(0)!r}")
            for fragment in args.forbid:
                if fragment and fragment in text:
                    errors.append(f"{label}: forbidden private fragment in {path}: {fragment!r}")

    if errors:
        print("Public release guard failed:", file=sys.stderr)
        for error in sorted(set(errors)):
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Public release guard passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
