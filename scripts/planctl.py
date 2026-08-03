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


def empty(value: str) -> bool:
    return value.lower() in validator.EMPTY_VALUES


def validate_repository_or_raise(root: Path) -> None:
    errors = validator.validate(root)
    if errors:
        raise PlanError("Repository validation failed:\n- " + "\n- ".join(errors))


def validate_or_raise(root: Path, *required_ids: str) -> None:
    """Block only on global ambiguity or state required by this operation."""
    errors: list[str] = []
    for required in ("README.md", "AGENTS.md", "ORCHESTRATION.md"):
        if not (root / required).is_file():
            errors.append(f"Missing {required}")
    registry_errors: list[str] = []
    if (root / "ORCHESTRATION.md").is_file():
        validator.parse_registry(root / "ORCHESTRATION.md", registry_errors)
    errors.extend(registry_errors)
    report = validator.scan(root)
    for item in report["diagnostics"]:
        if item["code"] == "ambiguous-plan-id":
            errors.append(f"ambiguous unmanaged input: {item['message']}")
    validation_errors, base_plans, invalid_ids = validator.validation_state(root)
    managed_ids = set(base_plans) - invalid_ids
    unknown = [plan_id for plan_id in required_ids if plan_id not in managed_ids]
    for plan_id in required_ids:
        if plan_id not in managed_ids:
            relevant = [message for message in validation_errors if plan_id in message]
            errors.extend(relevant or [f"Plan {plan_id!r} is not valid managed state"])
    if errors:
        message = "Repository validation failed:\n- " + "\n- ".join(dict.fromkeys(errors))
        if unknown:
            raise UnknownPlanIDError(message, plan_id=unknown[0])
        raise PlanError(message)


def load(
    root: Path,
) -> tuple[dict[str, str], dict[str, dict[str, object]], dict[str, str], dict[str, dict[str, object]]]:
    errors: list[str] = []
    prefixes, entries, retired = validator.parse_registry(root / "ORCHESTRATION.md", errors)
    if errors:
        raise PlanError("Cannot read plan registry:\n- " + "\n- ".join(errors))
    _, base_plans, invalid_ids = validator.validation_state(root)
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
    print(f"Unmanaged files: {len(report['unmanaged_files'])}")
    print(f"Diagnostics: {len(report['diagnostics'])}")
    for item in report["diagnostics"]:
        location = f" ({item['path']})" if "path" in item else ""
        print(f"- [{item['severity']}] {item['code']}{location}: {item['message']}")


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


def command_allocate(root: Path, args: argparse.Namespace) -> None:
    prefix = args.prefix.upper()
    name = args.plan_name.lower()
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name):
        raise PlanError("Plan name must use lowercase kebab-case")
    with repository_lock(root):
        validate_or_raise(root)
        prefixes, entries, retired, plans = load(root)
        if prefix not in prefixes:
            raise PlanError(f"Unregistered project prefix: {prefix}")
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
        path.write_text(f"# {title}\n\nID: {plan_id}\nStatus: planning\n", encoding="utf-8")
        insert_graph_row(root, f"| `{plan_id}` | `{project}` | — | — | — |")
        try:
            validate_or_raise(root, plan_id)
        except Exception:
            path.unlink(missing_ok=True)
            atomic_write(root / "ORCHESTRATION.md", original_orchestration)
            raise
    print(plan_id)
    print(path.resolve())


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
