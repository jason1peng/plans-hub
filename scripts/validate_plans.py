#!/usr/bin/env python3
"""Validate shared plan IDs, storage, findings, orchestration, and claims."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

STATUS_RE = re.compile(
    r"^(planning|ready|verifying|done)--([A-Z][A-Z0-9]*-[0-9]{3,})--([a-z0-9]+(?:-[a-z0-9]+)*)\.md$"
)
ID_RE = re.compile(r"^([A-Z][A-Z0-9]*)-([0-9]{3,})$")
PROJECT_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
EMPTY_VALUES = {"", "—", "-", "none"}
RESERVED_DIRS = {"scripts", "skills"}
STALE_PRIVATE_CLIENT_PATH = "scripts/planctl.py"


def clean_cell(value: str) -> str:
    return value.strip().strip("`").strip()


def split_values(value: str) -> list[str]:
    value = clean_cell(value)
    if value.lower() in EMPTY_VALUES:
        return []
    return [
        clean_cell(item)
        for item in re.split(r"\s*<br\s*/?>\s*|\s*,\s*", value)
        if clean_cell(item)
    ]


def markdown_table(path: Path, heading: str, columns: int, errors: list[str]) -> list[tuple[int, list[str]]]:
    if not path.is_file():
        errors.append(f"Missing {path.name}")
        return []
    rows: list[tuple[int, list[str]]] = []
    in_section = False
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip() == heading:
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if not in_section or not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != columns:
            errors.append(f"{path.name}:{line_number}: expected {columns} table columns")
            continue
        first = clean_cell(cells[0])
        if first in {"Prefix", "ID"} or set(first) <= {"-", ":"}:
            continue
        rows.append((line_number, cells))
    return rows


def parse_registry(
    path: Path, errors: list[str]
) -> tuple[dict[str, str], dict[str, dict[str, object]], dict[str, str]]:
    prefixes: dict[str, str] = {}
    projects: set[str] = set()
    for line, cells in markdown_table(path, "## Project prefix registry", 2, errors):
        prefix, project = map(clean_cell, cells)
        if not re.fullmatch(r"[A-Z][A-Z0-9]*", prefix):
            errors.append(f"{path.name}:{line}: invalid prefix {prefix!r}")
        if not PROJECT_RE.fullmatch(project):
            errors.append(f"{path.name}:{line}: invalid project folder {project!r}")
        if prefix in prefixes:
            errors.append(f"{path.name}:{line}: duplicate prefix {prefix!r}")
        if project in projects:
            errors.append(f"{path.name}:{line}: project {project!r} has multiple prefixes")
        prefixes[prefix] = project
        projects.add(project)

    entries: dict[str, dict[str, object]] = {}
    for line, cells in markdown_table(path, "## Plan dependency graph", 5, errors):
        plan_id, project, dependencies, claimed_by, claimed_at = map(clean_cell, cells)
        if not ID_RE.fullmatch(plan_id):
            errors.append(f"{path.name}:{line}: invalid plan ID {plan_id!r}")
        if plan_id in entries:
            errors.append(f"{path.name}:{line}: duplicate plan ID {plan_id!r}")
            continue
        entries[plan_id] = {
            "project": project,
            "dependencies": split_values(dependencies),
            "claimed_by": claimed_by,
            "claimed_at": claimed_at,
            "line": line,
        }

    retired: dict[str, str] = {}
    for line, cells in markdown_table(path, "## Retired plan IDs", 2, errors):
        plan_id, project = map(clean_cell, cells)
        if not ID_RE.fullmatch(plan_id):
            errors.append(f"{path.name}:{line}: invalid retired plan ID {plan_id!r}")
        if plan_id in retired:
            errors.append(f"{path.name}:{line}: duplicate retired plan ID {plan_id!r}")
        if plan_id in entries:
            errors.append(f"{path.name}:{line}: active plan ID {plan_id!r} is also retired")
        retired[plan_id] = project
    return prefixes, entries, retired


def find_plans(root: Path, errors: list[str]) -> dict[str, dict[str, object]]:
    plans: dict[str, dict[str, object]] = {}
    for project_dir in sorted(
        path
        for path in root.iterdir()
        if path.is_dir() and path.name not in RESERVED_DIRS and not path.name.startswith(".")
    ):
        if not PROJECT_RE.fullmatch(project_dir.name):
            errors.append(f"Invalid project folder name: {project_dir.name!r}")
        for plan_file in sorted(project_dir.glob("*.md")):
            match = STATUS_RE.fullmatch(plan_file.name)
            if not match:
                errors.append(f"Invalid plan filename: {plan_file.relative_to(root)}")
                continue
            status, plan_id, plan_name = match.groups()
            if plan_id in plans:
                errors.append(
                    f"Plan ID {plan_id!r} has multiple files: "
                    f"{plans[plan_id]['path'].relative_to(root)} and {plan_file.relative_to(root)}"
                )
                continue
            text = plan_file.read_text(encoding="utf-8")
            if not re.search(rf"(?m)^ID:\s*{re.escape(plan_id)}\s*$", text):
                errors.append(f"Plan {plan_id!r} has missing or inconsistent ID field")
            if not re.search(rf"(?m)^Status:\s*{status}\s*$", text):
                errors.append(f"Plan {plan_id!r} has missing or inconsistent Status field")
            plans[plan_id] = {
                "status": status,
                "project": project_dir.name,
                "path": plan_file,
                "name": plan_name,
                "text": text,
            }
    return plans


def validate_findings(root: Path, plans: dict[str, dict[str, object]], errors: list[str]) -> None:
    for plan_id, plan in plans.items():
        findings_dir = root / str(plan["project"]) / "findings" / plan_id
        expected_link = f"findings/{plan_id}/README.md"
        has_link = bool(
            re.search(rf"\]\(\s*{re.escape(expected_link)}\s*\)", str(plan["text"]))
        )
        if findings_dir.exists():
            if not findings_dir.is_dir():
                errors.append(f"Findings path is not a directory: {findings_dir.relative_to(root)}")
            elif plan["status"] == "done":
                errors.append(f"Done plan {plan_id!r} must not retain {findings_dir.relative_to(root)}")
            else:
                entrypoint = findings_dir / "README.md"
                if not entrypoint.is_file():
                    errors.append(f"Missing findings entry point: {entrypoint.relative_to(root)}")
                if not has_link:
                    errors.append(f"Plan {plan_id!r} does not link to {expected_link}")
        elif has_link:
            errors.append(f"Plan {plan_id!r} contains a stale findings link: {expected_link}")

    for project_dir in sorted(
        path
        for path in root.iterdir()
        if path.is_dir() and path.name not in RESERVED_DIRS and not path.name.startswith(".")
    ):
        findings_root = project_dir / "findings"
        if not findings_root.is_dir():
            continue
        for findings_dir in sorted(path for path in findings_root.iterdir() if path.is_dir()):
            if findings_dir.name not in plans:
                errors.append(f"Orphaned findings directory: {findings_dir.relative_to(root)}")
            elif plans[findings_dir.name]["project"] != project_dir.name:
                errors.append(f"Findings directory is under wrong project: {findings_dir.relative_to(root)}")


def detect_cycles(entries: dict[str, dict[str, object]], errors: list[str]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(plan_id: str, trail: list[str]) -> None:
        if plan_id in visiting:
            start = trail.index(plan_id)
            errors.append("Dependency cycle: " + " -> ".join(trail[start:] + [plan_id]))
            return
        if plan_id in visited:
            return
        visiting.add(plan_id)
        for dependency in entries.get(plan_id, {}).get("dependencies", []):
            if dependency in entries:
                visit(str(dependency), trail + [plan_id])
        visiting.remove(plan_id)
        visited.add(plan_id)

    for plan_id in entries:
        visit(plan_id, [])


def validate(root: Path) -> list[str]:
    errors: list[str] = []
    if not root.is_dir():
        return [f"Plans root is not a directory: {root}"]
    for required in ("README.md", "AGENTS.md", "ORCHESTRATION.md"):
        if not (root / required).is_file():
            errors.append(f"Missing {required}")

    orchestration_path = root / "ORCHESTRATION.md"
    if orchestration_path.is_file() and STALE_PRIVATE_CLIENT_PATH in orchestration_path.read_text(encoding="utf-8"):
        errors.append(
            "ORCHESTRATION.md references removed private client path 'scripts/planctl.py'; "
            "use the installed planctl wrapper"
        )

    prefixes, entries, retired = parse_registry(orchestration_path, errors)
    plans = find_plans(root, errors)

    for plan_id, plan in plans.items():
        match = ID_RE.fullmatch(plan_id)
        prefix = match.group(1) if match else ""
        expected_project = prefixes.get(prefix)
        if expected_project is None:
            errors.append(f"Plan {plan_id!r} uses unregistered prefix {prefix!r}")
        elif expected_project != plan["project"]:
            errors.append(f"Plan {plan_id!r} belongs in {expected_project!r}, not {plan['project']!r}")
        entry = entries.get(plan_id)
        if entry is None:
            errors.append(f"Plan missing from orchestration: {plan_id}")
            continue
        if entry["project"] != plan["project"]:
            errors.append(f"Orchestration project mismatch for {plan_id!r}")
        claimed_by = str(entry["claimed_by"])
        claimed_at = str(entry["claimed_at"])
        has_claimant = claimed_by.lower() not in EMPTY_VALUES
        has_time = claimed_at.lower() not in EMPTY_VALUES
        if has_claimant != has_time:
            errors.append(f"Plan {plan_id!r} must set or clear both claim fields")
        if has_claimant and plan["status"] not in {"ready", "verifying"}:
            errors.append(f"Plan {plan_id!r} cannot be claimed while status is {plan['status']!r}")
        dependencies_must_be_done = has_claimant or plan["status"] in {"verifying", "done"}
        if dependencies_must_be_done:
            for dependency in entry["dependencies"]:
                dependency_plan = plans.get(str(dependency))
                if dependency_plan is not None and dependency_plan["status"] != "done":
                    errors.append(
                        f"Plan {plan_id!r} requires dependency {dependency!r} to be done "
                        f"while status is {plan['status']!r}"
                    )

    for plan_id, project in retired.items():
        if plan_id in plans:
            errors.append(f"Retired plan ID has an active plan file: {plan_id}")
        match = ID_RE.fullmatch(plan_id)
        if match and prefixes.get(match.group(1)) != project:
            errors.append(f"Retired ID prefix/project mismatch for {plan_id!r}")

    for plan_id, entry in entries.items():
        if plan_id not in plans:
            errors.append(f"Orchestration entry has no plan file: {plan_id}")
        match = ID_RE.fullmatch(plan_id)
        if match and prefixes.get(match.group(1)) != entry["project"]:
            errors.append(f"Orchestration prefix/project mismatch for {plan_id!r}")
        for dependency in entry["dependencies"]:
            if dependency not in plans:
                errors.append(f"Plan {plan_id!r} has unresolved dependency {dependency!r}")
            if dependency == plan_id:
                errors.append(f"Plan {plan_id!r} depends on itself")

    validate_findings(root, plans, errors)
    detect_cycles(entries, errors)
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=".", type=Path)
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    errors = validate(root)
    if errors:
        print(f"Plan storage validation failed ({len(errors)} error(s)):", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(f"Plan storage validation passed: {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
