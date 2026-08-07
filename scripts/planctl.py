#!/usr/bin/env python3
"""Resolve and safely maintain plans by stable ID."""

from __future__ import annotations

import argparse
import fcntl
import importlib
import json
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

sys.dont_write_bytecode = True
validator = importlib.import_module("validate_plans")


class PlanError(RuntimeError):
    pass


class UnknownPlanIDError(PlanError):
    """A required plan ID is not valid managed state in the resolved hub."""

    def __init__(self, message: str, plan_id: str | None = None) -> None:
        super().__init__(message)
        self.plan_id = plan_id


class UnknownResearchIDError(PlanError):
    """A required research ID is not valid managed state in the resolved hub."""


class RootRef(NamedTuple):
    """One plan hub in the effective root set."""

    name: str | None
    path: Path
    source: str  # "config" | "--root"


CONFIG_SCHEMA_VERSION = 1
ROOT_NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
HUB_REQUIRED_FILES = ("ORCHESTRATION.md", "README.md", "AGENTS.md")


def registry_path() -> Path:
    """Return the fixed roots registry path."""
    return Path.home() / ".config" / "plans-hub" / "roots.json"


def load_registry() -> list[RootRef]:
    """Load the roots registry; a broken registry is never silently ignored."""
    path = registry_path()
    if not path.is_file():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise PlanError(f"cannot read roots registry: {path}: {error}") from error
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as error:
        raise PlanError(f"roots registry is not valid JSON: {path}: {error}") from error
    problems: list[str] = []
    roots: list[RootRef] = []
    names: set[str] = set()
    paths: set[Path] = set()
    if not isinstance(document, dict):
        problems.append("top level must be a JSON object")
    else:
        if document.get("schema_version") != CONFIG_SCHEMA_VERSION:
            problems.append(f"schema_version must be {CONFIG_SCHEMA_VERSION}")
        entries = document.get("roots")
        if not isinstance(entries, list):
            problems.append("roots must be a JSON array")
        else:
            for index, entry in enumerate(entries):
                label = f"roots[{index}]"
                if not isinstance(entry, dict):
                    problems.append(f"{label} must be a JSON object")
                    continue
                name = entry.get("name")
                raw_path = entry.get("path")
                ok = True
                if not isinstance(name, str) or not ROOT_NAME_RE.fullmatch(name):
                    problems.append(f"{label}.name must be a lowercase kebab-case string")
                    ok = False
                elif name in names:
                    problems.append(f"{label}.name duplicates root name {name!r}")
                    ok = False
                resolved: Path | None = None
                if not isinstance(raw_path, str) or not raw_path:
                    problems.append(f"{label}.path must be an absolute path string")
                    ok = False
                else:
                    resolved = Path(raw_path).expanduser()
                    if not resolved.is_absolute():
                        problems.append(f"{label}.path must be absolute: {raw_path!r}")
                        ok = False
                    else:
                        resolved = resolved.resolve()
                        if resolved in paths:
                            problems.append(f"{label}.path duplicates root path {raw_path!r}")
                            ok = False
                if not ok or resolved is None:
                    continue
                names.add(str(name))
                paths.add(resolved)
                roots.append(RootRef(str(name), resolved, "config"))
    if problems:
        raise PlanError(f"roots registry is invalid: {path}:\n- " + "\n- ".join(problems))
    return roots


def resolve_command_line_root(value: Path) -> RootRef:
    """Resolve --root as an existing path first, then as a registry root name.

    A value matching neither stays a (possibly not yet created) path, so legacy
    usage such as init into a new directory keeps working byte-for-byte.
    """
    candidate = value.expanduser()
    if candidate.exists():
        return RootRef(None, candidate.resolve(), "--root")
    name = str(value)
    for root in load_registry():
        if root.name == name:
            return RootRef(name, root.path, "--root")
    return RootRef(None, candidate.resolve(), "--root")


def configured_root(command_line_root: Path | None) -> Path:
    """Resolve storage independently from the client checkout.

    Precedence: --root PATH|NAME > a single-root registry. A multi-root
    registry never guesses a hub for single-hub commands. There is
    deliberately no fallback to the source checkout: this client is software,
    not plan data.
    """
    if command_line_root is not None:
        return resolve_command_line_root(command_line_root).path
    registry = load_registry()
    if len(registry) == 1:
        return registry[0].path
    if len(registry) > 1:
        names = ", ".join(str(root.name) for root in registry)
        raise PlanError(f"multiple plan hubs configured ({names}); pass --root NAME")
    raise PlanError(
        "no plan hub selected; pass --root PATH|NAME or create a roots registry "
        f"at {registry_path()}"
    )


def effective_roots(command_line_root: Path | None) -> tuple[list[RootRef], list[str]]:
    """Resolve the root set for roots/locate without requiring a single hub."""
    if command_line_root is not None:
        return [resolve_command_line_root(command_line_root)], []
    return load_registry(), []


def multi_root_registry() -> bool:
    try:
        return len(load_registry()) > 1
    except PlanError:
        return False


def root_validity(path: Path) -> str:
    if not path.is_dir():
        return "missing-path"
    if all((path / required).is_file() for required in HUB_REQUIRED_FILES):
        return "ok"
    return "not-a-hub"


def normalize_id(value: str) -> str:
    plan_id = value.upper()
    if not validator.ID_RE.fullmatch(plan_id):
        raise PlanError(f"Invalid plan ID: {value!r}")
    return plan_id


def normalize_research_id(value: str) -> str:
    research_id = value.upper()
    if not validator.RESEARCH_ID_RE.fullmatch(research_id):
        raise PlanError(f"Invalid research ID: {value!r}")
    return research_id


def flatten_research_ids(values: object) -> list[str]:
    """Normalize repeated/nargs research options without making a new ID namespace."""
    if values is None:
        return []
    if isinstance(values, str):
        raw_values = [values]
    else:
        raw_values = []
        for value in values:  # type: ignore[union-attr]
            if isinstance(value, (list, tuple)):
                raw_values.extend(str(item) for item in value)
            else:
                raw_values.append(str(value))
    normalized: list[str] = []
    for raw in raw_values:
        for value in raw.split(","):
            if value.strip():
                normalized.append(normalize_research_id(value.strip()))
    if len(normalized) != len(set(normalized)):
        raise PlanError("Research IDs must be unique")
    return normalized


def empty(value: str) -> bool:
    return value.lower() in validator.EMPTY_VALUES


def validate_repository_or_raise(root: Path) -> None:
    errors = validator.validate(root)
    if errors:
        raise PlanError("Repository validation failed:\n- " + "\n- ".join(errors))


def validate_or_raise(
    root: Path, *required_ids: str, include_research: bool = True
) -> None:
    """Block only on global ambiguity or state required by this operation."""
    errors: list[str] = []
    for required in ("README.md", "AGENTS.md", "ORCHESTRATION.md"):
        if not (root / required).is_file():
            errors.append(f"Missing {required}")
    registry_errors: list[str] = []
    prefixes: dict[str, str] = {}
    entries: dict[str, dict[str, object]] = {}
    if (root / "ORCHESTRATION.md").is_file():
        prefixes, entries, _ = validator.parse_registry(root / "ORCHESTRATION.md", registry_errors)
    errors.extend(registry_errors)
    report = validator.scan(root)
    for item in report["diagnostics"]:
        if item["code"] == "ambiguous-plan-id":
            errors.append(f"ambiguous unmanaged input: {item['message']}")
    validation_errors, base_plans, invalid_ids = validator.validation_state(root, include_research=False)
    managed_ids = set(base_plans) - invalid_ids
    unknown = [plan_id for plan_id in required_ids if plan_id not in managed_ids]
    for plan_id in required_ids:
        if plan_id not in managed_ids:
            relevant = [message for message in validation_errors if plan_id in message]
            errors.extend(relevant or [f"Plan {plan_id!r} is not valid managed state"])
    # Research is scoped out for unrelated plan operations, but an operation
    # on a plan that explicitly names research must still verify that source
    # and its backlink are valid.  Link operations opt out here so they can
    # establish a missing backlink transactionally.
    if include_research:
        for plan_id in required_ids:
            plan = base_plans.get(plan_id)
            source_ids = validator.plan_research_ids(str(plan["text"])) if plan else []
            if not source_ids:
                continue
            if len(source_ids) != len(set(source_ids)):
                errors.append(f"Plan {plan_id!r} lists source research more than once")
            malformed_sources = [source_id for source_id in source_ids if not validator.RESEARCH_ID_RE.fullmatch(source_id)]
            if malformed_sources:
                errors.append(
                    f"Plan {plan_id!r} has invalid source research ID(s): {', '.join(malformed_sources)}"
                )
            research_errors, research_records, _research_entries, research_invalid = validator.research_validation_state(
                root, prefixes, entries, base_plans
            )
            for research_id in source_ids:
                if research_id not in research_records or research_id in research_invalid:
                    relevant = [message for message in research_errors if research_id in message or plan_id in message]
                    errors.extend(relevant or [f"Plan {plan_id!r} links invalid research {research_id!r}"])
                elif plan_id not in research_records[research_id]["linked_plans"]:
                    errors.append(f"Plan {plan_id!r} is missing backlink in research {research_id!r}")
    if errors:
        message = "Repository validation failed:\n- " + "\n- ".join(dict.fromkeys(errors))
        if unknown:
            raise UnknownPlanIDError(message, plan_id=unknown[0])
        raise PlanError(message)


def research_contract(
    root: Path,
    required_ids: list[str] | None = None,
    *,
    block_unrelated_errors: bool = False,
    allow_plan_id: str | None = None,
) -> tuple[
    dict[str, dict[str, object]],
    dict[str, dict[str, object]],
    set[str],
]:
    """Load research records while keeping unrelated plan state out of scope."""
    required_ids = required_ids or []
    errors: list[str] = []
    for required in ("README.md", "AGENTS.md", "ORCHESTRATION.md"):
        if not (root / required).is_file():
            errors.append(f"Missing {required}")
    registry_errors: list[str] = []
    prefixes, entries, _ = validator.parse_registry(root / "ORCHESTRATION.md", registry_errors)
    errors.extend(registry_errors)
    _plan_errors, plan_entries, plan_invalid = validator.validation_state(root, include_research=False)
    plans = {plan_id: plan for plan_id, plan in plan_entries.items() if plan_id not in plan_invalid}
    research_errors, records, registry_entries, invalid_ids = validator.research_validation_state(
        root, prefixes, entries, plans
    )
    report = validator.scan(root)
    allowed_plan_path: str | None = None
    if allow_plan_id and allow_plan_id in plans:
        allowed_plan_path = str(Path(str(plans[allow_plan_id]["path"])).relative_to(root))
    for item in report["diagnostics"]:
        if item["code"] in {"ambiguous-research-id", "duplicate-research-id"}:
            message = str(item["message"])
            if allowed_plan_path and allowed_plan_path in message:
                _, separator, mentions = message.partition(": ")
                mentioned_paths = {part.strip() for part in mentions.split(",")} if separator else set()
                if mentioned_paths == {allowed_plan_path}:
                    continue
            errors.append(f"ambiguous unmanaged research input: {message}")
    if block_unrelated_errors:
        errors.extend(research_errors)
    else:
        for research_id in required_ids:
            relevant = [
                message for message in research_errors
                if research_id in message
                and not (
                    allow_plan_id
                    and f"Plan {allow_plan_id!r} is missing backlink" in message
                )
            ]
            if relevant:
                errors.extend(relevant)
            elif research_id not in records or research_id in invalid_ids:
                errors.append(f"Research {research_id!r} is not valid managed state")
    if errors:
        raise PlanError("Research repository validation failed:\n- " + "\n- ".join(dict.fromkeys(errors)))
    return records, registry_entries, invalid_ids


def load(
    root: Path,
) -> tuple[dict[str, str], dict[str, dict[str, object]], dict[str, str], dict[str, dict[str, object]]]:
    errors: list[str] = []
    prefixes, entries, retired = validator.parse_registry(root / "ORCHESTRATION.md", errors)
    if errors:
        raise PlanError("Cannot read plan registry:\n- " + "\n- ".join(errors))
    _, base_plans, invalid_ids = validator.validation_state(root, include_research=False)
    plans = {plan_id: plan for plan_id, plan in base_plans.items() if plan_id not in invalid_ids}
    return prefixes, entries, retired, plans


def lock_path(root: Path) -> Path:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--git-common-dir"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        git_common_dir = Path(result.stdout.strip())
        if not git_common_dir.is_absolute():
            git_common_dir = root / git_common_dir
        return git_common_dir / "planctl.lock"
    return root / ".planctl.lock"


@contextmanager
def repository_lock(root: Path):
    path = lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield


def atomic_write(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def research_registry_template() -> str:
    template = Path(__file__).resolve().parent.parent / "templates" / "hub" / "RESEARCH.md"
    if template.is_file():
        return template.read_text(encoding="utf-8")
    return (
        "# Research Registry\n\n"
        "## Managed research records\n\n"
        "| ID | Status | Scope | Projects | Linked plans | Path |\n"
        "| --- | --- | --- | --- | --- | --- |\n\n"
        "## Retired research IDs\n\n"
        "| ID |\n| --- |\n"
    )


def ensure_research_registry(root: Path) -> str:
    path = root / validator.RESEARCH_REGISTRY
    if path.is_file():
        return path.read_text(encoding="utf-8")
    text = research_registry_template()
    atomic_write(path, text if text.endswith("\n") else text + "\n")
    return text


def research_registry_row_index(lines: list[str], research_id: str) -> int:
    matches: list[int] = []
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            continue
        cells = [validator.clean_cell(cell) for cell in line.strip().strip("|").split("|")]
        if cells and cells[0] == research_id:
            matches.append(index)
    if len(matches) != 1:
        raise PlanError(f"Expected exactly one research registry row for {research_id}, found {len(matches)}")
    return matches[0]


def insert_research_registry_row(root: Path, row: str) -> None:
    path = root / validator.RESEARCH_REGISTRY
    lines = path.read_text(encoding="utf-8").splitlines()
    heading_index = next(
        (index for index, line in enumerate(lines)
         if line.strip().lower() in {
             "## managed research records", "## managed research", "## research records", "## research registry", "## research"
         }),
        None,
    )
    if heading_index is None:
        raise PlanError("RESEARCH.md is missing the managed research records section")
    index = heading_index + 1
    while index < len(lines) and not lines[index].startswith("|"):
        index += 1
    while index < len(lines) and lines[index].startswith("|"):
        index += 1
    lines.insert(index, row)
    atomic_write(path, "\n".join(lines) + "\n")


def update_research_registry_row(root: Path, research_id: str, values: dict[str, object]) -> None:
    path = root / validator.RESEARCH_REGISTRY
    lines = path.read_text(encoding="utf-8").splitlines()
    index = research_registry_row_index(lines, research_id)
    projects = [str(item) for item in values.get("projects", [])]
    linked_plans = [str(item) for item in values.get("linked_plans", [])]
    cells = [
        f"`{research_id}`",
        f"`{values['status']}`",
        f"`{values['scope']}`",
        "<br>".join(f"`{item}`" for item in projects) or "—",
        "<br>".join(f"`{item}`" for item in linked_plans) or "—",
        f"`{values['path']}`",
    ]
    lines[index] = "| " + " | ".join(cells) + " |"
    atomic_write(path, "\n".join(lines) + "\n")


def research_metadata_replace(text: str, field: str, value: str) -> str:
    aliases = {
        "Linked Plans": r"Linked Plans|Linked plans|Plans",
        "ID": r"ID|Research ID",
        "Status": r"Status",
        "Path": r"Path",
        "Scope": r"Scope",
        "Projects": r"Projects|Project",
    }
    pattern = aliases.get(field, re.escape(field))
    updated, count = re.subn(rf"(?m)^({pattern}):\s*.*$", rf"\1: {value}", text)
    if count != 1:
        raise PlanError(f"Expected exactly one {field} field in research record")
    return updated


def research_file_update(
    root: Path,
    record: dict[str, object],
    *,
    status: str | None = None,
    linked_plans: list[str] | None = None,
) -> tuple[Path, Path, str]:
    source = root / str(record["path"])
    if not source.is_file():
        raise PlanError(f"Research record file is missing: {source}")
    current_status = str(record["status"])
    target_status = status or current_status
    target = source.with_name(source.name.replace(f"{current_status}--", f"{target_status}--", 1))
    text = source.read_text(encoding="utf-8")
    text = research_metadata_replace(text, "Status", target_status)
    text = research_metadata_replace(text, "Path", f"research/{target.name}")
    if linked_plans is not None:
        linked_value = ", ".join(linked_plans) if linked_plans else "—"
        text = research_metadata_replace(text, "Linked Plans", linked_value)
    return source, target, text


def add_plan_research_sources(text: str, research_ids: list[str]) -> str:
    existing = validator.plan_research_ids(text)
    combined = list(existing)
    for research_id in research_ids:
        if research_id not in combined:
            combined.append(research_id)
    if not combined:
        return text
    value = ", ".join(combined)
    matches = re.findall(r"(?m)^(?:Research(?: IDs)?|Source Research):\s*.*$", text)
    if matches:
        updated, count = re.subn(r"(?m)^(?:Research(?: IDs)?|Source Research):\s*.*$", f"Research: {value}", text, count=1)
        if count != 1:
            raise PlanError("Expected exactly one Research field in plan")
        return updated
    status_match = re.search(r"(?m)^Status:\s*.*$", text)
    if status_match is None:
        raise PlanError("Plan is missing Status metadata")
    return text[:status_match.end()] + f"\nResearch: {value}" + text[status_match.end():]


def graph_row_index(lines: list[str], plan_id: str) -> int:
    prefix = f"| `{plan_id}` |"
    matches = [index for index, line in enumerate(lines) if line.startswith(prefix)]
    if len(matches) != 1:
        raise PlanError(f"Expected exactly one orchestration row for {plan_id}, found {len(matches)}")
    return matches[0]


def update_graph_row(root: Path, plan_id: str, column: int, value: str) -> None:
    path = root / "ORCHESTRATION.md"
    lines = path.read_text(encoding="utf-8").splitlines()
    index = graph_row_index(lines, plan_id)
    cells = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
    if len(cells) != 5:
        raise PlanError(f"Malformed orchestration row for {plan_id}")
    cells[column] = value
    lines[index] = "| " + " | ".join(cells) + " |"
    atomic_write(path, "\n".join(lines) + "\n")


def insert_graph_row(root: Path, row: str) -> None:
    path = root / "ORCHESTRATION.md"
    lines = path.read_text(encoding="utf-8").splitlines()
    heading = lines.index("## Plan dependency graph")
    index = heading + 1
    while index < len(lines) and not lines[index].startswith("|"):
        index += 1
    while index < len(lines) and lines[index].startswith("|"):
        index += 1
    lines.insert(index, row)
    atomic_write(path, "\n".join(lines) + "\n")


def readiness(plan_id: str, entries: dict[str, dict[str, object]], plans: dict[str, dict[str, object]]) -> list[str]:
    if plan_id not in plans:
        return ["plan file does not exist"]
    if plan_id not in entries:
        return ["orchestration entry does not exist"]
    reasons: list[str] = []
    plan = plans[plan_id]
    entry = entries[plan_id]
    if plan["status"] != "ready":
        reasons.append(f"status is {plan['status']}, not ready")
    if not empty(str(entry["claimed_by"])):
        reasons.append(f"claimed by {entry['claimed_by']}")
    for dependency in entry["dependencies"]:
        dependency_plan = plans.get(str(dependency))
        if dependency_plan is None:
            reasons.append(f"dependency {dependency} is missing")
        elif dependency_plan["status"] != "done":
            reasons.append(f"dependency {dependency} is {dependency_plan['status']}, not done")
    return reasons


def command_init(root: Path, _args: argparse.Namespace) -> None:
    templates = Path(__file__).resolve().parent.parent / "templates" / "hub"
    if not templates.is_dir():
        raise PlanError(f"hub templates are missing: {templates}")
    if root.exists() and any(root.iterdir()):
        raise PlanError(f"refusing to initialize non-empty directory: {root}")
    root.mkdir(parents=True, exist_ok=True)
    for source in sorted(templates.iterdir()):
        if source.is_file():
            shutil.copyfile(source, root / source.name)
    validate_repository_or_raise(root)
    print(f"Initialized plan hub: {root}")


def command_validate(root: Path, _args: argparse.Namespace) -> None:
    validate_repository_or_raise(root)
    print(f"Plan storage validation passed: {root}")


def command_roots(roots: list[RootRef], warnings: list[str], args: argparse.Namespace) -> None:
    entries = [
        {"name": root.name, "path": str(root.path), "source": root.source, "validity": root_validity(root.path)}
        for root in roots
    ]
    if args.json:
        print(json.dumps({"schema_version": 1, "roots": entries, "warnings": warnings}, indent=2, sort_keys=True))
    else:
        if not entries:
            print(f"No plan hubs configured; create a roots registry at {registry_path()}")
        for entry in entries:
            print(f"{entry['name'] or '—'}\t{entry['path']}\t{entry['source']}\t{entry['validity']}")
    for warning in warnings:
        print(f"planctl: warning: {warning}", file=sys.stderr)


def command_locate(roots: list[RootRef], warnings: list[str], args: argparse.Namespace) -> None:
    """Read-only cross-root lookup; every outcome is a result, never a failure."""
    plan_id = normalize_id(args.id)
    warnings = list(warnings)
    hits: list[dict[str, object]] = []
    searched: list[dict[str, object]] = []
    for root in roots:
        label = root.name or str(root.path)
        validity = root_validity(root.path)
        if validity != "ok":
            warnings.append(f"skipped root {label}: {validity} ({root.path})")
            continue
        report = validator.scan(root.path)
        searched.append({"name": root.name, "path": str(root.path), "source": root.source})
        for item in report["diagnostics"]:
            if item["code"] == "ambiguous-plan-id" and item.get("plan_id") == plan_id:
                warnings.append(f"{label}: [{item['code']}] {item['message']}")
        for item in report["managed_plans"]:
            if item["id"] == plan_id:
                hits.append({
                    "root": root.name,
                    "hub": str(root.path),
                    "project": item["project"],
                    "status": item["status"],
                    "plan_path": str(root.path / str(item["path"])),
                })
    if len(hits) == 1:
        result = "unique"
    elif hits:
        result = "ambiguous"
    else:
        result = "not-found"
    if args.json:
        payload = {
            "schema_version": 1,
            "id": plan_id,
            "result": result,
            "hits": hits,
            "searched": searched,
            "warnings": warnings,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"Result: {result}")
        print(f"ID: {plan_id}")
        if result == "unique":
            hit = hits[0]
            print(f"Hub: {hit['root'] or '—'} ({hit['hub']})")
            print(f"Project: {hit['project']}")
            print(f"Status: {hit['status']}")
            print(f"Path: {hit['plan_path']}")
        elif result == "ambiguous":
            print("Hits:")
            for hit in hits:
                print(f"- {hit['root'] or '—'} ({hit['hub']}): {hit['project']} {hit['status']} {hit['plan_path']}")
        else:
            names = ", ".join(str(entry["name"] or entry["path"]) for entry in searched) or "none"
            print(f"Searched hubs: {names}")
    for warning in warnings:
        print(f"planctl: warning: {warning}", file=sys.stderr)


def command_scan(root: Path, args: argparse.Namespace) -> None:
    report = validator.scan(root)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return
    print(f"Plan datastore scan: {root}")
    print(f"Projects: {len(report['projects'])}")
    print(f"Managed plans: {len(report['managed_plans'])}")
    print(f"Managed research: {len(report.get('managed_research', []))}")
    print(f"Unmanaged files: {len(report['unmanaged_files'])}")
    print(f"Unmanaged research: {len(report.get('unmanaged_research', []))}")
    print(f"Diagnostics: {len(report['diagnostics'])}")
    for item in report["diagnostics"]:
        location = f" ({item['path']})" if "path" in item else ""
        identifier = item.get("research_id") or item.get("plan_id")
        label = f" [{identifier}]" if identifier else ""
        print(f"- [{item['severity']}] {item['code']}{label}{location}: {item['message']}")


def repair_proposals(root: Path, report: dict[str, object]) -> list[dict[str, object]]:
    proposals: list[dict[str, object]] = []
    registry_errors: list[str] = []
    _, entries, _ = validator.parse_registry(root / "ORCHESTRATION.md", registry_errors)
    for item in report["unmanaged_files"]:
        proposal = item.get("repair_proposal")
        if proposal:
            proposal = dict(proposal)
            target_match = validator.STATUS_RE.fullmatch(Path(str(proposal["to"])).name)
            patch_lines = [f"rename {proposal['from']} => {proposal['to']}"]
            if target_match and target_match.group(2) not in entries:
                proposal["registration_required"] = True
                proposal["reason"] += "; orchestration registration requires explicit approval"
            proposal["patch"] = "\n".join(patch_lines)
            proposals.append(proposal)
    for item in report["diagnostics"]:
        if item["code"].startswith("research") or item["code"] in {
            "ambiguous-research-id", "duplicate-research-id", "unmanaged-research-file",
        }:
            # Research diagnostics are intentionally scoped out of plan repair;
            # the explicit research namespace owns its own mutations.
            continue
        has_file_proposal = any(proposal.get("from") == item.get("path") for proposal in proposals)
        if item["code"] == "unmanaged-plan-file" and has_file_proposal:
            continue
        proposals.append({
            "kind": "manual-review",
            "classification": item["repair_classification"],
            "diagnostic_code": item["code"],
            "reason": item["message"],
        })
    return proposals


def command_repair(root: Path, args: argparse.Namespace) -> None:
    report = validator.scan(root)
    proposals = repair_proposals(root, report)
    if args.llm:
        payload = {
            "schema_version": 1,
            "mode": "llm-proposal-handoff",
            "review_required": True,
            "may_apply": False,
            "may_commit_or_push": False,
            "prohibited_changes": [
                "silent ID assignment or renumbering",
                "lifecycle, dependency, or claim changes",
                "deletion, commit, push, or conflict resolution",
            ],
            "diagnostics": report["diagnostics"],
            "instruction": "Propose an inspectable patch only; a user or host must review and apply it explicitly.",
        }
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print("LLM repair handoff (proposal only; review required)")
            print(json.dumps(payload, indent=2, sort_keys=True))
        return

    applied = False
    if args.apply:
        unsafe = [item for item in proposals if item["classification"] != "automatic-safe"]
        if unsafe:
            raise PlanError("refusing --apply because approval-required or unsupported diagnostics remain")
        original_orchestration = (root / "ORCHESTRATION.md").read_text(encoding="utf-8")
        moved: list[tuple[Path, Path]] = []
        try:
            for proposal in proposals:
                if proposal["kind"] != "rename":
                    continue
                source = root / str(proposal["from"])
                target = root / str(proposal["to"])
                source.replace(target)
                moved.append((source, target))
            validate_or_raise(root)
            applied = True
        except Exception:
            atomic_write(root / "ORCHESTRATION.md", original_orchestration)
            for source, target in reversed(moved):
                if target.exists():
                    target.replace(source)
            raise

    payload = {"schema_version": 1, "dry_run": not args.apply, "applied": applied, "proposals": proposals}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print("Applied automatic-safe repairs" if applied else "Repair proposals (dry-run; no files changed)")
        for proposal in proposals:
            print(f"- [{proposal['classification']}] {proposal['kind']}: {proposal['reason']}")


def command_show(root: Path, args: argparse.Namespace) -> None:
    plan_id = normalize_id(args.id)
    validate_or_raise(root, plan_id)
    _, entries, _, plans = load(root)
    if plan_id not in plans:
        raise UnknownPlanIDError(f"Unknown plan ID: {plan_id}", plan_id=plan_id)
    plan = plans[plan_id]
    entry = entries[plan_id]
    dependencies = ", ".join(entry["dependencies"]) or "—"
    claim = "—" if empty(str(entry["claimed_by"])) else f"{entry['claimed_by']} at {entry['claimed_at']}"
    print(f"ID: {plan_id}")
    print(f"Project: {plan['project']}")
    print(f"Status: {plan['status']}")
    print(f"Path: {Path(plan['path']).resolve()}")
    print(f"Depends on: {dependencies}")
    print(f"Claim: {claim}")
    print("\n--- Plan ---\n")
    print(str(plan["text"]).rstrip())


def command_ready(root: Path, args: argparse.Namespace) -> None:
    plan_id = normalize_id(args.id)
    validate_or_raise(root, plan_id)
    _, entries, _, plans = load(root)
    reasons = readiness(plan_id, entries, plans)
    if reasons:
        raise PlanError(f"{plan_id} is not ready to start:\n- " + "\n- ".join(reasons))
    print(f"{plan_id} is ready to start")


def command_list_ready(root: Path, _args: argparse.Namespace) -> None:
    validate_or_raise(root)
    _, entries, _, plans = load(root)
    ready_ids = [plan_id for plan_id in sorted(plans) if not readiness(plan_id, entries, plans)]
    if not ready_ids:
        print("No plans are ready to start")
        return
    for plan_id in ready_ids:
        print(f"{plan_id}\t{plans[plan_id]['project']}\t{plans[plan_id]['path'].name}")


def _research_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise PlanError("Research title/name must produce a lowercase kebab-case slug")
    return slug


def _research_title(value: str) -> str:
    title = value.strip()
    if not title or "\n" in title or "\r" in title:
        raise PlanError("Research title must be a non-empty single line")
    return title


def _research_allocate_values(root: Path, args: argparse.Namespace) -> tuple[str, list[str], str, str]:
    values = list(getattr(args, "values", []) or [])
    scope = (getattr(args, "scope_option", None) or "").strip().lower()
    if not scope and values and values[0].lower() in {"project", "cross-project", "unknown", "unscoped"}:
        scope = values.pop(0).lower()
    if scope == "unscoped":
        scope = "unknown"
    if scope not in {"project", "cross-project", "unknown"}:
        raise PlanError("Research scope is required; choose project, cross-project, or unknown")

    projects: list[str] = []
    for item in getattr(args, "project", []) or []:
        projects.extend(part.strip() for part in str(item).split(",") if part.strip())
    if scope == "project" and not projects and values and (
        len(values) >= 2 or getattr(args, "title", None) or getattr(args, "name", None)
    ):
        projects.append(values.pop(0))
    if scope != "project" and projects:
        raise PlanError(f"Research scope {scope!r} must not include a project")
    if scope == "project" and len(set(projects)) != len(projects):
        raise PlanError("Research projects must be unique")
    if scope == "project" and len(projects) != 1:
        raise PlanError("Project-scoped research requires exactly one explicit project")

    name = getattr(args, "name", None) or (" ".join(values) if values else "")
    values.clear()
    title = _research_title(getattr(args, "title", None) or name.replace("-", " ").capitalize())
    if not name:
        name = title
    slug = _research_slug(name)
    return scope, projects, slug, title


def command_research_allocate(root: Path, args: argparse.Namespace) -> None:
    with repository_lock(root):
        # Research allocation is allowed on an otherwise usable hub even when
        # unrelated research input is awaiting review.  Registry syntax and
        # ambiguous IDs are still safety blockers in research_contract.
        records, registry_entries, _ = research_contract(root, [])
        errors: list[str] = []
        prefixes, _entries, _retired_plans = validator.parse_registry(root / "ORCHESTRATION.md", errors)
        if errors:
            raise PlanError("Cannot read plan registry:\n- " + "\n- ".join(errors))
        scope, projects, slug, title = _research_allocate_values(root, args)
        if scope == "project" and projects[0] not in set(prefixes.values()):
            raise PlanError(f"Unknown project folder for research scope: {projects[0]!r}")

        registry_errors: list[str] = []
        _parsed_registry_entries, retired = validator.parse_research_registry(root / validator.RESEARCH_REGISTRY, registry_errors)
        if registry_errors and (root / validator.RESEARCH_REGISTRY).exists():
            raise PlanError("Cannot read research registry:\n- " + "\n- ".join(registry_errors))
        all_ids = set(records) | set(registry_entries) | set(retired)
        for matches in validator.find_research_candidates(root).values():
            all_ids.update(str(item["id"]) for item in matches)
        number = max(
            (int(research_id.split("-", 1)[1]) for research_id in all_ids if validator.RESEARCH_ID_RE.fullmatch(research_id)),
            default=0,
        ) + 1
        research_id = f"RES-{number:03d}"
        research_dir = root / "research"
        research_dir_existed = research_dir.is_dir()
        research_dir.mkdir(parents=True, exist_ok=True)
        path = research_dir / f"open--{research_id}--{slug}.md"
        registry_path = root / validator.RESEARCH_REGISTRY
        original_registry = registry_path.read_text(encoding="utf-8") if registry_path.is_file() else None
        if path.exists():
            raise PlanError(f"Research path already exists: {path}")
        projects_value = projects[0] if projects else "—"
        relative_path = f"research/{path.name}"
        try:
            ensure_research_registry(root)
            path.write_text(
                f"# {title}\n\n"
                f"ID: {research_id}\n"
                "Status: open\n"
                f"Scope: {scope}\n"
                f"Projects: {projects_value}\n"
                "Linked Plans: —\n"
                f"Path: {relative_path}\n",
                encoding="utf-8",
            )
            insert_research_registry_row(
                root,
                f"| `{research_id}` | `open` | `{scope}` | "
                f"{projects_value if projects else '—'} | — | `{relative_path}` |",
            )
            research_contract(root, [research_id])
        except Exception:
            path.unlink(missing_ok=True)
            if original_registry is None:
                registry_path.unlink(missing_ok=True)
            else:
                atomic_write(registry_path, original_registry)
            if not research_dir_existed and research_dir.is_dir() and not any(research_dir.iterdir()):
                research_dir.rmdir()
            raise
    if getattr(args, "json", False):
        print(json.dumps({
            "schema_version": 1,
            "id": research_id,
            "research_id": research_id,
            "status": "open",
            "scope": scope,
            "projects": projects,
            "path": str(path.resolve()),
        }, indent=2, sort_keys=True))
    else:
        print(research_id)
        print(path.resolve())


def command_research_show(root: Path, args: argparse.Namespace) -> None:
    research_id = normalize_research_id(args.id)
    records, _registry_entries, _invalid = research_contract(root, [research_id])
    if research_id not in records:
        raise UnknownResearchIDError(f"Unknown research ID: {research_id}")
    record = records[research_id]
    path = root / str(record["path"])
    if getattr(args, "json", False):
        print(json.dumps({
            "schema_version": 1,
            "id": research_id,
            "research_id": research_id,
            "status": record["status"],
            "scope": record["scope"],
            "projects": record["projects"],
            "linked_plans": record["linked_plans"],
            "path": str(path.resolve()),
            "text": record["text"],
        }, indent=2, sort_keys=True))
        return
    print(f"ID: {research_id}")
    print(f"Status: {record['status']}")
    print(f"Scope: {record['scope']}")
    print(f"Projects: {', '.join(record['projects']) or '—'}")
    print(f"Linked plans: {', '.join(record['linked_plans']) or '—'}")
    print(f"Path: {path.resolve()}")
    print("\n--- Research ---\n")
    print(str(record["text"]).rstrip())


def command_research_status(root: Path, args: argparse.Namespace) -> None:
    research_id = normalize_research_id(args.id)
    target = args.status
    transitions = {
        "open": {"converted", "cancelled"},
        "converted": {"archived"},
        "cancelled": {"archived"},
        "archived": set(),
    }
    with repository_lock(root):
        records, _registry_entries, _invalid = research_contract(root, [research_id])
        if research_id not in records:
            raise UnknownResearchIDError(f"Unknown research ID: {research_id}")
        record = records[research_id]
        current = str(record["status"])
        linked_plans = [str(item) for item in record["linked_plans"]]
        if target == "cancelled" and linked_plans:
            raise PlanError("Cannot cancel research that is linked to a plan")
        if target not in transitions[current]:
            raise PlanError(f"Unsupported research status transition: {current} -> {target}")
        if target == "converted" and not linked_plans:
            raise PlanError("Converted research must be linked to at least one plan")
        if target == "cancelled" and linked_plans:
            raise PlanError("Cannot cancel research that is linked to a plan")
        source, target_path, updated_text = research_file_update(
            root, record, status=target, linked_plans=linked_plans
        )
        if target_path.exists():
            raise PlanError(f"Target research status file already exists: {target_path.relative_to(root)}")
        registry_path = root / validator.RESEARCH_REGISTRY
        original_registry = registry_path.read_text(encoding="utf-8")
        original_text = source.read_text(encoding="utf-8")
        updated_record = dict(record)
        updated_record["status"] = target
        updated_record["path"] = str(target_path.relative_to(root))
        try:
            atomic_write(target_path, updated_text)
            source.unlink()
            update_research_registry_row(root, research_id, updated_record)
            research_contract(root, [research_id])
        except Exception:
            atomic_write(source, original_text)
            if target_path != source:
                target_path.unlink(missing_ok=True)
            atomic_write(registry_path, original_registry)
            raise
    if getattr(args, "json", False):
        print(json.dumps({
            "schema_version": 1,
            "id": research_id,
            "research_id": research_id,
            "from_status": current,
            "status": target,
            "path": str(target_path.resolve()),
        }, indent=2, sort_keys=True))
    else:
        print(f"Moved {research_id}: {current} -> {target}")
        print(target_path.resolve())


def command_research_link(root: Path, args: argparse.Namespace) -> None:
    try:
        research_id = normalize_research_id(args.research_id)
        plan_id = normalize_id(args.plan_id)
    except PlanError:
        # Accept the equally readable plan-first spelling for compatibility
        # with clients that model this as ``link PLAN RESEARCH``.
        research_id = normalize_research_id(args.plan_id)
        plan_id = normalize_id(args.research_id)
    with repository_lock(root):
        validate_or_raise(root, plan_id, include_research=False)
        _prefixes, _entries, _retired, plans = load(root)
        records, _registry_entries, _invalid = research_contract(
            root, [research_id], allow_plan_id=plan_id
        )
        if research_id not in records:
            raise UnknownResearchIDError(f"Unknown research ID: {research_id}")
        record = records[research_id]
        if record["status"] in {"cancelled", "archived"}:
            raise PlanError(f"Cannot link plan to research in status {record['status']!r}")
        plan = plans.get(plan_id)
        if plan is None:
            raise UnknownPlanIDError(f"Unknown plan ID: {plan_id}", plan_id=plan_id)
        if record["scope"] == "project" and str(plan["project"]) not in record["projects"]:
            raise PlanError(
                f"Project-scoped research {research_id} belongs to {record['projects'][0]!r}, "
                f"not plan project {plan['project']!r}"
            )
        linked_plans = [str(item) for item in record["linked_plans"]]
        if plan_id not in linked_plans:
            linked_plans.append(plan_id)
        new_status = "converted" if str(record["status"]) == "open" else str(record["status"])
        source, target_path, updated_research = research_file_update(
            root, record, status=new_status, linked_plans=linked_plans
        )
        if target_path != source and target_path.exists():
            raise PlanError(f"Target research path already exists: {target_path.relative_to(root)}")
        plan_path = Path(str(plan["path"]))
        original_plan = plan_path.read_text(encoding="utf-8")
        original_research = source.read_text(encoding="utf-8")
        registry_path = root / validator.RESEARCH_REGISTRY
        original_registry = registry_path.read_text(encoding="utf-8")
        updated_plan = add_plan_research_sources(original_plan, [research_id])
        updated_record = dict(record)
        updated_record["status"] = new_status
        updated_record["linked_plans"] = linked_plans
        updated_record["path"] = str(target_path.relative_to(root))
        try:
            atomic_write(plan_path, updated_plan)
            atomic_write(target_path, updated_research)
            if target_path != source:
                source.unlink()
            update_research_registry_row(root, research_id, updated_record)
            validate_or_raise(root, plan_id)
            research_contract(root, [research_id])
        except Exception:
            atomic_write(plan_path, original_plan)
            if target_path != source:
                target_path.unlink(missing_ok=True)
            atomic_write(source, original_research)
            atomic_write(registry_path, original_registry)
            raise
    if getattr(args, "json", False):
        print(json.dumps({
            "schema_version": 1,
            "research_id": research_id,
            "plan_id": plan_id,
            "research_path": str((root / str(updated_record["path"])).resolve()),
            "plan_path": str(plan_path.resolve()),
            "status": updated_record["status"],
        }, indent=2, sort_keys=True))
    else:
        print(f"Linked {research_id} to {plan_id}")
        print(f"Research path: {(root / str(updated_record['path'])).resolve()}")
        print(f"Plan path: {plan_path.resolve()}")


def command_allocate(root: Path, args: argparse.Namespace) -> None:
    prefix = args.prefix.upper()
    name = args.plan_name.lower()
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        raise PlanError("Plan name must use lowercase kebab-case")
    source_ids = flatten_research_ids(getattr(args, "from_research", []))
    converted_paths: list[tuple[str, Path]] = []
    with repository_lock(root):
        validate_or_raise(root)
        prefixes, entries, retired, plans = load(root)
        if prefix not in prefixes:
            raise PlanError(f"Unregistered project prefix: {prefix}")
        source_records: dict[str, dict[str, object]] = {}
        if source_ids:
            source_records, _research_entries, _research_invalid = research_contract(root, source_ids)
            for research_id in source_ids:
                if research_id not in source_records:
                    raise UnknownResearchIDError(f"Unknown research ID: {research_id}")
                record = source_records[research_id]
                if record["status"] in {"cancelled", "archived"}:
                    raise PlanError(f"Cannot create a plan from research in status {record['status']!r}")
                if record["scope"] == "project" and str(prefixes[prefix]) not in record["projects"]:
                    raise PlanError(
                        f"Project-scoped research {research_id} belongs to {record['projects'][0]!r}, "
                        f"not plan project {prefixes[prefix]!r}"
                    )
        numbers = [
            int(match.group(2))
            for plan_id in set(entries) | set(retired) | set(plans)
            if (match := validator.ID_RE.fullmatch(plan_id)) and match.group(1) == prefix
        ]
        number = max(numbers, default=0) + 1
        plan_id = f"{prefix}-{number:03d}"
        project = prefixes[prefix]
        project_dir = root / project
        project_dir.mkdir(parents=True, exist_ok=True)
        path = project_dir / f"planning--{plan_id}--{name}.md"
        if path.exists():
            raise PlanError(f"Plan path already exists: {path}")
        title = args.title or name.replace("-", " ").capitalize()
        original_orchestration = (root / "ORCHESTRATION.md").read_text(encoding="utf-8")
        original_registry_path = root / validator.RESEARCH_REGISTRY
        original_registry = (
            original_registry_path.read_text(encoding="utf-8")
            if original_registry_path.is_file() else None
        )
        original_research: dict[Path, str] = {}
        moved_research: list[tuple[Path, Path]] = []
        for research_id in source_ids:
            record = source_records[research_id]
            source_path = root / str(record["path"])
            original_research[source_path] = source_path.read_text(encoding="utf-8")
        plan_text = f"# {title}\n\nID: {plan_id}\nStatus: planning\n"
        if source_ids:
            plan_text += f"Research: {', '.join(source_ids)}\n"
        try:
            path.write_text(plan_text, encoding="utf-8")
            insert_graph_row(root, f"| `{plan_id}` | `{project}` | — | — | — |")
            for research_id in source_ids:
                record = source_records[research_id]
                linked_plans = [str(item) for item in record["linked_plans"]]
                if plan_id not in linked_plans:
                    linked_plans.append(plan_id)
                current_status = str(record["status"])
                target_status = "converted" if current_status == "open" else current_status
                source_path, target_path, updated_text = research_file_update(
                    root, record, status=target_status, linked_plans=linked_plans
                )
                if target_path != source_path and target_path.exists():
                    raise PlanError(f"Target research path already exists: {target_path.relative_to(root)}")
                atomic_write(target_path, updated_text)
                if target_path != source_path:
                    source_path.unlink()
                    moved_research.append((source_path, target_path))
                updated_record = dict(record)
                updated_record["status"] = target_status
                updated_record["linked_plans"] = linked_plans
                updated_record["path"] = str(target_path.relative_to(root))
                update_research_registry_row(root, research_id, updated_record)
                converted_paths.append((research_id, target_path))
            validate_or_raise(root, plan_id)
            if source_ids:
                research_contract(root, source_ids)
        except Exception:
            path.unlink(missing_ok=True)
            atomic_write(root / "ORCHESTRATION.md", original_orchestration)
            for source_path, target_path in reversed(moved_research):
                if target_path.exists():
                    target_path.replace(source_path)
            for source_path, original_text in original_research.items():
                if source_path.exists():
                    atomic_write(source_path, original_text)
            if original_registry is None:
                original_registry_path.unlink(missing_ok=True)
            else:
                atomic_write(original_registry_path, original_registry)
            raise
    print(plan_id)
    print(path.resolve())
    for research_id, research_path in converted_paths:
        print(f"Research {research_id}")
        print(research_path.resolve())


def command_claim(root: Path, args: argparse.Namespace) -> None:
    plan_id = normalize_id(args.id)
    if not re.fullmatch(r"[A-Za-z0-9._@:/+-]+", args.agent):
        raise PlanError("Agent name may contain only letters, numbers, and . _ @ : / + -")
    with repository_lock(root):
        validate_or_raise(root, plan_id)
        _, entries, _, plans = load(root)
        reasons = readiness(plan_id, entries, plans)
        if reasons:
            raise PlanError(f"{plan_id} cannot be claimed:\n- " + "\n- ".join(reasons))
        timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        original = (root / "ORCHESTRATION.md").read_text(encoding="utf-8")
        update_graph_row(root, plan_id, 3, f"`{args.agent}`")
        update_graph_row(root, plan_id, 4, f"`{timestamp}`")
        try:
            validate_or_raise(root, plan_id)
        except Exception:
            atomic_write(root / "ORCHESTRATION.md", original)
            raise
    print(f"Claimed {plan_id} for {args.agent} at {timestamp}")


def command_release(root: Path, args: argparse.Namespace) -> None:
    plan_id = normalize_id(args.id)
    with repository_lock(root):
        validate_or_raise(root, plan_id)
        _, entries, _, plans = load(root)
        if plan_id not in plans or plan_id not in entries:
            raise UnknownPlanIDError(f"Unknown plan ID: {plan_id}", plan_id=plan_id)
        if empty(str(entries[plan_id]["claimed_by"])):
            raise PlanError(f"{plan_id} has no active claim")
        original = (root / "ORCHESTRATION.md").read_text(encoding="utf-8")
        update_graph_row(root, plan_id, 3, "—")
        update_graph_row(root, plan_id, 4, "—")
        try:
            validate_or_raise(root, plan_id)
        except Exception:
            atomic_write(root / "ORCHESTRATION.md", original)
            raise
    print(f"Released {plan_id}")


def command_depends(root: Path, args: argparse.Namespace) -> None:
    plan_id = normalize_id(args.id)
    dependencies = [normalize_id(value) for value in args.dependencies]
    if len(dependencies) != len(set(dependencies)):
        raise PlanError("Dependencies must be unique")
    with repository_lock(root):
        validate_or_raise(root, plan_id)
        _, entries, _, plans = load(root)
        if plan_id not in plans:
            raise UnknownPlanIDError(f"Unknown plan ID: {plan_id}", plan_id=plan_id)
        plan = plans[plan_id]
        entry = entries[plan_id]
        status = str(plan["status"])
        if status in {"verifying", "done"}:
            raise PlanError(f"{plan_id} cannot change dependencies while status is {status}")
        if not empty(str(entry["claimed_by"])):
            raise PlanError(f"{plan_id} cannot change dependencies while plan is claimed")
        unknown = [dependency for dependency in dependencies if dependency not in plans]
        if unknown:
            raise PlanError("Unknown dependencies: " + ", ".join(unknown))
        original = (root / "ORCHESTRATION.md").read_text(encoding="utf-8")
        value = "<br>".join(f"`{dependency}`" for dependency in dependencies) or "—"
        update_graph_row(root, plan_id, 2, value)
        try:
            validate_or_raise(root, plan_id)
        except Exception:
            atomic_write(root / "ORCHESTRATION.md", original)
            raise
    print(f"Updated dependencies for {plan_id}: {', '.join(dependencies) or '—'}")


def command_status(root: Path, args: argparse.Namespace) -> None:
    plan_id = normalize_id(args.id)
    target = args.status
    transitions = {
        "planning": {"ready"},
        "ready": {"planning", "verifying"},
        "verifying": {"ready", "done"},
        "done": set(),
    }
    with repository_lock(root):
        validate_or_raise(root, plan_id)
        _, entries, _, plans = load(root)
        if plan_id not in plans:
            raise UnknownPlanIDError(f"Unknown plan ID: {plan_id}", plan_id=plan_id)
        plan = plans[plan_id]
        current = str(plan["status"])
        if target not in transitions[current]:
            raise PlanError(f"Unsupported status transition: {current} -> {target}")
        if target == "verifying" and empty(str(entries[plan_id]["claimed_by"])):
            raise PlanError("Claim the plan before moving to verifying")
        if target == "done":
            findings = root / str(plan["project"]) / "findings" / plan_id
            expected_link = f"findings/{plan_id}/README.md"
            has_findings_link = bool(
                re.search(rf"\]\(\s*{re.escape(expected_link)}\s*\)", str(plan["text"]))
            )
            if findings.exists() or has_findings_link:
                raise PlanError("Delete findings and remove their plan link before moving to done")
            if not empty(str(entries[plan_id]["claimed_by"])):
                raise PlanError("Release the active claim before moving to done")
        source = Path(plan["path"])
        target_path = source.with_name(source.name.replace(f"{current}--", f"{target}--", 1))
        original_text = source.read_text(encoding="utf-8")
        updated_text, count = re.subn(rf"(?m)^Status:\s*{re.escape(current)}\s*$", f"Status: {target}", original_text)
        if count != 1:
            raise PlanError("Expected exactly one Status field in plan")
        if target_path.exists():
            raise PlanError(f"Target status file already exists: {target_path.relative_to(root)}")
        atomic_write(target_path, updated_text)
        source.unlink()
        try:
            validate_or_raise(root, plan_id)
        except Exception:
            atomic_write(source, original_text)
            target_path.unlink(missing_ok=True)
            raise
    print(f"Moved {plan_id}: {current} -> {target}")
    print(target_path.relative_to(root))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        help=(
            "plan-hub checkout path or configured root name "
            "(never defaults to the client checkout)"
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("init").set_defaults(handler=command_init)
    commands.add_parser("validate").set_defaults(handler=command_validate)
    commands.add_parser("list-ready").set_defaults(handler=command_list_ready)

    roots_cmd = commands.add_parser("roots", help="list the effective plan-hub root set")
    roots_cmd.add_argument("--json", action="store_true", help="emit the versioned structured report")
    roots_cmd.set_defaults(handler=command_roots)

    locate = commands.add_parser("locate", help="read-only lookup of a plan ID across every configured hub")
    locate.add_argument("id")
    locate.add_argument("--json", action="store_true", help="emit the versioned structured report")
    locate.set_defaults(handler=command_locate)

    scan = commands.add_parser("scan", help="read-only inventory of managed and unmanaged datastore input")
    scan.add_argument("--json", action="store_true", help="emit the versioned structured report")
    scan.set_defaults(handler=command_scan)

    repair = commands.add_parser("repair", help="propose safe, reviewable repairs (dry-run by default)")
    repair.add_argument("--json", action="store_true", help="emit structured proposals")
    repair_mode = repair.add_mutually_exclusive_group()
    repair_mode.add_argument("--apply", action="store_true", help="apply automatic-safe proposals only")
    repair_mode.add_argument("--llm", action="store_true", help="emit a provider-agnostic semantic repair handoff")
    repair.set_defaults(handler=command_repair)

    show = commands.add_parser("show")
    show.add_argument("id")
    show.set_defaults(handler=command_show)

    ready = commands.add_parser("ready")
    ready.add_argument("id")
    ready.set_defaults(handler=command_ready)

    allocate = commands.add_parser("allocate")
    allocate.add_argument("prefix")
    allocate.add_argument("plan_name")
    allocate.add_argument("--title")
    allocate.add_argument(
        "--from-research",
        action="append",
        nargs="+",
        default=[],
        help="one or more research IDs to explicitly convert into this plan (repeatable)",
    )
    allocate.set_defaults(handler=command_allocate)

    claim = commands.add_parser("claim")
    claim.add_argument("id")
    claim.add_argument("agent")
    claim.set_defaults(handler=command_claim)

    release = commands.add_parser("release")
    release.add_argument("id")
    release.set_defaults(handler=command_release)

    depends = commands.add_parser("depends")
    depends.add_argument("id")
    depends.add_argument("dependencies", nargs="*")
    depends.set_defaults(handler=command_depends)

    status = commands.add_parser("status")
    status.add_argument("id")
    status.add_argument("status", choices=("planning", "ready", "verifying", "done"))
    status.set_defaults(handler=command_status)

    research = commands.add_parser("research", help="allocate and maintain first-class research artifacts")
    research_commands = research.add_subparsers(dest="research_command", required=True)

    research_allocate = research_commands.add_parser(
        "allocate", aliases=("create", "new"), help="allocate a new research record"
    )
    research_allocate.add_argument("values", nargs="*", help="scope, optional project, and slug/name")
    research_allocate.add_argument(
        "--scope", dest="scope_option", choices=("project", "cross-project", "unknown", "unscoped")
    )
    research_allocate.add_argument("--project", "--projects", dest="project", action="append", default=[])
    research_allocate.add_argument("--name")
    research_allocate.add_argument("--title")
    research_allocate.add_argument("--json", action="store_true")
    research_allocate.set_defaults(handler=command_research_allocate)

    research_show = research_commands.add_parser("show", help="show a research record")
    research_show.add_argument("id")
    research_show.add_argument("--json", action="store_true")
    research_show.set_defaults(handler=command_research_show)

    research_status = research_commands.add_parser("status", help="move a research record through its lifecycle")
    research_status.add_argument("id")
    research_status.add_argument("status", choices=("open", "converted", "cancelled", "archived"))
    research_status.add_argument("--json", action="store_true")
    research_status.set_defaults(handler=command_research_status)

    research_link = research_commands.add_parser(
        "link", aliases=("plan-link", "link-plan"), help="link a plan to research"
    )
    research_link.add_argument("research_id")
    research_link.add_argument("plan_id")
    research_link.add_argument("--json", action="store_true")
    research_link.set_defaults(handler=command_research_link)
    return parser


ROOT_SET_COMMANDS = {"roots", "locate"}


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        if args.command in ROOT_SET_COMMANDS:
            roots, warnings = effective_roots(args.root)
            args.handler(roots, warnings, args)
        else:
            root = configured_root(args.root)
            args.handler(root, args)
    except UnknownPlanIDError as error:
        print(f"planctl: {error}", file=sys.stderr)
        if error.plan_id and multi_root_registry():
            print(
                f"planctl: hint: run 'planctl locate {error.plan_id}' to search all configured hubs",
                file=sys.stderr,
            )
        return 1
    except (PlanError, ValueError) as error:
        print(f"planctl: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
