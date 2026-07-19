#!/usr/bin/env python3
"""Resolve and safely maintain plans by stable ID."""

from __future__ import annotations

import argparse
import fcntl
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True
validator = importlib.import_module("validate_plans")


class PlanError(RuntimeError):
    pass


def configured_root(command_line_root: Path | None) -> Path:
    """Resolve storage independently from the client checkout.

    An explicit --root always wins over PLANS_ROOT. There is deliberately no
    fallback to the source checkout: this client is software, not plan data.
    """
    if command_line_root is not None:
        return command_line_root.expanduser().resolve()
    configured = os.environ.get("PLANS_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    raise PlanError("no plan hub selected; pass --root PATH or set PLANS_ROOT")


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
    for plan_id in required_ids:
        if plan_id not in managed_ids:
            relevant = [message for message in validation_errors if plan_id in message]
            errors.extend(relevant or [f"Plan {plan_id!r} is not valid managed state"])
    if errors:
        raise PlanError("Repository validation failed:\n- " + "\n- ".join(dict.fromkeys(errors)))


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
        raise PlanError(f"Unknown plan ID: {plan_id}")
    plan = plans[plan_id]
    entry = entries[plan_id]
    dependencies = ", ".join(entry["dependencies"]) or "—"
    claim = "—" if empty(str(entry["claimed_by"])) else f"{entry['claimed_by']} at {entry['claimed_at']}"
    print(f"ID: {plan_id}")
    print(f"Project: {plan['project']}")
    print(f"Status: {plan['status']}")
    print(f"Path: {Path(plan['path']).relative_to(root)}")
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
    print(path.relative_to(root))


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
            raise PlanError(f"Unknown plan ID: {plan_id}")
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
            raise PlanError(f"Unknown plan ID: {plan_id}")
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
            raise PlanError(f"Unknown plan ID: {plan_id}")
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
        help="plan-hub checkout (overrides PLANS_ROOT; never defaults to the client checkout)",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("init").set_defaults(handler=command_init)
    commands.add_parser("validate").set_defaults(handler=command_validate)
    commands.add_parser("list-ready").set_defaults(handler=command_list_ready)

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


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        root = configured_root(args.root)
        args.handler(root, args)
    except (PlanError, ValueError) as error:
        print(f"planctl: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
