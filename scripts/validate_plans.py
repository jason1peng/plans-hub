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
                # Arbitrary Markdown is passive datastore input. It is inventoried by
                # scan(), but never enters managed orchestration state.
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


def _diagnostic(code: str, message: str, *, path: str | None = None, plan_id: str | None = None,
                severity: str = "error", repair: str = "unsupported") -> dict[str, object]:
    item: dict[str, object] = {
        "code": code,
        "severity": severity,
        "message": message,
        "repair_classification": repair,
    }
    if path is not None:
        item["path"] = path
    if plan_id is not None:
        item["plan_id"] = plan_id
    return item


def scan(root: Path) -> dict[str, object]:
    """Inventory raw files and managed state without mutating the datastore."""
    diagnostics: list[dict[str, object]] = []
    unmanaged: list[dict[str, object]] = []
    managed: list[dict[str, object]] = []
    projects: list[str] = []
    if not root.is_dir():
        return {
            "schema_version": 1,
            "root": str(root),
            "projects": [],
            "managed_plans": [],
            "unmanaged_files": [],
            "diagnostics": [_diagnostic("root-not-directory", f"Plans root is not a directory: {root}")],
            "clean": False,
        }

    registry_errors: list[str] = []
    prefixes, entries, _ = parse_registry(root / "ORCHESTRATION.md", registry_errors)
    managed_ids: set[str] = set()
    syntactic_ids: dict[str, list[str]] = {}
    unmanaged_ids: dict[str, list[str]] = {}
    loose_name_re = re.compile(
        r"^(planning|ready|verifying|done)--([A-Z][A-Z0-9]*-[0-9]{3,})--(.+)\.md$"
    )
    metadata_id_re = re.compile(r"(?m)^ID:\s*([^\s]+)\s*$")

    for project_dir in sorted(
        path for path in root.iterdir()
        if path.is_dir() and path.name not in RESERVED_DIRS and not path.name.startswith(".")
    ):
        projects.append(project_dir.name)
        for path in sorted(project_dir.glob("*.md")):
            relative = str(path.relative_to(root))
            text = path.read_text(encoding="utf-8")
            match = STATUS_RE.fullmatch(path.name)
            if match:
                status, plan_id, name = match.groups()
                syntactic_ids.setdefault(plan_id, []).append(relative)
                metadata_id_ok = bool(re.search(rf"(?m)^ID:\s*{re.escape(plan_id)}\s*$", text))
                metadata_status_ok = bool(re.search(rf"(?m)^Status:\s*{re.escape(status)}\s*$", text))
                prefix_match = ID_RE.fullmatch(plan_id)
                expected_project = prefixes.get(prefix_match.group(1) if prefix_match else "")
                entry = entries.get(plan_id)
                contract_ok = (
                    metadata_id_ok
                    and metadata_status_ok
                    and expected_project == project_dir.name
                    and entry is not None
                    and entry["project"] == project_dir.name
                )
                if contract_ok:
                    managed_ids.add(plan_id)
                    managed.append({
                        "id": plan_id,
                        "status": status,
                        "project": project_dir.name,
                        "name": name,
                        "path": relative,
                    })
                else:
                    unmanaged_ids.setdefault(plan_id, []).append(relative)
                    unmanaged.append({
                        "path": relative,
                        "observed_id": plan_id,
                        "reason": "managed filename failed metadata or orchestration contract",
                    })
                continue

            metadata_match = metadata_id_re.search(text)
            raw_id = metadata_match.group(1) if metadata_match else None
            if raw_id and ID_RE.fullmatch(raw_id):
                unmanaged_ids.setdefault(raw_id, []).append(relative)
            proposal: dict[str, object] | None = None
            loose = loose_name_re.fullmatch(path.name)
            if loose and raw_id == loose.group(2) and re.search(
                rf"(?m)^Status:\s*{re.escape(loose.group(1))}\s*$", text
            ):
                slug = re.sub(r"[^a-z0-9]+", "-", loose.group(3).lower()).strip("-")
                if slug:
                    target = path.with_name(f"{loose.group(1)}--{loose.group(2)}--{slug}.md")
                    if target != path and not target.exists():
                        proposal = {
                            "kind": "rename",
                            "from": relative,
                            "to": str(target.relative_to(root)),
                            "classification": "automatic-safe",
                            "reason": "filename status and ID agree with metadata; normalize only the plan-name slug",
                        }
            unmanaged_item: dict[str, object] = {"path": relative, "reason": "filename is outside managed contract"}
            if raw_id:
                unmanaged_item["observed_id"] = raw_id
            if proposal:
                unmanaged_item["repair_proposal"] = proposal
            unmanaged.append(unmanaged_item)
            diagnostics.append(_diagnostic(
                "unmanaged-plan-file",
                "Markdown input is inactive because its filename does not satisfy the managed-plan contract",
                path=relative,
                plan_id=raw_id,
                severity="warning",
                repair="automatic-safe" if proposal else "approval-required",
            ))
            if raw_id and not ID_RE.fullmatch(raw_id):
                diagnostics.append(_diagnostic("malformed-plan-id", f"Invalid metadata ID {raw_id!r}", path=relative))

    duplicate_ids = {plan_id for plan_id, paths in syntactic_ids.items() if len(paths) > 1}
    if duplicate_ids:
        kept: list[dict[str, object]] = []
        for item in managed:
            if item["id"] in duplicate_ids:
                unmanaged.append({"path": item["path"], "observed_id": item["id"], "reason": "duplicate managed ID"})
                unmanaged_ids.setdefault(str(item["id"]), []).append(str(item["path"]))
                managed_ids.discard(str(item["id"]))
            else:
                kept.append(item)
        managed = kept

    for plan_id, paths in sorted(unmanaged_ids.items()):
        if plan_id in managed_ids or len(paths) > 1 or plan_id in duplicate_ids:
            diagnostics.append(_diagnostic(
                "ambiguous-plan-id",
                f"Unmanaged input mentions active or duplicate ID {plan_id}: {', '.join(sorted(set(paths)))}",
                plan_id=plan_id,
                repair="approval-required",
            ))

    code_rules = (
        ("missing or inconsistent ID", "metadata-id-mismatch"),
        ("missing or inconsistent Status", "lifecycle-mismatch"),
        ("multiple files", "duplicate-plan-id"),
        ("missing from orchestration", "missing-orchestration-row"),
        ("Orchestration entry has no plan file", "stale-orchestration-row"),
        ("unresolved dependency", "missing-dependency"),
        ("Dependency cycle", "dependency-cycle"),
        ("claim", "invalid-claim"),
        ("findings", "findings-lifecycle"),
        ("Findings", "findings-lifecycle"),
    )
    for message in validate(root):
        code = "contract-violation"
        for fragment, candidate in code_rules:
            if fragment in message:
                code = candidate
                break
        diagnostics.append(_diagnostic(code, message, repair="approval-required"))

    return {
        "schema_version": 1,
        "root": str(root),
        "projects": projects,
        "managed_plans": managed,
        "unmanaged_files": unmanaged,
        "diagnostics": diagnostics,
        "clean": not diagnostics,
    }


def policy_errors(root: Path) -> list[str]:
    """Return violations that can make policy-aware mutations unsafe."""
    errors = validate(root)
    report = scan(root)
    for item in report["diagnostics"]:
        if item["code"] == "ambiguous-plan-id":
            errors.append(f"ambiguous unmanaged input: {item['message']}")
    return errors


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
        if has_claimant and not re.fullmatch(r"[A-Za-z0-9._@:/+-]+", claimed_by):
            errors.append(f"Plan {plan_id!r} has invalid claimant {claimed_by!r}")
        if has_time and not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", claimed_at):
            errors.append(f"Plan {plan_id!r} has invalid claim timestamp {claimed_at!r}")
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
