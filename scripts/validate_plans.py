#!/usr/bin/env python3
"""Validate shared plan IDs, storage, findings, orchestration, and claims."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

STATUS_RE = re.compile(
    r"^(planning|ready|verifying|done)--([A-Z][A-Z0-9]*-[0-9]{3,})--([a-z0-9]+(?:-[a-z0-9]+)*)\.md$"
)
RESEARCH_STATUS_RE = re.compile(
    r"^(open|converted|cancelled|archived)--(RES-[0-9]{3,})--([a-z0-9]+(?:-[a-z0-9]+)*)\.md$"
)
ID_RE = re.compile(r"^([A-Z][A-Z0-9]*)-([0-9]{3,})$")
RESEARCH_ID_RE = re.compile(r"^RES-[0-9]{3,}$")
PROJECT_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
EMPTY_VALUES = {"", "—", "-", "none"}
# ``research`` is deliberately not a project folder.  Keeping it here makes
# the reservation apply to every plan scan, including older hubs that have no
# RESEARCH.md yet.
RESERVED_DIRS = {"scripts", "skills", "research"}
RESEARCH_REGISTRY = "RESEARCH.md"
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
        if prefix == "RES":
            errors.append(f"{path.name}:{line}: prefix 'RES' is reserved for research IDs")
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


def find_plan_candidates(root: Path) -> dict[str, list[dict[str, object]]]:
    """Return syntactically named candidates without activating raw input."""
    candidates: dict[str, list[dict[str, object]]] = {}
    for project_dir in sorted(
        path
        for path in root.iterdir()
        if path.is_dir() and path.name not in RESERVED_DIRS and not path.name.startswith(".")
    ):
        for plan_file in sorted(project_dir.glob("*.md")):
            match = STATUS_RE.fullmatch(plan_file.name)
            if not match:
                continue
            status, plan_id, plan_name = match.groups()
            candidates.setdefault(plan_id, []).append({
                "status": status,
                "project": project_dir.name,
                "path": plan_file,
                "name": plan_name,
                "text": plan_file.read_text(encoding="utf-8"),
            })
    return candidates


def metadata_values(text: str, field: str) -> list[str]:
    """Return every top-level metadata value for a managed contract field."""
    return re.findall(rf"(?m)^{re.escape(field)}:\s*(.*?)\s*$", text)


def research_metadata_values(text: str, field: str) -> list[str]:
    """Return research metadata, accepting the early public spellings too."""
    aliases = {
        "ID": ("ID", "Research ID"),
        "Status": ("Status",),
        "Scope": ("Scope",),
        "Projects": ("Projects", "Project"),
        "Linked Plans": ("Linked Plans", "Linked plans", "Plans"),
        "Path": ("Path",),
    }
    fields = aliases.get(field, (field,))
    values: list[str] = []
    for candidate in fields:
        values.extend(metadata_values(text, candidate))
    return values


def _research_table_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def parse_research_registry(
    path: Path, errors: list[str]
) -> tuple[dict[str, dict[str, object]], dict[str, str]]:
    """Read the optional RESEARCH.md registry without activating raw records."""
    if not path.exists():
        return {}, {}
    if not path.is_file():
        errors.append(f"Cannot read {path.name}: path is not a file")
        return {}, {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        errors.append(f"Cannot read {path.name}: {error}")
        return {}, {}

    entries: dict[str, dict[str, object]] = {}
    retired: dict[str, str] = {}
    section: str | None = None
    saw_records = False
    saw_retired = False
    for line_number, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("# "):
            if "research" in stripped[2:].strip().lower():
                section = "records"
                saw_records = True
            else:
                section = None
            continue
        if stripped.startswith("## "):
            heading = stripped[3:].strip().lower()
            if "retired" in heading and "research" in heading:
                section = "retired"
                saw_retired = True
            elif "research" in heading and (
                heading == "research" or any(word in heading for word in ("record", "registry", "managed"))
            ):
                section = "records"
                saw_records = True
            else:
                section = None
            continue
        if section is None or not line.lstrip().startswith("|"):
            continue
        cells = _research_table_cells(line)
        if not cells or all(set(cell) <= {"-", ":", " "} for cell in cells):
            continue
        first = clean_cell(cells[0]).lower()
        if first in {"id", "research id", "research-id"}:
            continue
        if section == "records":
            if len(cells) != 6:
                errors.append(f"{path.name}:{line_number}: expected 6 research registry columns")
                continue
            research_id, status, scope, projects, linked_plans, record_path = map(clean_cell, cells)
            if not RESEARCH_ID_RE.fullmatch(research_id):
                errors.append(f"{path.name}:{line_number}: invalid research ID {research_id!r}")
            if research_id in entries:
                errors.append(f"{path.name}:{line_number}: duplicate research ID {research_id!r}")
                continue
            entries[research_id] = {
                "id": research_id,
                "status": status,
                "scope": scope,
                "projects": split_values(projects),
                "linked_plans": split_values(linked_plans),
                "path": record_path,
                "line": line_number,
            }
        elif section == "retired":
            if len(cells) not in {1, 2}:
                errors.append(f"{path.name}:{line_number}: expected 1 or 2 retired research columns")
                continue
            research_id = clean_cell(cells[0])
            if not RESEARCH_ID_RE.fullmatch(research_id):
                errors.append(f"{path.name}:{line_number}: invalid retired research ID {research_id!r}")
            if research_id in retired:
                errors.append(f"{path.name}:{line_number}: duplicate retired research ID {research_id!r}")
            retired[research_id] = clean_cell(cells[1]) if len(cells) == 2 else ""
    if not saw_records:
        errors.append(f"{path.name} is missing a managed research records section")
    if not saw_retired:
        # Retired IDs are optional for compatibility with hand-written registries;
        # allocation still considers all records and files.  The synthetic
        # template includes this section so new hubs get the complete contract.
        pass
    return entries, retired


def find_research_candidates(root: Path) -> dict[str, list[dict[str, object]]]:
    """Return syntactically named research files, without activating raw input."""
    candidates: dict[str, list[dict[str, object]]] = {}
    directory = root / "research"
    if not directory.is_dir():
        return candidates
    for record_file in sorted(directory.glob("*.md")):
        match = RESEARCH_STATUS_RE.fullmatch(record_file.name)
        if not match:
            continue
        status, research_id, slug = match.groups()
        candidates.setdefault(research_id, []).append({
            "status": status,
            "id": research_id,
            "name": slug,
            "path": record_file,
            "text": record_file.read_text(encoding="utf-8"),
        })
    return candidates


def _research_record_metadata(
    record: dict[str, object], prefixes: dict[str, str]
) -> tuple[dict[str, object], list[str]]:
    """Parse and validate one record's intrinsic metadata."""
    text = str(record["text"])
    research_id = str(record["id"])
    status = str(record["status"])
    errors: list[str] = []
    values: dict[str, object] = {
        "id": research_id,
        "status": status,
        "scope": "",
        "projects": [],
        "linked_plans": [],
        "path": str(Path(str(record["path"])).as_posix()),
        "name": str(record["name"]),
        "text": text,
    }
    id_values = research_metadata_values(text, "ID")
    status_values = research_metadata_values(text, "Status")
    scope_values = research_metadata_values(text, "Scope")
    project_values = research_metadata_values(text, "Projects")
    linked_values = research_metadata_values(text, "Linked Plans")
    path_values = research_metadata_values(text, "Path")
    if id_values != [research_id]:
        errors.append(f"Research {research_id!r} must have exactly one matching ID field")
    if status_values != [status]:
        errors.append(f"Research {research_id!r} must have exactly one matching Status field")
    if scope_values != [scope_values[0] if scope_values else ""]:
        errors.append(f"Research {research_id!r} must have exactly one Scope field")
    scope = clean_cell(scope_values[0]) if len(scope_values) == 1 else ""
    values["scope"] = scope
    if scope not in {"project", "cross-project", "unknown"}:
        errors.append(f"Research {research_id!r} has invalid scope {scope!r}")
    projects = split_values(project_values[0]) if len(project_values) == 1 else []
    if len(project_values) != 1:
        errors.append(f"Research {research_id!r} must have exactly one Projects field")
    values["projects"] = projects
    if scope == "project":
        if len(projects) != 1 or not PROJECT_RE.fullmatch(projects[0]):
            errors.append(f"Research {research_id!r} project scope requires one lowercase project")
        elif projects[0] not in set(prefixes.values()):
            errors.append(f"Research {research_id!r} uses unknown project {projects[0]!r}")
    elif projects:
        errors.append(f"Research {research_id!r} scope {scope!r} must not list projects")
    linked_plans = split_values(linked_values[0]) if len(linked_values) == 1 else []
    if len(linked_values) != 1:
        errors.append(f"Research {research_id!r} must have exactly one Linked Plans field")
    if len(linked_plans) != len(set(linked_plans)):
        errors.append(f"Research {research_id!r} lists a linked plan more than once")
    invalid_links = [plan_id for plan_id in linked_plans if not ID_RE.fullmatch(plan_id)]
    if invalid_links:
        errors.append(f"Research {research_id!r} has invalid linked plan ID(s): {', '.join(invalid_links)}")
    values["linked_plans"] = linked_plans
    expected_relative = f"research/{Path(str(record['path'])).name}"
    if path_values != [expected_relative]:
        errors.append(f"Research {research_id!r} must have Path: {expected_relative}")
    values["path"] = expected_relative
    if status == "open" and linked_plans:
        errors.append(f"Open research {research_id!r} must not have linked plans")
    if status == "converted" and not linked_plans:
        errors.append(f"Converted research {research_id!r} must list at least one linked plan")
    if status == "cancelled" and linked_plans:
        errors.append(f"Cancelled research {research_id!r} must not have linked plans")
    return values, errors


def research_validation_state(
    root: Path,
    prefixes: dict[str, str] | None = None,
    plan_entries: dict[str, dict[str, object]] | None = None,
    plans: dict[str, dict[str, object]] | None = None,
) -> tuple[list[str], dict[str, dict[str, object]], dict[str, dict[str, object]], set[str]]:
    """Validate the optional research namespace and return valid records."""
    errors: list[str] = []
    records: dict[str, dict[str, object]] = {}
    invalid_ids: set[str] = set()
    if not root.is_dir():
        return [f"Plans root is not a directory: {root}"], {}, {}, invalid_ids
    if prefixes is None:
        registry_errors: list[str] = []
        prefixes, _, _ = parse_registry(root / "ORCHESTRATION.md", registry_errors)
        errors.extend(registry_errors)
    registry_path = root / RESEARCH_REGISTRY
    candidates = find_research_candidates(root)
    registry_entries: dict[str, dict[str, object]] = {}
    retired: dict[str, str] = {}
    registry_invalid = False
    if registry_path.exists():
        registry_errors: list[str] = []
        registry_entries, retired = parse_research_registry(registry_path, registry_errors)
        errors.extend(registry_errors)
        registry_invalid = bool(registry_errors)
    elif candidates:
        errors.append("Missing RESEARCH.md for managed research records")

    for research_id, matches in sorted(candidates.items()):
        if len(matches) != 1:
            paths = ", ".join(str(item["path"].relative_to(root)) for item in matches)
            errors.append(f"Research ID {research_id!r} has multiple files: {paths}")
            invalid_ids.add(research_id)
            continue
        record, intrinsic_errors = _research_record_metadata(matches[0], prefixes or {})
        records[research_id] = record
        if intrinsic_errors:
            errors.extend(intrinsic_errors)
            invalid_ids.add(research_id)
        entry = registry_entries.get(research_id)
        if entry is None:
            errors.append(f"Research {research_id!r} is missing from RESEARCH.md")
            invalid_ids.add(research_id)
            continue
        comparisons = (
            ("status", "status"),
            ("scope", "scope"),
            ("projects", "projects"),
            ("linked_plans", "linked_plans"),
            ("path", "path"),
        )
        for field, label in comparisons:
            if entry[field] != record[field]:
                errors.append(f"Research registry mismatch for {research_id!r}: {label}")
                invalid_ids.add(research_id)
    for research_id, entry in registry_entries.items():
        if research_id not in candidates:
            errors.append(f"Research registry entry has no file: {research_id}")

    if registry_invalid:
        invalid_ids.update(candidates)
    duplicate_ids = {research_id for research_id, matches in candidates.items() if len(matches) > 1}
    for research_id in sorted(duplicate_ids):
        errors.append(f"Ambiguous research ID {research_id!r}: duplicate research files")
    for research_id in sorted(retired):
        if research_id in registry_entries or research_id in candidates:
            errors.append(f"Retired research ID has an active record: {research_id}")
            invalid_ids.add(research_id)

    # Cross-link checks are intentionally only applied when the caller supplied
    # the current plan view.  Standalone scan still reports intrinsic research
    # state, while plan operations can validate one requested relationship.
    if plan_entries is not None:
        for research_id, record in records.items():
            if research_id in invalid_ids:
                continue
            for plan_id in record["linked_plans"]:
                if plan_id not in plan_entries:
                    errors.append(f"Research {research_id!r} links unknown plan {plan_id!r}")
                    invalid_ids.add(research_id)
                elif plans is not None and plan_id in plans:
                    linked_plan = plans[plan_id]
                    if (
                        record["scope"] == "project"
                        and str(linked_plan["project"]) not in record["projects"]
                    ):
                        errors.append(
                            f"Project-scoped research {research_id!r} cannot link plan {plan_id!r} "
                            f"from project {linked_plan['project']!r}"
                        )
                        invalid_ids.add(research_id)
                    sources = plan_research_ids(str(linked_plan["text"]))
                    if research_id not in sources:
                        errors.append(f"Research {research_id!r} is missing backlink from plan {plan_id!r}")
                        invalid_ids.add(research_id)
        if plans is not None:
            for plan_id, plan in plans.items():
                source_ids = plan_research_ids(str(plan["text"]))
                for research_id in source_ids:
                    if research_id not in records:
                        errors.append(f"Plan {plan_id!r} links unknown research {research_id!r}")
                        continue
                    if research_id in invalid_ids:
                        continue
                    record = records[research_id]
                    if (
                        record["scope"] == "project"
                        and plan_id in plans
                        and str(plans[plan_id]["project"]) not in record["projects"]
                    ):
                        errors.append(
                            f"Project-scoped research {research_id!r} cannot link plan {plan_id!r} "
                            f"from project {plans[plan_id]['project']!r}"
                        )
                        invalid_ids.add(plan_id)
                        continue
                    if plan_id not in record["linked_plans"]:
                        errors.append(f"Plan {plan_id!r} is missing backlink in research {research_id!r}")
    return errors, records, registry_entries, invalid_ids


def plan_research_ids(text: str) -> list[str]:
    """Parse optional source-research metadata from an implementation plan."""
    values: list[str] = []
    for field in ("Research", "Research IDs", "Source Research"):
        for value in metadata_values(text, field):
            values.extend(split_values(value))
    return values


def find_plans(
    root: Path,
    errors: list[str],
    prefixes: dict[str, str] | None = None,
    entries: dict[str, dict[str, object]] | None = None,
) -> dict[str, dict[str, object]]:
    """Return only registered candidates that satisfy the base managed contract."""
    if prefixes is None or entries is None:
        prefixes, entries, _ = parse_registry(root / "ORCHESTRATION.md", errors)
    plans: dict[str, dict[str, object]] = {}
    for plan_id, matches in find_plan_candidates(root).items():
        # Duplicate inactive inputs remain raw. A duplicate of a registered ID is
        # active ambiguity and therefore invalidates that orchestration entry.
        if len(matches) != 1:
            if plan_id in entries:
                paths = " and ".join(str(item["path"].relative_to(root)) for item in matches)
                errors.append(f"Plan ID {plan_id!r} has multiple files: {paths}")
            continue
        plan = matches[0]
        if plan_id not in entries:
            continue
        text = str(plan["text"])
        valid = True
        id_values = metadata_values(text, "ID")
        if id_values != [plan_id]:
            errors.append(f"Plan {plan_id!r} must have exactly one matching ID field")
            valid = False
        status_values = metadata_values(text, "Status")
        if status_values != [str(plan["status"])]:
            errors.append(f"Plan {plan_id!r} must have exactly one matching Status field")
            valid = False
        match = ID_RE.fullmatch(plan_id)
        expected_project = prefixes.get(match.group(1) if match else "")
        if expected_project is None:
            errors.append(f"Plan {plan_id!r} uses unregistered prefix")
            valid = False
        elif expected_project != plan["project"]:
            errors.append(f"Plan {plan_id!r} belongs in {expected_project!r}, not {plan['project']!r}")
            valid = False
        if entries[plan_id]["project"] != plan["project"]:
            errors.append(f"Orchestration project mismatch for {plan_id!r}")
            valid = False
        if valid:
            plans[plan_id] = plan
    return plans


def validate_findings(
    root: Path,
    plans: dict[str, dict[str, object]],
    errors: list[str],
    invalid_ids: set[str] | None = None,
) -> None:
    for plan_id, plan in plans.items():
        findings_dir = root / str(plan["project"]) / "findings" / plan_id
        expected_link = f"findings/{plan_id}/README.md"
        has_link = bool(
            re.search(rf"\]\(\s*{re.escape(expected_link)}\s*\)", str(plan["text"]))
        )
        plan_errors_before = len(errors)
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
        if invalid_ids is not None and len(errors) != plan_errors_before:
            invalid_ids.add(plan_id)

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
                # Findings attached only to inactive raw input remain inactive too.
                continue
            elif plans[findings_dir.name]["project"] != project_dir.name:
                errors.append(f"Findings directory is under wrong project: {findings_dir.relative_to(root)}")
                if invalid_ids is not None:
                    invalid_ids.add(findings_dir.name)


def detect_cycles(
    entries: dict[str, dict[str, object]], errors: list[str], invalid_ids: set[str] | None = None
) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(plan_id: str, trail: list[str]) -> None:
        if plan_id in visiting:
            start = trail.index(plan_id)
            cycle = trail[start:] + [plan_id]
            errors.append("Dependency cycle: " + " -> ".join(cycle))
            if invalid_ids is not None:
                invalid_ids.update(cycle)
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


def _diagnostic(
    code: str,
    message: str,
    *,
    path: str | None = None,
    plan_id: str | None = None,
    research_id: str | None = None,
    severity: str = "error",
    repair: str = "unsupported",
) -> dict[str, object]:
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
    if research_id is not None:
        item["research_id"] = research_id
    return item


def scan(root: Path) -> dict[str, object]:
    """Inventory raw files and managed state without mutating the datastore."""
    diagnostics: list[dict[str, object]] = []
    unmanaged: list[dict[str, object]] = []
    managed: list[dict[str, object]] = []
    managed_research: list[dict[str, object]] = []
    unmanaged_research: list[dict[str, object]] = []
    projects: list[str] = []
    if not root.is_dir():
        return {
            "schema_version": 1,
            "root": str(root),
            "projects": [],
            "managed_plans": [],
            "managed_research": [],
            "unmanaged_files": [],
            "unmanaged_research": [],
            "diagnostics": [_diagnostic("root-not-directory", f"Plans root is not a directory: {root}")],
            "clean": False,
        }

    registry_errors: list[str] = []
    prefixes, entries, _ = parse_registry(root / "ORCHESTRATION.md", registry_errors)
    managed_ids: set[str] = set()
    syntactic_ids: dict[str, list[str]] = {}
    unmanaged_ids: dict[str, list[str]] = {}
    unmanaged_id_mentions: dict[str, int] = {}
    loose_name_re = re.compile(
        r"^(planning|ready|verifying|done)--([A-Z][A-Z0-9]*-[0-9]{3,})--(.+)\.md$"
    )

    def record_unmanaged_id(plan_id: str, relative: str) -> None:
        if ID_RE.fullmatch(plan_id):
            unmanaged_ids.setdefault(plan_id, []).append(relative)
            unmanaged_id_mentions[plan_id] = unmanaged_id_mentions.get(plan_id, 0) + 1

    for project_dir in sorted(
        path for path in root.iterdir()
        if path.is_dir() and path.name not in RESERVED_DIRS and not path.name.startswith(".")
    ):
        projects.append(project_dir.name)
        if not PROJECT_RE.fullmatch(project_dir.name):
            diagnostics.append(_diagnostic(
                "invalid-project-folder",
                "Project folder is outside the managed contract; use a lowercase kebab-case name",
                path=project_dir.name,
                severity="warning",
                repair="approval-required",
            ))
        for path in sorted(project_dir.glob("*.md")):
            relative = str(path.relative_to(root))
            text = path.read_text(encoding="utf-8")
            match = STATUS_RE.fullmatch(path.name)
            if match:
                status, plan_id, name = match.groups()
                syntactic_ids.setdefault(plan_id, []).append(relative)
                metadata_id_ok = metadata_values(text, "ID") == [plan_id]
                metadata_status_ok = metadata_values(text, "Status") == [status]
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
                    # A sole registered candidate that fails its own contract is
                    # scoped invalid state, not global identity ambiguity. Other
                    # active IDs mentioned by that candidate remain ambiguous.
                    id_values = metadata_values(text, "ID")
                    if entry is None:
                        for metadata_id in id_values:
                            record_unmanaged_id(metadata_id, relative)
                        if plan_id not in id_values:
                            record_unmanaged_id(plan_id, relative)
                    else:
                        for metadata_id in id_values:
                            if metadata_id != plan_id and metadata_id in entries:
                                record_unmanaged_id(metadata_id, relative)
                    reason = "managed filename failed metadata or orchestration contract"
                    unmanaged.append({
                        "path": relative,
                        "observed_id": plan_id,
                        "reason": reason,
                    })
                    diagnostics.append(_diagnostic(
                        "unregistered-plan-file" if entry is None else "invalid-managed-candidate",
                        f"Markdown input is inactive because its managed contract is incomplete: {reason}",
                        path=relative,
                        plan_id=plan_id,
                        severity="warning" if entry is None else "error",
                        repair="approval-required",
                    ))
                    if len(id_values) > 1:
                        diagnostics.append(_diagnostic(
                            "metadata-id-mismatch",
                            "Raw input has repeated or conflicting ID metadata",
                            path=relative,
                            plan_id=plan_id,
                            repair="approval-required",
                        ))
                    if len(metadata_values(text, "Status")) > 1:
                        diagnostics.append(_diagnostic(
                            "lifecycle-mismatch",
                            "Raw input has repeated or conflicting Status metadata",
                            path=relative,
                            plan_id=plan_id,
                            repair="approval-required",
                        ))
                continue

            raw_ids = metadata_values(text, "ID")
            raw_statuses = metadata_values(text, "Status")
            for raw_id in raw_ids:
                record_unmanaged_id(raw_id, relative)
            observed_id = raw_ids[0] if len(raw_ids) == 1 else None
            proposal: dict[str, object] | None = None
            loose = loose_name_re.fullmatch(path.name)
            if (
                loose
                and raw_ids == [loose.group(2)]
                and raw_statuses == [loose.group(1)]
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
            if observed_id:
                unmanaged_item["observed_id"] = observed_id
            if proposal:
                unmanaged_item["repair_proposal"] = proposal
            unmanaged.append(unmanaged_item)
            diagnostics.append(_diagnostic(
                "unmanaged-plan-file",
                "Markdown input is inactive because its filename does not satisfy the managed-plan contract",
                path=relative,
                plan_id=observed_id,
                severity="warning",
                repair="automatic-safe" if proposal else "approval-required",
            ))
            if len(raw_ids) > 1:
                diagnostics.append(_diagnostic(
                    "metadata-id-mismatch",
                    "Raw input has repeated or conflicting ID metadata",
                    path=relative,
                    repair="approval-required",
                ))
            if len(raw_statuses) > 1:
                diagnostics.append(_diagnostic(
                    "lifecycle-mismatch",
                    "Raw input has repeated or conflicting Status metadata",
                    path=relative,
                    repair="approval-required",
                ))
            for raw_id in raw_ids:
                if not ID_RE.fullmatch(raw_id):
                    diagnostics.append(_diagnostic(
                        "malformed-plan-id", f"Invalid metadata ID {raw_id!r}", path=relative
                    ))

    duplicate_ids = {plan_id for plan_id, paths in syntactic_ids.items() if len(paths) > 1}
    if duplicate_ids:
        kept: list[dict[str, object]] = []
        for item in managed:
            if item["id"] in duplicate_ids:
                unmanaged.append({"path": item["path"], "observed_id": item["id"], "reason": "duplicate managed ID"})
                record_unmanaged_id(str(item["id"]), str(item["path"]))
                managed_ids.discard(str(item["id"]))
            else:
                kept.append(item)
        managed = kept

    for plan_id, paths in sorted(unmanaged_ids.items()):
        unique_paths = sorted(set(paths))
        if plan_id in entries or plan_id in managed_ids or unmanaged_id_mentions[plan_id] > 1:
            diagnostics.append(_diagnostic(
                "ambiguous-plan-id",
                f"Unmanaged input mentions active or duplicate ID {plan_id}: {', '.join(unique_paths)}",
                plan_id=plan_id,
                repair="approval-required",
            ))

    code_rules = (
        ("multiple research files", "duplicate-research-id"),
        ("Ambiguous research ID", "ambiguous-research-id"),
        ("duplicate research ID", "duplicate-research-id"),
        ("Research ID ", "duplicate-research-id"),
        ("matching ID field", "metadata-id-mismatch"),
        ("matching Status field", "lifecycle-mismatch"),
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
        ("Research registry mismatch", "research-registry-mismatch"),
        ("registry entry has no file", "stale-research-registry-row"),
        ("Missing RESEARCH.md", "missing-research-registry"),
        ("missing from RESEARCH.md", "missing-research-registry-row"),
        ("RESEARCH.md", "research-registry"),
        ("invalid scope", "research-scope"),
        ("project scope", "research-scope"),
        ("scope ", "research-scope"),
        ("linked plan", "research-link"),
        ("lists source research", "research-link"),
        ("invalid source research", "research-link"),
        ("Research ", "research-contract"),
    )
    validation_errors, base_plans, invalid_ids = validation_state(root, include_research=False)
    research_errors, research_records, research_entries, research_invalid = research_validation_state(
        root, prefixes, entries, base_plans
    )
    validation_errors.extend(research_errors)
    for plan_id, plan in base_plans.items():
        source_ids = plan_research_ids(str(plan["text"]))
        if len(source_ids) != len(set(source_ids)):
            validation_errors.append(f"Plan {plan_id!r} lists source research more than once")
            invalid_ids.add(plan_id)
        malformed_sources = [source_id for source_id in source_ids if not RESEARCH_ID_RE.fullmatch(source_id)]
        if malformed_sources:
            validation_errors.append(
                f"Plan {plan_id!r} has invalid source research ID(s): {', '.join(malformed_sources)}"
            )
            invalid_ids.add(plan_id)
        for research_id in source_ids:
            if research_id not in research_records:
                validation_errors.append(f"Plan {plan_id!r} links unknown research {research_id!r}")
                invalid_ids.add(plan_id)
            elif research_id in research_invalid:
                validation_errors.append(f"Plan {plan_id!r} links invalid research {research_id!r}")
                invalid_ids.add(plan_id)
            elif plan_id not in research_records[research_id]["linked_plans"]:
                validation_errors.append(f"Plan {plan_id!r} is missing backlink in research {research_id!r}")
                invalid_ids.add(plan_id)
    if invalid_ids:
        kept = []
        for item in managed:
            if item["id"] in invalid_ids:
                unmanaged.append({
                    "path": item["path"],
                    "observed_id": item["id"],
                    "reason": "registered plan failed managed-state validation",
                })
                managed_ids.discard(str(item["id"]))
            else:
                kept.append(item)
        managed = kept

    for message in validation_errors:
        code = "contract-violation"
        for fragment, candidate in code_rules:
            if fragment in message:
                code = candidate
                break
        research_match = re.search(r"['\"](RES-[0-9]{3,})['\"]", message)
        if research_match is None:
            research_match = re.search(r"(?:Research(?: ID)?|research(?: ID)?)\s+(RES-[0-9]{3,})", message)
        diagnostics.append(_diagnostic(
            code,
            message,
            research_id=research_match.group(1) if research_match else None,
            repair="approval-required",
        ))

    # Research is a separate namespace in scan output.  Invalid records stay
    # visible as unmanaged input and never become plan IDs or ready work.
    candidate_paths: set[str] = set()
    for research_id, matches in sorted(find_research_candidates(root).items()):
        for match in matches:
            relative = str(Path(str(match["path"])).relative_to(root))
            candidate_paths.add(relative)
            if research_id not in research_records or research_id in research_invalid:
                unmanaged_research.append({
                    "path": relative,
                    "observed_id": research_id,
                    "reason": "research record failed managed-state validation",
                })
            else:
                record = research_records[research_id]
                managed_research.append({
                    "id": research_id,
                    "research_id": research_id,
                    "status": record["status"],
                    "scope": record["scope"],
                    "projects": record["projects"],
                    "linked_plans": record["linked_plans"],
                    "name": record["name"],
                    "path": relative,
                })
    research_dir = root / "research"
    if research_dir.is_dir():
        for path in sorted(research_dir.glob("*.md")):
            relative = str(path.relative_to(root))
            if relative in candidate_paths:
                continue
            text = path.read_text(encoding="utf-8")
            observed = research_metadata_values(text, "ID")
            observed_id = observed[0] if len(observed) == 1 and RESEARCH_ID_RE.fullmatch(observed[0]) else None
            if observed and any(not RESEARCH_ID_RE.fullmatch(value) for value in observed):
                diagnostics.append(_diagnostic(
                    "malformed-research-id",
                    f"Invalid research metadata ID(s): {', '.join(observed)}",
                    path=relative,
                    research_id=observed_id,
                    repair="approval-required",
                ))
            unmanaged_research.append({
                "path": relative,
                **({"observed_id": observed_id} if observed_id else {}),
                "reason": "filename is outside managed research contract",
            })
            diagnostics.append(_diagnostic(
                "unmanaged-research-file",
                "Markdown input is inactive because its filename does not satisfy the managed-research contract",
                path=relative,
                research_id=observed_id,
                severity="warning",
                repair="approval-required",
            ))

    # A raw RES ID is ambiguous for research mutations even when it lives in a
    # non-research Markdown file.  It is intentionally not included in the
    # managed record inventory.
    raw_research_mentions: dict[str, list[str]] = {}
    managed_plan_paths = {str(item["path"]) for item in managed}
    managed_research_paths = {
        str(record["path"])
        for research_id, record in research_records.items()
        if research_id not in research_invalid
    }
    candidate_ids_by_path: dict[str, set[str]] = {}
    for research_id, matches in find_research_candidates(root).items():
        for candidate in matches:
            candidate_path = str(Path(str(candidate["path"])).relative_to(root))
            candidate_ids_by_path.setdefault(candidate_path, set()).add(research_id)
    for path in sorted(root.rglob("*.md")):
        relative_path = str(path.relative_to(root))
        if ".git" in path.relative_to(root).parts or path == root / RESEARCH_REGISTRY:
            continue
        # Only valid managed records are exempt from raw-ID ambiguity checks.
        # A syntactically named but unregistered/invalid file is still raw input
        # and must not be able to smuggle an active RES ID into a mutation.
        if relative_path in managed_plan_paths or relative_path in managed_research_paths:
            continue
        text = path.read_text(encoding="utf-8")
        for value in research_metadata_values(text, "ID") + research_metadata_values(text, "Research"):
            for research_id in split_values(value):
                if not RESEARCH_ID_RE.fullmatch(research_id):
                    continue
                if research_id in candidate_ids_by_path.get(relative_path, set()):
                    continue
                raw_research_mentions.setdefault(research_id, []).append(relative_path)
    for research_id, paths in sorted(raw_research_mentions.items()):
        if research_id in research_entries or len(paths) > 1:
            unique_paths = ", ".join(sorted(set(paths)))
            diagnostics.append(_diagnostic(
                "ambiguous-research-id",
                f"Unmanaged input mentions active or duplicate research ID {research_id}: {unique_paths}",
                research_id=research_id,
                repair="approval-required",
            ))

    return {
        "schema_version": 1,
        "root": str(root),
        "projects": projects,
        "managed_plans": managed,
        "managed_research": sorted(managed_research, key=lambda item: str(item["id"])),
        "unmanaged_files": unmanaged,
        "unmanaged_research": unmanaged_research,
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
        elif item["code"] in {"ambiguous-research-id", "duplicate-research-id"}:
            errors.append(f"ambiguous unmanaged research input: {item['message']}")
    return errors


def validation_state(
    root: Path,
    *,
    include_research: bool = True,
) -> tuple[list[str], dict[str, dict[str, object]], set[str]]:
    """Validate active state and identify registered plans unsafe to expose as managed."""
    errors: list[str] = []
    invalid_ids: set[str] = set()
    if not root.is_dir():
        return [f"Plans root is not a directory: {root}"], {}, invalid_ids
    for required in ("README.md", "AGENTS.md", "ORCHESTRATION.md"):
        if not (root / required).is_file():
            errors.append(f"Missing {required}")

    orchestration_path = root / "ORCHESTRATION.md"
    if orchestration_path.is_file() and STALE_PRIVATE_CLIENT_PATH in orchestration_path.read_text(encoding="utf-8"):
        errors.append(
            "ORCHESTRATION.md references removed private client path 'scripts/planctl.py'; "
            "use the installed planctl wrapper"
        )

    errors_before_registry = len(errors)
    prefixes, entries, retired = parse_registry(orchestration_path, errors)
    registry_invalid = len(errors) != errors_before_registry
    plans = find_plans(root, errors, prefixes, entries)
    candidates = find_plan_candidates(root)
    for plan_id in entries:
        if plan_id not in plans and plan_id in candidates:
            invalid_ids.add(plan_id)
    if registry_invalid:
        invalid_ids.update(plans)

    for plan_id, plan in plans.items():
        entry = entries[plan_id]
        before = len(errors)
        claimed_by = str(entry["claimed_by"])
        claimed_at = str(entry["claimed_at"])
        has_claimant = claimed_by.lower() not in EMPTY_VALUES
        has_time = claimed_at.lower() not in EMPTY_VALUES
        if has_claimant != has_time:
            errors.append(f"Plan {plan_id!r} must set or clear both claim fields")
        if has_claimant and not re.fullmatch(r"[A-Za-z0-9._@:/+-]+", claimed_by):
            errors.append(f"Plan {plan_id!r} has invalid claimant {claimed_by!r}")
        if has_time:
            timestamp_shape = re.fullmatch(
                r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", claimed_at
            )
            try:
                if timestamp_shape is None:
                    raise ValueError
                datetime.strptime(claimed_at, "%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
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
        if len(errors) != before:
            invalid_ids.add(plan_id)

    for plan_id, project in retired.items():
        if plan_id in plans:
            errors.append(f"Retired plan ID has an active plan file: {plan_id}")
            invalid_ids.add(plan_id)
        match = ID_RE.fullmatch(plan_id)
        if match and prefixes.get(match.group(1)) != project:
            errors.append(f"Retired ID prefix/project mismatch for {plan_id!r}")

    for plan_id, entry in entries.items():
        before = len(errors)
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
        if len(errors) != before and plan_id in plans:
            invalid_ids.add(plan_id)

    validate_findings(root, plans, errors, invalid_ids)
    detect_cycles(entries, errors, invalid_ids)

    # A plan cannot be fully managed when any dependency is itself invalid.
    changed = True
    while changed:
        changed = False
        for plan_id, plan in plans.items():
            if plan_id in invalid_ids:
                continue
            invalid_dependencies = [
                str(dependency) for dependency in entries[plan_id]["dependencies"]
                if str(dependency) in invalid_ids
            ]
            if invalid_dependencies:
                errors.append(
                    f"Plan {plan_id!r} depends on invalid managed state: {', '.join(invalid_dependencies)}"
                )
                invalid_ids.add(plan_id)
                changed = True

    if include_research:
        research_errors, research_records, _research_entries, research_invalid = research_validation_state(
            root, prefixes, entries, plans
        )
        errors.extend(research_errors)
        for plan_id, plan in plans.items():
            source_ids = plan_research_ids(str(plan["text"]))
            if len(source_ids) != len(set(source_ids)):
                errors.append(f"Plan {plan_id!r} lists source research more than once")
                invalid_ids.add(plan_id)
            malformed_sources = [source_id for source_id in source_ids if not RESEARCH_ID_RE.fullmatch(source_id)]
            if malformed_sources:
                errors.append(f"Plan {plan_id!r} has invalid source research ID(s): {', '.join(malformed_sources)}")
                invalid_ids.add(plan_id)
            for research_id in source_ids:
                if research_id not in research_records:
                    # Keep the research diagnostic scoped, but a plan that
                    # explicitly declares a missing source is not managed.
                    errors.append(f"Plan {plan_id!r} links unknown research {research_id!r}")
                    invalid_ids.add(plan_id)
                elif research_id in research_invalid:
                    errors.append(f"Plan {plan_id!r} links invalid research {research_id!r}")
                    invalid_ids.add(plan_id)
                elif plan_id not in research_records[research_id]["linked_plans"]:
                    errors.append(f"Plan {plan_id!r} is missing backlink in research {research_id!r}")
                    invalid_ids.add(plan_id)
    return errors, plans, invalid_ids


def validate(root: Path) -> list[str]:
    return validation_state(root)[0]


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
