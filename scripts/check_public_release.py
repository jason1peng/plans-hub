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
    r"(^|/)(?:findings|research)(?:/|$)|(^|/)(?:planning|ready|verifying|done)--[A-Z][A-Z0-9]*-[0-9]{3,}--"
    r"|(^|/)(?:open|converted|cancelled|archived)--RES-[0-9]{3,}--"
)
RESEARCH_REGISTRY_PATH = re.compile(r"(^|/)RESEARCH\.md$")
PUBLIC_RESEARCH_TEMPLATE = "templates/hub/RESEARCH.md"
GENERATED_PATH = re.compile(r"(^|/)(?:\.pi-subagents|__pycache__)(?:/|$)|\.py[co]$")
CREDENTIAL_URL = re.compile(r"https?://[^/\s:@]+:[^/\s@]+@")
PLAN_IDENTIFIER = re.compile(r"(?<![A-Z0-9])([A-Z][A-Z0-9]*)-[0-9]{3,}(?![A-Z0-9])")
SYNTHETIC_PLAN_PREFIXES = {"DEMO"}


def snapshot_files(root: Path, revision: str | None = None) -> list[str]:
    if revision == "working tree":
        return sorted(
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() and ".git" not in path.relative_to(root).parts
        )
    command = ["git", "-C", str(root)]
    command += ["ls-tree", "-r", "--name-only", revision] if revision else ["ls-files"]
    result = subprocess.run(command, text=True, capture_output=True, check=True)
    return [line for line in result.stdout.splitlines() if line]


def content_at(root: Path, path: str, revision: str | None) -> bytes:
    if revision == "working tree":
        worktree_path = root / path
        return worktree_path.read_bytes() if worktree_path.is_file() else b""
    object_name = f"{revision}:{path}" if revision else f":{path}"
    result = subprocess.run(
        ["git", "-C", str(root), "show", object_name],
        capture_output=True,
        check=True,
    )
    return result.stdout


def revisions(root: Path, include_history: bool) -> list[str | None]:
    snapshots: list[str | None] = ["working tree", None]
    if not include_history:
        return snapshots
    result = subprocess.run(
        ["git", "-C", str(root), "rev-list", "--all"],
        text=True,
        capture_output=True,
        check=True,
    )
    return [*snapshots, *result.stdout.splitlines()]


def commit_metadata(root: Path, revision: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "cat-file", "commit", revision],
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout


def scan_sensitive_text(text: str, label: str, errors: list[str], forbidden_fragments: list[str]) -> None:
    if CREDENTIAL_URL.search(text):
        errors.append(f"{label}: credential-bearing URL")
    for fragment in forbidden_fragments:
        if fragment and fragment in text:
            errors.append(f"{label}: forbidden private fragment: {fragment!r}")


def scan_project_markers(text: str, label: str, errors: list[str]) -> None:
    for match in PLAN_IDENTIFIER.finditer(text):
        if match.group(1) not in SYNTHETIC_PLAN_PREFIXES:
            errors.append(f"{label}: non-synthetic plan identifier: {match.group(0)!r}")


def scan_text(text: str, label: str, errors: list[str], forbidden_fragments: list[str]) -> None:
    scan_sensitive_text(text, label, errors, forbidden_fragments)
    scan_project_markers(text, label, errors)


def scan_commit_metadata(text: str, label: str, errors: list[str], forbidden_fragments: list[str]) -> None:
    # Author/committer identities are untrusted metadata, not project content. Continue to
    # audit all metadata for credentials and explicit private fragments, but apply generic
    # project marker heuristics only to the commit message after the header separator.
    _, separator, message = text.partition("\n\n")
    scan_sensitive_text(text, label, errors, forbidden_fragments)
    scan_project_markers(message if separator else "", f"{label} message", errors)


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
        tree_revision = revision if revision == "working tree" else revision
        for path in snapshot_files(root, tree_revision):
            top = path.split("/", 1)[0]
            if top not in ALLOWED_TOP_LEVEL:
                errors.append(f"{label}: unexpected top-level path: {path}")
            if PRIVATE_PATH.search(path) or (RESEARCH_REGISTRY_PATH.search(path) and path != PUBLIC_RESEARCH_TEMPLATE):
                errors.append(f"{label}: private plan-state path: {path}")
            if GENERATED_PATH.search(path):
                errors.append(f"{label}: generated path: {path}")
            text = content_at(root, path, revision).decode("utf-8", errors="ignore")
            scan_text(text, f"{label}: {path}", errors, args.forbid)
        if args.history and isinstance(revision, str) and revision != "working tree":
            scan_commit_metadata(commit_metadata(root, revision), f"{label}: commit metadata", errors, args.forbid)

    if errors:
        print("Public release guard failed:", file=sys.stderr)
        for error in sorted(set(errors)):
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Public release guard passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
