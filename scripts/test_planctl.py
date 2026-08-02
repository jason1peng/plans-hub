#!/usr/bin/env python3
"""Behavior tests for planctl and shared-plan validation."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("planctl.py")
INSTALLER = Path(__file__).with_name("install-skill.sh")
RELEASE_GUARD = Path(__file__).with_name("check_public_release.py")

ORCHESTRATION = """# Plan Orchestration

## Project prefix registry

| Prefix | Project folder |
| --- | --- |
| `DEMO` | `demo-project` |

## Plan dependency graph

| ID | Project | Depends on | Claimed by | Claimed at |
| --- | --- | --- | --- | --- |

## Retired plan IDs

| ID | Project |
| --- | --- |

## Maintenance rules
"""


class PlanctlTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / ".git").mkdir()
        (self.root / ".pi-subagents").mkdir()
        (self.root / "README.md").write_text("# Plans\n", encoding="utf-8")
        (self.root / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
        (self.root / "ORCHESTRATION.md").write_text(ORCHESTRATION, encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, *arguments: str, succeeds: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["python3", str(SCRIPT), "--root", str(self.root), *arguments],
            text=True,
            capture_output=True,
            check=False,
        )
        if succeeds and result.returncode != 0:
            self.fail(f"command failed: {arguments}\nstdout={result.stdout}\nstderr={result.stderr}")
        if not succeeds and result.returncode == 0:
            self.fail(f"command unexpectedly passed: {arguments}\nstdout={result.stdout}")
        return result

    def test_validate_rejects_removed_private_client_path_in_orchestration(self) -> None:
        orchestration = self.root / "ORCHESTRATION.md"
        orchestration.write_text(
            orchestration.read_text(encoding="utf-8")
            + "\nRun `scripts/" + "planctl.py validate` after every change.\n",
            encoding="utf-8",
        )

        result = self.run_cli("validate", succeeds=False)

        self.assertIn("references removed private client path", result.stderr)
        self.assertIn("use the installed planctl wrapper", result.stderr)

    def test_root_is_required_and_explicit_root_selects_the_hub(self) -> None:
        with tempfile.TemporaryDirectory() as home_name:
            environment = {**os.environ, "HOME": home_name}
            missing = subprocess.run(
                ["python3", str(SCRIPT), "validate"],
                text=True,
                capture_output=True,
                check=False,
                env=environment,
            )
            self.assertNotEqual(0, missing.returncode)
            self.assertIn("no plan hub selected", missing.stderr)

            explicit = subprocess.run(
                ["python3", str(SCRIPT), "--root", str(self.root), "validate"],
                text=True,
                capture_output=True,
                check=False,
                env=environment,
            )
            self.assertEqual(0, explicit.returncode, explicit.stderr)

    def test_documented_existing_hub_onboarding_upgrades_and_uses_installed_wrapper(self) -> None:
        # The supported order is: register the hub, install, then invoke the full wrapper path.
        legacy_skill = self.root.resolve() / "skills" / "shared-plan-storage"
        self.assertFalse(legacy_skill.exists(), "exercise the dangling post-migration legacy link")

        with tempfile.TemporaryDirectory() as home_name:
            home = Path(home_name)
            registry = home / ".config" / "plans-hub" / "roots.json"
            registry.parent.mkdir(parents=True)
            registry.write_text(
                json.dumps({"schema_version": 1, "roots": [{"name": "main", "path": str(self.root)}]}),
                encoding="utf-8",
            )
            destination = home / ".agents" / "skills" / "shared-plan-storage"
            destination.parent.mkdir(parents=True)
            destination.symlink_to(legacy_skill)
            environment = {**os.environ, "HOME": str(home)}
            environment.pop("PYTHONDONTWRITEBYTECODE", None)
            environment.pop("PYTHONPYCACHEPREFIX", None)

            installed = subprocess.run(
                [str(INSTALLER)],
                text=True,
                capture_output=True,
                check=False,
                env=environment,
            )
            self.assertEqual(0, installed.returncode, installed.stderr)
            self.assertIn("Upgraded legacy installation", installed.stdout)
            self.assertNotEqual(legacy_skill, destination.resolve())

            wrapper = destination / "bin" / "planctl"
            validated = subprocess.run(
                [str(wrapper), "validate"],
                text=True,
                capture_output=True,
                check=False,
                env=environment,
            )
            self.assertEqual(0, validated.returncode, validated.stderr)
            listed = subprocess.run(
                [str(wrapper), "list-ready"],
                text=True,
                capture_output=True,
                check=False,
                env=environment,
            )
            self.assertEqual(0, listed.returncode, listed.stderr)

            release_guard = subprocess.run(
                ["python3", str(RELEASE_GUARD), "--history"],
                cwd=SCRIPT.parent.parent,
                text=True,
                capture_output=True,
                check=False,
                env=environment,
            )
            self.assertEqual(0, release_guard.returncode, release_guard.stderr)

        for existing_kind in ("symlink", "file"):
            with self.subTest(existing_kind=existing_kind), tempfile.TemporaryDirectory() as home_name:
                home = Path(home_name)
                destination = home / ".agents" / "skills" / "shared-plan-storage"
                destination.parent.mkdir(parents=True)
                if existing_kind == "symlink":
                    unknown = home / "unknown-skill"
                    unknown.mkdir()
                    destination.symlink_to(unknown)
                else:
                    destination.write_text("do not replace\n", encoding="utf-8")
                environment = {**os.environ, "HOME": str(home)}

                rejected = subprocess.run(
                    [str(INSTALLER)],
                    text=True,
                    capture_output=True,
                    check=False,
                    env=environment,
                )
                self.assertNotEqual(0, rejected.returncode)
                self.assertIn("refusing to replace existing", rejected.stderr)

    def test_documented_git_sequence_targets_private_hub_from_public_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_name:
            workspace = Path(workspace_name)
            seed = workspace / "seed"
            remote = workspace / "remote.git"
            worker = workspace / "worker"
            verification = workspace / "verification"

            initialized = subprocess.run(
                ["python3", str(SCRIPT), "--root", str(seed), "init"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, initialized.returncode, initialized.stderr)
            subprocess.run(["git", "-C", str(seed), "init", "-q", "-b", "main"], check=True)
            subprocess.run(["git", "-C", str(seed), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(seed), "config", "user.name", "Test"], check=True)
            for arguments in (("allocate", "DEMO", "first-plan"), ("status", "DEMO-001", "ready")):
                result = subprocess.run(
                    ["python3", str(SCRIPT), "--root", str(seed), *arguments],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(0, result.returncode, result.stderr)
            subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
            subprocess.run(["git", "-C", str(seed), "commit", "-qm", "initialize hub"], check=True)
            subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
            subprocess.run(["git", "-C", str(seed), "remote", "add", "origin", str(remote)], check=True)
            subprocess.run(["git", "-C", str(seed), "push", "-q", "-u", "origin", "main"], check=True)
            subprocess.run(["git", "clone", "-q", "-b", "main", str(remote), str(worker)], check=True)
            subprocess.run(["git", "-C", str(worker), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(worker), "config", "user.name", "Test"], check=True)

            # Run from the public client checkout while every Git operation explicitly targets the private hub.
            for command in (
                ["git", "-C", str(worker), "fetch", "origin"],
                ["git", "-C", str(worker), "merge", "--ff-only", "origin/main"],
                ["python3", str(SCRIPT), "--root", str(worker), "list-ready"],
                ["python3", str(SCRIPT), "--root", str(worker), "claim", "DEMO-001", "agent-name"],
                ["python3", str(SCRIPT), "--root", str(worker), "validate"],
                ["git", "-C", str(worker), "add", "ORCHESTRATION.md"],
                ["git", "-C", str(worker), "commit", "-m", "plans(DEMO-001): claim"],
                ["git", "-C", str(worker), "push", "origin", "main"],
            ):
                result = subprocess.run(
                    command,
                    cwd=SCRIPT.parent.parent,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(0, result.returncode, f"{command}\n{result.stdout}\n{result.stderr}")

            subprocess.run(["git", "clone", "-q", "-b", "main", str(remote), str(verification)], check=True)
            shown = subprocess.run(
                ["python3", str(SCRIPT), "--root", str(verification), "show", "DEMO-001"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, shown.returncode, shown.stderr)
            self.assertIn("Claim: agent-name", shown.stdout)

    def test_rejected_claim_recovery_discards_only_unpublished_commit(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_name:
            workspace = Path(workspace_name)
            remote = workspace / "remote.git"
            winner = workspace / "winner"
            loser = workspace / "loser"
            subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
            subprocess.run(["git", "clone", "-q", str(remote), str(winner)], check=True)
            for checkout in (winner,):
                subprocess.run(["git", "-C", str(checkout), "config", "user.email", "test@example.invalid"], check=True)
                subprocess.run(["git", "-C", str(checkout), "config", "user.name", "Test"], check=True)
            (winner / "ORCHESTRATION.md").write_text("unclaimed\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(winner), "add", "ORCHESTRATION.md"], check=True)
            subprocess.run(["git", "-C", str(winner), "commit", "-qm", "initialize"], check=True)
            subprocess.run(["git", "-C", str(winner), "branch", "-M", "main"], check=True)
            subprocess.run(["git", "-C", str(winner), "push", "-q", "-u", "origin", "main"], check=True)
            subprocess.run(["git", "clone", "-q", "-b", "main", str(remote), str(loser)], check=True)
            subprocess.run(["git", "-C", str(loser), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(loser), "config", "user.name", "Test"], check=True)

            (winner / "ORCHESTRATION.md").write_text("winner claim\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(winner), "commit", "-qam", "winner claim"], check=True)
            subprocess.run(["git", "-C", str(winner), "push", "-q", "origin", "main"], check=True)
            (loser / "ORCHESTRATION.md").write_text("loser claim\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(loser), "commit", "-qam", "loser claim"], check=True)
            rejected = subprocess.run(
                ["git", "-C", str(loser), "push", "origin", "main"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(0, rejected.returncode)

            subprocess.run(["git", "-C", str(loser), "fetch", "-q", "origin"], check=True)
            impossible = subprocess.run(
                ["git", "-C", str(loser), "merge", "--ff-only", "origin/main"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(0, impossible.returncode)
            subprocess.run(["git", "-C", str(loser), "reset", "--keep", "origin/main"], check=True)
            self.assertEqual("winner claim\n", (loser / "ORCHESTRATION.md").read_text(encoding="utf-8"))
            self.assertEqual(
                self.git_head(winner),
                self.git_head(loser),
            )

    @staticmethod
    def git_head(checkout: Path) -> str:
        return subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()

    def test_init_bootstraps_external_synthetic_hub(self) -> None:
        initialized = self.root / "initialized-hub"
        result = subprocess.run(
            ["python3", str(SCRIPT), "--root", str(initialized), "init"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue((initialized / "ORCHESTRATION.md").is_file())
        allocated = subprocess.run(
            ["python3", str(SCRIPT), "--root", str(initialized), "allocate", "DEMO", "sample-plan"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, allocated.returncode, allocated.stderr)
        self.assertIn("DEMO-001", allocated.stdout)

        repeated = subprocess.run(
            ["python3", str(SCRIPT), "--root", str(initialized), "init"],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(0, repeated.returncode)
        self.assertIn("non-empty", repeated.stderr)

    def test_allocate_resolve_claim_and_complete(self) -> None:
        result = self.run_cli("allocate", "DEMO", "first-plan", "--title", "First plan")
        self.assertIn("DEMO-001", result.stdout)
        plan = self.root / "demo-project" / "planning--DEMO-001--first-plan.md"
        self.assertTrue(plan.is_file())
        self.assertEqual(["DEMO-001", str(plan.resolve())], result.stdout.splitlines())

        shown = self.run_cli("show", "demo-001")
        self.assertIn(f"Path: {plan.resolve()}", shown.stdout)
        self.run_cli("ready", "DEMO-001", succeeds=False)
        self.run_cli("status", "DEMO-001", "ready")
        self.run_cli("ready", "DEMO-001")
        self.run_cli("claim", "DEMO-001", "test-agent")
        self.run_cli("ready", "DEMO-001", succeeds=False)
        self.run_cli("status", "DEMO-001", "verifying")
        self.run_cli("release", "DEMO-001")
        self.run_cli("status", "DEMO-001", "done")
        self.run_cli("validate")
        self.assertTrue((self.root / "demo-project" / "done--DEMO-001--first-plan.md").is_file())

    def test_dependencies_and_findings_gate_readiness_and_done(self) -> None:
        self.run_cli("allocate", "DEMO", "upstream")
        self.run_cli("allocate", "DEMO", "downstream")
        self.run_cli("depends", "DEMO-002", "DEMO-001")
        self.run_cli("status", "DEMO-002", "ready")
        blocked = self.run_cli("ready", "DEMO-002", succeeds=False)
        self.assertIn("dependency DEMO-001 is planning", blocked.stderr)

        self.run_cli("status", "DEMO-001", "ready")
        self.run_cli("claim", "DEMO-001", "upstream-agent")
        self.run_cli("status", "DEMO-001", "verifying")
        self.run_cli("release", "DEMO-001")
        self.run_cli("status", "DEMO-001", "done")
        self.run_cli("ready", "DEMO-002")

        plan = self.root / "demo-project" / "ready--DEMO-002--downstream.md"
        findings = self.root / "demo-project" / "findings" / "DEMO-002"
        findings.mkdir(parents=True)
        (findings / "README.md").write_text("# Findings\n", encoding="utf-8")
        plan.write_text(plan.read_text(encoding="utf-8") + "\n[Findings](findings/DEMO-002/README.md)\n", encoding="utf-8")
        self.run_cli("validate")
        self.run_cli("claim", "DEMO-002", "downstream-agent")
        self.run_cli("status", "DEMO-002", "verifying")
        rejected = self.run_cli("status", "DEMO-002", "done", succeeds=False)
        self.assertIn("Delete findings", rejected.stderr)

        plan = self.root / "demo-project" / "verifying--DEMO-002--downstream.md"
        plan.write_text(plan.read_text(encoding="utf-8").replace("\n[Findings](findings/DEMO-002/README.md)\n", "\n"), encoding="utf-8")
        (findings / "README.md").unlink()
        findings.rmdir()
        self.run_cli("release", "DEMO-002")
        self.run_cli("status", "DEMO-002", "done")
        self.run_cli("validate")

    def test_dependency_cli_lock_and_current_graph_validation(self) -> None:
        self.run_cli("allocate", "DEMO", "upstream")
        self.run_cli("allocate", "DEMO", "downstream")
        self.run_cli("allocate", "DEMO", "completed-dependency")
        self.run_cli("status", "DEMO-003", "ready")
        self.run_cli("claim", "DEMO-003", "completed-agent")
        self.run_cli("status", "DEMO-003", "verifying")
        self.run_cli("release", "DEMO-003")
        self.run_cli("status", "DEMO-003", "done")
        self.run_cli("status", "DEMO-002", "ready")
        self.run_cli("claim", "DEMO-002", "dependency-agent")

        claimed = self.run_cli("depends", "DEMO-002", "DEMO-001", succeeds=False)
        self.assertIn("cannot change dependencies while plan is claimed", claimed.stderr)
        self.run_cli("status", "DEMO-002", "verifying")

        verifying = self.run_cli("depends", "DEMO-002", "DEMO-001", succeeds=False)
        self.assertIn("cannot change dependencies while status is verifying", verifying.stderr)

        orchestration = self.root / "ORCHESTRATION.md"
        original = orchestration.read_text(encoding="utf-8")
        mutated = original.replace(
            "| `DEMO-002` | `demo-project` | — | `dependency-agent` |",
            "| `DEMO-002` | `demo-project` | `DEMO-001` | `dependency-agent` |",
        )
        self.assertNotEqual(original, mutated)
        orchestration.write_text(mutated, encoding="utf-8")
        invalid = self.run_cli("validate", succeeds=False)
        self.assertIn("requires dependency 'DEMO-001' to be done", invalid.stderr)

        completed_dependency = original.replace(
            "| `DEMO-002` | `demo-project` | — | `dependency-agent` |",
            "| `DEMO-002` | `demo-project` | `DEMO-003` | `dependency-agent` |",
        )
        self.assertNotEqual(original, completed_dependency)
        orchestration.write_text(completed_dependency, encoding="utf-8")
        self.run_cli("validate")
        orchestration.write_text(original, encoding="utf-8")

        self.run_cli("release", "DEMO-002")
        self.run_cli("status", "DEMO-002", "done")
        done = self.run_cli("depends", "DEMO-002", "DEMO-001", succeeds=False)
        self.assertIn("cannot change dependencies while status is done", done.stderr)
        self.run_cli("validate")

    def test_retired_ids_are_not_reused_and_cycles_are_rejected(self) -> None:
        orchestration = self.root / "ORCHESTRATION.md"
        text = orchestration.read_text(encoding="utf-8")
        text = text.replace(
            "| ID | Project |\n| --- | --- |\n",
            "| ID | Project |\n| --- | --- |\n| `DEMO-005` | `demo-project` |\n",
        )
        orchestration.write_text(text, encoding="utf-8")

        allocated = self.run_cli("allocate", "DEMO", "after-retired")
        self.assertIn("DEMO-006", allocated.stdout)
        self.run_cli("allocate", "DEMO", "cycle-peer")
        self.run_cli("depends", "DEMO-007", "DEMO-006")
        rejected = self.run_cli("depends", "DEMO-006", "DEMO-007", succeeds=False)
        self.assertIn("Dependency cycle", rejected.stderr)
        self.run_cli("claim", "DEMO-006", "bad`agent", succeeds=False)
        self.run_cli("validate")

    def test_tool_agnostic_ingestion_converges_in_isolated_git_clones(self) -> None:
        """Exercise the P3 ingestion and approval boundary using only synthetic data."""
        with tempfile.TemporaryDirectory() as workspace_name:
            workspace = Path(workspace_name)
            seed = workspace / "seed"
            remote = workspace / "remote.git"
            depositor = workspace / "depositor"
            verifier = workspace / "verifier"

            initialized = subprocess.run(
                ["python3", str(SCRIPT), "--root", str(seed), "init"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(0, initialized.returncode, initialized.stderr)
            subprocess.run(["git", "-C", str(seed), "init", "-q", "-b", "main"], check=True)
            subprocess.run(["git", "-C", str(seed), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(seed), "config", "user.name", "Test"], check=True)

            def cli(root: Path, *arguments: str, succeeds: bool = True) -> subprocess.CompletedProcess[str]:
                result = subprocess.run(
                    ["python3", str(SCRIPT), "--root", str(root), *arguments],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                if succeeds:
                    self.assertEqual(0, result.returncode, result.stderr)
                else:
                    self.assertNotEqual(0, result.returncode, result.stdout)
                return result

            cli(seed, "allocate", "DEMO", "managed")
            subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
            subprocess.run(["git", "-C", str(seed), "commit", "-qm", "initialize datastore"], check=True)
            subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
            subprocess.run(["git", "-C", str(seed), "remote", "add", "origin", str(remote)], check=True)
            subprocess.run(["git", "-C", str(seed), "push", "-q", "-u", "origin", "main"], check=True)
            subprocess.run(["git", "clone", "-q", "-b", "main", str(remote), str(depositor)], check=True)
            subprocess.run(["git", "-C", str(depositor), "config", "user.email", "tool@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(depositor), "config", "user.name", "External Tool"], check=True)

            # Another author deposits good, malformed, ambiguous, and unregistered
            # files in distinct project folders, then publishes them as ordinary Git data.
            additions = {
                "private-hub/planning--DEMO-002--Sample Plan.md":
                    "# Normalize me\n\nID: DEMO-002\nStatus: planning\n",
                "plan-hub/draft.md": "# Malformed\n\nID: invalid\nStatus: planning\n",
                "initialized-hub/planning--DEMO-003--external.md":
                    "# Unregistered\n\nID: DEMO-003\nStatus: planning\n",
                "private-hub/ambiguous.md":
                    "# Ambiguous\n\nID: DEMO-001\nStatus: planning\n",
            }
            for relative, content in additions.items():
                path = depositor / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            subprocess.run(["git", "-C", str(depositor), "add", "."], check=True)
            subprocess.run(["git", "-C", str(depositor), "commit", "-qm", "external tool ingestion"], check=True)
            subprocess.run(["git", "-C", str(depositor), "push", "-q"], check=True)

            subprocess.run(["git", "clone", "-q", "-b", "main", str(remote), str(verifier)], check=True)
            # The fresh clone is understandable before installing or invoking this client.
            self.assertTrue((verifier / "README.md").is_file())
            self.assertTrue((verifier / "ORCHESTRATION.md").is_file())
            self.assertIn("ID: DEMO-001", subprocess.run(
                ["git", "-C", str(verifier), "show", "HEAD:demo-project/planning--DEMO-001--managed.md"],
                text=True, capture_output=True, check=True,
            ).stdout)

            def normalized_snapshot(root: Path) -> tuple[tuple[str, str], ...]:
                return tuple(
                    (path.relative_to(root).as_posix(), path.read_text(encoding="utf-8"))
                    for path in sorted(root.rglob("*.md"))
                    if ".git" not in path.relative_to(root).parts
                )

            before_scan = normalized_snapshot(verifier)
            report = __import__("json").loads(cli(verifier, "scan", "--json").stdout)
            self.assertEqual(before_scan, normalized_snapshot(verifier), "scan must be read-only")
            self.assertEqual(["DEMO-001"], [item["id"] for item in report["managed_plans"]])
            codes = {item["code"] for item in report["diagnostics"]}
            self.assertTrue({
                "unmanaged-plan-file", "malformed-plan-id", "ambiguous-plan-id",
                "unregistered-plan-file",
            } <= codes)
            unregistered = [
                item for item in report["diagnostics"]
                if item["code"] == "unregistered-plan-file" and item.get("plan_id") == "DEMO-003"
            ]
            self.assertEqual(1, len(unregistered))
            self.assertEqual(
                "initialized-hub/planning--DEMO-003--external.md",
                unregistered[0]["path"],
            )
            self.assertEqual("approval-required", unregistered[0]["repair_classification"])
            self.assertNotIn("DEMO-003", [item["id"] for item in report["managed_plans"]])
            inactive = cli(verifier, "show", "DEMO-003", succeeds=False)
            self.assertIn("DEMO-003' is not valid managed state", inactive.stderr)

            dry_run = __import__("json").loads(cli(verifier, "repair", "--json").stdout)
            self.assertIn("automatic-safe", {item["classification"] for item in dry_run["proposals"]})
            self.assertEqual(before_scan, normalized_snapshot(verifier))
            handoff = __import__("json").loads(cli(verifier, "repair", "--llm", "--json").stdout)
            self.assertTrue(handoff["review_required"])
            self.assertFalse(handoff["may_apply"])
            refused = cli(verifier, "repair", "--apply", "--json", succeeds=False)
            self.assertIn("refusing --apply", refused.stderr)
            self.assertEqual(before_scan, normalized_snapshot(verifier))

            # Emulate an explicitly approved host patch: remove the ambiguous input,
            # normalize semantic content, and register only reviewed managed plans.
            (verifier / "private-hub" / "ambiguous.md").unlink()
            (verifier / "plan-hub" / "draft.md").unlink()
            external = verifier / "initialized-hub" / "planning--DEMO-003--external.md"
            reviewed = verifier / "demo-project" / external.name
            external.replace(reviewed)
            orchestration = verifier / "ORCHESTRATION.md"
            orchestration.write_text(
                orchestration.read_text(encoding="utf-8").replace(
                    "## Retired plan IDs",
                    "| `DEMO-003` | `demo-project` | — | — | — |\n\n## Retired plan IDs",
                ),
                encoding="utf-8",
            )

            # With semantic issues reviewed, deterministic repair may normalize a slug.
            applied = __import__("json").loads(cli(verifier, "repair", "--apply", "--json").stdout)
            self.assertTrue(applied["applied"])
            normalized = verifier / "private-hub" / "planning--DEMO-002--sample-plan.md"
            self.assertTrue(normalized.is_file())

            # Registration and project placement remain a second explicit approval.
            destination = verifier / "demo-project" / normalized.name
            normalized.replace(destination)
            orchestration.write_text(
                orchestration.read_text(encoding="utf-8").replace(
                    "| `DEMO-003` | `demo-project` | — | — | — |",
                    "| `DEMO-002` | `demo-project` | — | — | — |\n"
                    "| `DEMO-003` | `demo-project` | — | — | — |",
                ),
                encoding="utf-8",
            )
            clean = __import__("json").loads(cli(verifier, "scan", "--json").stdout)
            self.assertTrue(clean["clean"], clean["diagnostics"])
            self.assertEqual(["DEMO-001", "DEMO-002", "DEMO-003"],
                             [item["id"] for item in clean["managed_plans"]])
            cli(verifier, "validate")

    def test_scan_inventories_unmanaged_input_without_activating_it(self) -> None:
        self.run_cli("allocate", "DEMO", "managed")
        self.run_cli("status", "DEMO-001", "ready")
        raw = self.root / "demo-project" / "idea from another tool.md"
        raw.write_text("# Idea\n\nThis is deliberately not a managed plan.\n", encoding="utf-8")

        scan = self.run_cli("scan", "--json")
        payload = __import__("json").loads(scan.stdout)

        self.assertEqual(["DEMO-001"], [plan["id"] for plan in payload["managed_plans"]])
        self.assertEqual("demo-project/idea from another tool.md", payload["unmanaged_files"][0]["path"])
        self.assertEqual("unmanaged-plan-file", payload["diagnostics"][0]["code"])
        self.run_cli("ready", "DEMO-001")

    def test_scan_reports_invalid_project_folder_names(self) -> None:
        self.run_cli("allocate", "DEMO", "managed")
        invalid_project = self.root / "Bad Project"
        invalid_project.mkdir()
        (invalid_project / "draft.md").write_text(
            "# Raw input\n\nID: DEMO-999\nStatus: planning\n", encoding="utf-8"
        )

        payload = __import__("json").loads(self.run_cli("scan", "--json").stdout)
        self.assertIn("Bad Project", payload["projects"])
        self.assertEqual(["DEMO-001"], [plan["id"] for plan in payload["managed_plans"]])
        naming = [item for item in payload["diagnostics"] if item["code"] == "invalid-project-folder"]
        self.assertEqual(1, len(naming))
        self.assertEqual("Bad Project", naming[0]["path"])
        self.assertIn("lowercase kebab-case", naming[0]["message"])
        self.assertFalse(payload["clean"])

    def test_unregistered_valid_shaped_input_is_inactive_for_policy_operations(self) -> None:
        self.run_cli("allocate", "DEMO", "managed")
        self.run_cli("status", "DEMO-001", "ready")
        raw = self.root / "demo-project" / "planning--DEMO-999--sample-plan.md"
        raw.write_text("# External draft\n\nID: DEMO-999\nStatus: planning\n", encoding="utf-8")

        payload = __import__("json").loads(self.run_cli("scan", "--json").stdout)
        self.assertEqual(["DEMO-001"], [plan["id"] for plan in payload["managed_plans"]])
        self.assertIn("unregistered-plan-file", {item["code"] for item in payload["diagnostics"]})
        self.run_cli("ready", "DEMO-001")
        self.run_cli("claim", "DEMO-001", "test-agent")
        allocated = self.run_cli("allocate", "DEMO", "first-plan")
        self.assertIn("DEMO-002", allocated.stdout)

    def test_ambiguous_unmanaged_id_blocks_only_policy_operations(self) -> None:
        self.run_cli("allocate", "DEMO", "managed")
        self.run_cli("status", "DEMO-001", "ready")
        raw = self.root / "demo-project" / "draft.md"
        raw.write_text("# Duplicate\n\nID: DEMO-001\nStatus: ready\n", encoding="utf-8")

        scan = self.run_cli("scan", "--json")
        payload = __import__("json").loads(scan.stdout)
        self.assertIn("ambiguous-plan-id", {item["code"] for item in payload["diagnostics"]})
        blocked = self.run_cli("ready", "DEMO-001", succeeds=False)
        self.assertIn("ambiguous unmanaged input", blocked.stderr)

    def test_every_raw_id_field_participates_in_active_id_ambiguity(self) -> None:
        self.run_cli("allocate", "DEMO", "managed")
        self.run_cli("status", "DEMO-001", "ready")
        raw = self.root / "demo-project" / "draft.md"
        raw.write_text(
            "# Conflicting IDs\n\nID: DEMO-999\nID: DEMO-001\nStatus: ready\n",
            encoding="utf-8",
        )

        payload = __import__("json").loads(self.run_cli("scan", "--json").stdout)
        ambiguity = [item for item in payload["diagnostics"] if item["code"] == "ambiguous-plan-id"]
        self.assertIn("DEMO-001", {item["plan_id"] for item in ambiguity})
        blocked = self.run_cli("ready", "DEMO-001", succeeds=False)
        self.assertIn("ambiguous unmanaged input", blocked.stderr)

    def test_scan_excludes_invalid_dependency_cycle_claim_findings_and_stale_rows(self) -> None:
        self.run_cli("allocate", "DEMO", "first")
        self.run_cli("allocate", "DEMO", "second")
        orchestration = self.root / "ORCHESTRATION.md"

        # A missing dependency invalidates its owner but not an unrelated plan.
        text = orchestration.read_text(encoding="utf-8").replace(
            "| `DEMO-001` | `demo-project` | — | — | — |",
            "| `DEMO-001` | `demo-project` | `DEMO-999` | — | — |",
        )
        orchestration.write_text(text, encoding="utf-8")
        payload = __import__("json").loads(self.run_cli("scan", "--json").stdout)
        self.assertEqual(["DEMO-002"], [plan["id"] for plan in payload["managed_plans"]])
        self.assertIn("missing-dependency", {item["code"] for item in payload["diagnostics"]})

        # A dependency cycle invalidates every participant.
        text = text.replace("`DEMO-999`", "`DEMO-002`").replace(
            "| `DEMO-002` | `demo-project` | — | — | — |",
            "| `DEMO-002` | `demo-project` | `DEMO-001` | — | — |",
        )
        orchestration.write_text(text, encoding="utf-8")
        payload = __import__("json").loads(self.run_cli("scan", "--json").stdout)
        self.assertEqual([], payload["managed_plans"])
        self.assertIn("dependency-cycle", {item["code"] for item in payload["diagnostics"]})

        # Invalid claims and findings lifecycle state exclude the affected plans.
        impossible = "2026" + "-99" + "-99T99:99:99Z"
        text = text.replace("`DEMO-002` | — | — |", f"— | `agent` | `{impossible}` |", 1)
        text = text.replace("| `DEMO-002` | `demo-project` | `DEMO-001` | — | — |",
                            "| `DEMO-002` | `demo-project` | — | — | — |")
        orchestration.write_text(text, encoding="utf-8")
        findings = self.root / "demo-project" / "findings" / "DEMO-002"
        findings.mkdir(parents=True)
        payload = __import__("json").loads(self.run_cli("scan", "--json").stdout)
        self.assertEqual([], payload["managed_plans"])
        codes = {item["code"] for item in payload["diagnostics"]}
        self.assertIn("invalid-claim", codes)
        self.assertIn("findings-lifecycle", codes)

        # A stale row is diagnosed but never represented as a managed plan.
        orchestration.write_text(
            orchestration.read_text(encoding="utf-8").replace(
                "## Retired plan IDs",
                "| `DEMO-003` | `demo-project` | — | — | — |\n\n## Retired plan IDs",
            ),
            encoding="utf-8",
        )
        payload = __import__("json").loads(self.run_cli("scan", "--json").stdout)
        self.assertNotIn("DEMO-003", [plan["id"] for plan in payload["managed_plans"]])
        self.assertIn("stale-orchestration-row", {item["code"] for item in payload["diagnostics"]})

    def test_wrong_project_findings_exclude_plan_from_managed_state(self) -> None:
        self.run_cli("allocate", "DEMO", "managed")
        misplaced = self.root / "plan-hub" / "findings" / "DEMO-001"
        misplaced.mkdir(parents=True)

        payload = __import__("json").loads(self.run_cli("scan", "--json").stdout)
        lifecycle = [
            item for item in payload["diagnostics"]
            if item["code"] == "findings-lifecycle"
        ]
        self.assertEqual(1, len(lifecycle))
        self.assertIn("under wrong project", lifecycle[0]["message"])
        self.assertEqual([], payload["managed_plans"])
        blocked = self.run_cli("show", "DEMO-001", succeeds=False)
        self.assertIn("Findings directory is under wrong project", blocked.stderr)

    def test_registered_invalid_plan_does_not_block_unrelated_policy_operations(self) -> None:
        self.run_cli("allocate", "DEMO", "valid")
        self.run_cli("allocate", "DEMO", "invalid")
        orchestration = self.root / "ORCHESTRATION.md"
        orchestration.write_text(
            orchestration.read_text(encoding="utf-8").replace(
                "| `DEMO-002` | `demo-project` | — | — | — |",
                "| `DEMO-002` | `demo-project` | `DEMO-999` | — | — |",
            ),
            encoding="utf-8",
        )

        self.run_cli("depends", "DEMO-001")
        self.run_cli("status", "DEMO-001", "ready")
        self.run_cli("ready", "DEMO-001")
        self.run_cli("claim", "DEMO-001", "test-agent")
        allocated = self.run_cli("allocate", "DEMO", "next")
        self.assertIn("DEMO-003", allocated.stdout)
        invalid = self.run_cli("ready", "DEMO-002", succeeds=False)
        self.assertIn("unresolved dependency", invalid.stderr)

    def test_duplicate_raw_ids_are_diagnosed_as_ambiguous(self) -> None:
        project = self.root / "demo-project"
        project.mkdir()
        for name in ("first", "second"):
            (project / f"planning--DEMO-999--{name}.md").write_text(
                f"# {name}\n\nID: DEMO-999\nStatus: planning\n", encoding="utf-8"
            )

        payload = __import__("json").loads(self.run_cli("scan", "--json").stdout)
        ambiguity = [item for item in payload["diagnostics"] if item["code"] == "ambiguous-plan-id"]
        self.assertEqual(["DEMO-999"], [item["plan_id"] for item in ambiguity])

    def test_registered_plan_requires_exactly_one_id_and_status_field(self) -> None:
        self.run_cli("allocate", "DEMO", "metadata")
        plan = self.root / "demo-project" / "planning--DEMO-001--metadata.md"
        original = plan.read_text(encoding="utf-8")
        duplicate_fields = (
            ("ID: DEMO-999\n", "metadata-id-mismatch"),
            ("Status: ready\n", "lifecycle-mismatch"),
        )
        for extra, code in duplicate_fields:
            with self.subTest(extra=extra):
                plan.write_text(original + extra, encoding="utf-8")
                payload = __import__("json").loads(self.run_cli("scan", "--json").stdout)
                self.assertEqual([], payload["managed_plans"])
                self.assertIn(code, {item["code"] for item in payload["diagnostics"]})
                blocked = self.run_cli("show", "DEMO-001", succeeds=False)
                self.assertIn("exactly one matching", blocked.stderr)
        plan.write_text(original, encoding="utf-8")

    def test_metadata_invalid_registered_plan_does_not_block_unrelated_policy(self) -> None:
        self.run_cli("allocate", "DEMO", "valid")
        self.run_cli("allocate", "DEMO", "invalid")
        self.run_cli("status", "DEMO-001", "ready")
        invalid = self.root / "demo-project" / "planning--DEMO-002--invalid.md"
        invalid.write_text(
            invalid.read_text(encoding="utf-8") + "ID: DEMO-999\nStatus: ready\n",
            encoding="utf-8",
        )

        payload = __import__("json").loads(self.run_cli("scan", "--json").stdout)
        self.assertEqual(["DEMO-001"], [plan["id"] for plan in payload["managed_plans"]])
        self.assertNotIn("ambiguous-plan-id", {item["code"] for item in payload["diagnostics"]})
        self.run_cli("ready", "DEMO-001")
        self.run_cli("claim", "DEMO-001", "test-agent")
        allocated = self.run_cli("allocate", "DEMO", "next")
        self.assertIn("DEMO-003", allocated.stdout)
        blocked = self.run_cli("show", "DEMO-002", succeeds=False)
        self.assertIn("exactly one matching", blocked.stderr)

    def test_validate_rejects_impossible_utc_claim_timestamps(self) -> None:
        self.run_cli("allocate", "DEMO", "claimed")
        self.run_cli("status", "DEMO-001", "ready")
        orchestration = self.root / "ORCHESTRATION.md"
        baseline = orchestration.read_text(encoding="utf-8")
        timestamps = ("2026" + "-02" + "-30T12:00:00Z", "2026" + "-12" + "-01T24:00:00Z")
        for timestamp in timestamps:
            with self.subTest(timestamp=timestamp):
                orchestration.write_text(
                    baseline.replace("| `DEMO-001` | `demo-project` | — | — | — |",
                                     f"| `DEMO-001` | `demo-project` | — | `agent` | `{timestamp}` |"),
                    encoding="utf-8",
                )
                rejected = self.run_cli("validate", succeeds=False)
                self.assertIn("invalid claim timestamp", rejected.stderr)

    def test_repair_refuses_repeated_or_conflicting_id_and_status_metadata(self) -> None:
        project = self.root / "demo-project"
        project.mkdir()
        source = project / "planning--DEMO-999--Sample Plan.md"
        cases = (
            ("ID: DEMO-999\nID: DEMO-999\nStatus: planning\n", "metadata-id-mismatch"),
            ("ID: DEMO-999\nID: DEMO-998\nStatus: planning\n", "metadata-id-mismatch"),
            ("ID: DEMO-999\nStatus: planning\nStatus: planning\n", "lifecycle-mismatch"),
            ("ID: DEMO-999\nStatus: planning\nStatus: ready\n", "lifecycle-mismatch"),
        )
        for metadata, expected_code in cases:
            with self.subTest(metadata=metadata):
                source.write_text("# Sample plan\n\n" + metadata, encoding="utf-8")
                payload = __import__("json").loads(self.run_cli("repair", "--json").stdout)
                self.assertNotIn("automatic-safe", {item["classification"] for item in payload["proposals"]})
                self.assertIn(expected_code, {item.get("diagnostic_code") for item in payload["proposals"]})
                refused = self.run_cli("repair", "--apply", "--json", succeeds=False)
                self.assertIn("refusing --apply", refused.stderr)
                self.assertTrue(source.exists())
                self.assertFalse((project / "planning--DEMO-999--sample-plan.md").exists())

    def test_repair_is_dry_run_and_llm_handoff_never_mutates(self) -> None:
        project = self.root / "demo-project"
        project.mkdir()
        source = project / "planning--DEMO-001--Sample Plan.md"
        source.write_text("# Sample plan\n\nID: DEMO-001\nStatus: planning\n", encoding="utf-8")
        original_orchestration = (self.root / "ORCHESTRATION.md").read_text(encoding="utf-8")

        dry_run = self.run_cli("repair", "--json")
        payload = __import__("json").loads(dry_run.stdout)
        self.assertEqual("automatic-safe", payload["proposals"][0]["classification"])
        self.assertTrue(source.exists())
        self.assertFalse((project / "planning--DEMO-001--sample-plan.md").exists())

        llm = self.run_cli("repair", "--llm", "--json")
        handoff = __import__("json").loads(llm.stdout)
        self.assertTrue(handoff["review_required"])
        self.assertFalse(handoff["may_commit_or_push"])
        self.assertTrue(source.exists())

        applied = self.run_cli("repair", "--apply", "--json")
        self.assertIn('"applied": true', applied.stdout)
        self.assertFalse(source.exists())
        self.assertTrue((project / "planning--DEMO-001--sample-plan.md").exists())
        self.assertEqual(original_orchestration, (self.root / "ORCHESTRATION.md").read_text(encoding="utf-8"))
        scan = __import__("json").loads(self.run_cli("scan", "--json").stdout)
        self.assertEqual([], scan["managed_plans"])


class MultiRootTest(unittest.TestCase):
    """Multi-root registry, roots/locate commands, and root-selection precedence."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.empty_home = self.base / "empty-home"
        self.empty_home.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def make_hub(self, path: Path) -> Path:
        (path / ".git").mkdir(parents=True)
        (path / ".pi-subagents").mkdir()
        (path / "README.md").write_text("# Plans\n", encoding="utf-8")
        (path / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
        (path / "ORCHESTRATION.md").write_text(ORCHESTRATION, encoding="utf-8")
        return path

    def write_config(self, roots: list[dict[str, str]] | None = None, *, raw: str | None = None,
                     version: int = 1) -> Path:
        config = self.home / ".config" / "plans-hub" / "roots.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        if raw is None:
            raw = json.dumps({"schema_version": version, "roots": roots if roots is not None else []})
        config.write_text(raw, encoding="utf-8")
        return config

    def env(self, **overrides: str) -> dict[str, str]:
        environment = os.environ.copy()
        environment["HOME"] = str(self.home)
        environment.update(overrides)
        return environment

    def run_cli(self, *arguments: str, env: dict[str, str] | None = None, cwd: Path | None = None,
                succeeds: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["python3", str(SCRIPT), *arguments],
            text=True,
            capture_output=True,
            check=False,
            env=env if env is not None else self.env(),
            cwd=cwd,
        )
        if succeeds and result.returncode != 0:
            self.fail(f"command failed: {arguments}\nstdout={result.stdout}\nstderr={result.stderr}")
        if not succeeds and result.returncode == 0:
            self.fail(f"command unexpectedly passed: {arguments}\nstdout={result.stdout}")
        return result

    def assert_config_error(self, fragment: str) -> None:
        result = self.run_cli("roots", succeeds=False)
        self.assertIn("roots registry", result.stderr)
        self.assertIn(fragment, result.stderr)

    def test_missing_default_config_means_no_registry(self) -> None:
        result = self.run_cli("roots")
        self.assertIn("No plan hubs configured", result.stdout)

    def test_config_strict_validation(self) -> None:
        self.write_config(raw="{ not json")
        self.assert_config_error("not valid JSON")
        self.write_config(raw="[]")
        self.assert_config_error("top level must be a JSON object")
        self.write_config(raw='{"schema_version": 1}')
        self.assert_config_error("roots must be a JSON array")
        self.write_config([{"name": "alpha", "path": "/hub-a"}], version=2)
        self.assert_config_error("schema_version must be 1")
        self.write_config([{"name": "alpha", "path": "/hub-a"}, {"name": "alpha", "path": "/hub-b"}])
        self.assert_config_error("duplicates root name 'alpha'")
        self.write_config([{"name": "alpha", "path": "/hub-a"}, {"name": "beta", "path": "/hub-a"}])
        self.assert_config_error("duplicates root path '/hub-a'")
        self.write_config([{"name": "Alpha", "path": "/hub-a"}])
        self.assert_config_error("name must be a lowercase kebab-case string")
        self.write_config([{"name": "alpha", "path": "relative/hub"}])
        self.assert_config_error("path must be absolute")
        self.write_config([{"name": "alpha"}])
        self.assert_config_error("path must be an absolute path string")
        self.write_config([{"path": "/hub-a"}])
        self.assert_config_error("name must be a lowercase kebab-case string")

    def test_root_precedence_matrix(self) -> None:
        alpha = self.make_hub(self.base / "alpha-hub")
        beta = self.make_hub(self.base / "beta-hub")
        self.write_config([
            {"name": "alpha", "path": str(alpha)},
            {"name": "beta", "path": str(beta)},
        ])

        # --root as an existing path beats the registry.
        result = self.run_cli("--root", str(beta), "validate")
        self.assertIn(str(beta.resolve()), result.stdout)

        # --root otherwise resolves as a registry root name.
        result = self.run_cli("--root", "alpha", "validate")
        self.assertIn(str(alpha.resolve()), result.stdout)

        # A multi-root registry never guesses for single-hub commands.
        result = self.run_cli("validate", succeeds=False)
        self.assertIn("multiple plan hubs configured (alpha, beta)", result.stderr)

        # A single-root registry is used when no --root selects a hub.
        self.write_config([{"name": "alpha", "path": str(alpha)}])
        result = self.run_cli("validate")
        self.assertIn(str(alpha.resolve()), result.stdout)

        # Nothing configured: selecting a hub fails.
        result = self.run_cli("validate", env=self.env(HOME=str(self.empty_home)), succeeds=False)
        self.assertIn("no plan hub selected", result.stderr)

    def test_root_value_matching_existing_directory_and_name_resolves_as_path(self) -> None:
        registered = self.make_hub(self.base / "registered-hub")
        self.write_config([{"name": "alpha", "path": str(registered)}])
        workspace = self.base / "workspace"
        collision = self.make_hub(workspace / "alpha")

        result = self.run_cli("--root", "alpha", "validate", cwd=workspace)

        self.assertIn(str(collision.resolve()), result.stdout)
        self.assertNotIn(str(registered.resolve()), result.stdout)

    def test_roots_and_locate_bypass_the_single_root_requirement(self) -> None:
        alpha = self.make_hub(self.base / "alpha-hub")
        beta = self.make_hub(self.base / "beta-hub")
        self.write_config([
            {"name": "alpha", "path": str(alpha)},
            {"name": "beta", "path": str(beta)},
        ])

        # Only a registry: both commands succeed without selecting a single root.
        roots = self.run_cli("roots")
        self.assertIn("alpha", roots.stdout)
        locate = self.run_cli("locate", "DEMO-001")
        self.assertIn("Result: not-found", locate.stdout)

        # With nothing configured at all both commands still exit 0.
        roots = self.run_cli("roots", env=self.env(HOME=str(self.empty_home)))
        self.assertIn("No plan hubs configured", roots.stdout)
        locate = self.run_cli("locate", "DEMO-001", env=self.env(HOME=str(self.empty_home)))
        self.assertIn("Result: not-found", locate.stdout)

    def test_roots_reports_validity_and_sources(self) -> None:
        ok_hub = self.make_hub(self.base / "ok-hub")
        missing = self.base / "missing-hub"
        not_a_hub = self.base / "not-a-hub"
        not_a_hub.mkdir()
        self.write_config([
            {"name": "ok", "path": str(ok_hub)},
            {"name": "lost", "path": str(missing)},
            {"name": "raw", "path": str(not_a_hub)},
        ])

        payload = json.loads(self.run_cli("roots", "--json").stdout)
        self.assertEqual(1, payload["schema_version"])
        self.assertEqual([], payload["warnings"])
        by_name = {entry["name"]: entry for entry in payload["roots"]}
        self.assertEqual("ok", by_name["ok"]["validity"])
        self.assertEqual("missing-path", by_name["lost"]["validity"])
        self.assertEqual("not-a-hub", by_name["raw"]["validity"])
        self.assertEqual({"config"}, {entry["source"] for entry in payload["roots"]})
        self.assertEqual(str(ok_hub.resolve()), by_name["ok"]["path"])

        # --root overrides the registry with a single command-line-sourced root.
        payload = json.loads(self.run_cli("--root", str(ok_hub), "roots", "--json").stdout)
        self.assertEqual(1, len(payload["roots"]))
        self.assertIsNone(payload["roots"][0]["name"])
        self.assertEqual("--root", payload["roots"][0]["source"])

        # Nothing configured: an empty set with a note, still exit 0.
        payload = json.loads(self.run_cli("roots", "--json", env=self.env(HOME=str(self.empty_home))).stdout)
        self.assertEqual([], payload["roots"])

    def test_locate_unique_not_found_and_ambiguous(self) -> None:
        alpha = self.make_hub(self.base / "alpha-hub")
        beta = self.make_hub(self.base / "beta-hub")
        self.write_config([
            {"name": "alpha", "path": str(alpha)},
            {"name": "beta", "path": str(beta)},
        ])
        self.run_cli("--root", str(alpha), "allocate", "DEMO", "alpha-plan")

        unique = self.run_cli("locate", "demo-001")
        self.assertIn("Result: unique", unique.stdout)
        self.assertIn(f"Hub: alpha ({alpha.resolve()})", unique.stdout)
        self.assertIn("Project: demo-project", unique.stdout)
        self.assertIn("Status: planning", unique.stdout)
        self.assertIn("planning--DEMO-001--alpha-plan.md", unique.stdout)

        missing = self.run_cli("locate", "DEMO-042")
        self.assertIn("Result: not-found", missing.stdout)
        self.assertIn("Searched hubs: alpha, beta", missing.stdout)

        self.run_cli("--root", str(beta), "allocate", "DEMO", "beta-plan")
        ambiguous = self.run_cli("locate", "DEMO-001")
        self.assertIn("Result: ambiguous", ambiguous.stdout)
        self.assertIn(str(alpha.resolve()), ambiguous.stdout)
        self.assertIn(str(beta.resolve()), ambiguous.stdout)

        payload = json.loads(self.run_cli("locate", "DEMO-001", "--json").stdout)
        self.assertEqual(1, payload["schema_version"])
        self.assertEqual("DEMO-001", payload["id"])
        self.assertEqual("ambiguous", payload["result"])
        self.assertEqual(["alpha", "beta"], sorted(hit["root"] for hit in payload["hits"]))
        for hit in payload["hits"]:
            self.assertIn(hit["hub"], {str(alpha.resolve()), str(beta.resolve())})
            self.assertEqual("demo-project", hit["project"])
            self.assertEqual("planning", hit["status"])
            self.assertTrue(hit["plan_path"].startswith(hit["hub"]))
        self.assertEqual(["alpha", "beta"], [entry["name"] for entry in payload["searched"]])
        self.assertEqual([], payload["warnings"])

    def test_locate_skips_unreachable_roots_with_a_warning(self) -> None:
        alpha = self.make_hub(self.base / "alpha-hub")
        missing = self.base / "missing-hub"
        self.write_config([
            {"name": "alpha", "path": str(alpha)},
            {"name": "gone", "path": str(missing)},
        ])
        self.run_cli("--root", str(alpha), "allocate", "DEMO", "alpha-plan")

        result = self.run_cli("locate", "DEMO-001")

        self.assertIn("Result: unique", result.stdout)
        self.assertIn("skipped root gone: missing-path", result.stderr)
        payload = json.loads(self.run_cli("locate", "DEMO-001", "--json").stdout)
        self.assertEqual(["alpha"], [entry["name"] for entry in payload["searched"]])
        self.assertEqual(1, len(payload["warnings"]))

    def test_locate_reports_ambiguous_mentions_as_warnings_never_hits(self) -> None:
        alpha = self.make_hub(self.base / "alpha-hub")
        beta = self.make_hub(self.base / "beta-hub")
        self.write_config([
            {"name": "alpha", "path": str(alpha)},
            {"name": "beta", "path": str(beta)},
        ])
        project = alpha / "demo-project"
        project.mkdir()
        for name in ("draft-a.md", "draft-b.md"):
            (project / name).write_text("# Draft\n\nID: DEMO-001\nStatus: planning\n", encoding="utf-8")

        result = self.run_cli("locate", "DEMO-001")
        self.assertIn("Result: not-found", result.stdout)
        self.assertIn("DEMO-001", result.stderr)
        self.assertIn("ambiguous", result.stderr.lower())

        self.run_cli("--root", str(beta), "allocate", "DEMO", "beta-plan")
        result = self.run_cli("locate", "DEMO-001")
        self.assertIn("Result: unique", result.stdout)
        self.assertIn(f"Hub: beta ({beta.resolve()})", result.stdout)
        self.assertIn("ambiguous", result.stderr.lower())

    def test_locate_never_hits_registered_but_invalid_plans(self) -> None:
        alpha = self.make_hub(self.base / "alpha-hub")
        beta = self.make_hub(self.base / "beta-hub")
        self.write_config([
            {"name": "alpha", "path": str(alpha)},
            {"name": "beta", "path": str(beta)},
        ])
        self.run_cli("--root", str(alpha), "allocate", "DEMO", "first")
        self.run_cli("--root", str(alpha), "allocate", "DEMO", "second")
        orchestration = alpha / "ORCHESTRATION.md"
        text = orchestration.read_text(encoding="utf-8")
        text = text.replace(
            "| `DEMO-001` | `demo-project` | — |", "| `DEMO-001` | `demo-project` | `DEMO-002` |"
        )
        text = text.replace(
            "| `DEMO-002` | `demo-project` | — |", "| `DEMO-002` | `demo-project` | `DEMO-001` |"
        )
        orchestration.write_text(text, encoding="utf-8")

        result = self.run_cli("locate", "DEMO-001")

        self.assertIn("Result: not-found", result.stdout)
        self.assertIn("Searched hubs: alpha, beta", result.stdout)

    def test_unknown_id_hint_requires_a_multi_root_registry(self) -> None:
        alpha = self.make_hub(self.base / "alpha-hub")
        beta = self.make_hub(self.base / "beta-hub")
        self.write_config([
            {"name": "alpha", "path": str(alpha)},
            {"name": "beta", "path": str(beta)},
        ])

        hinted = self.run_cli("--root", str(alpha), "show", "DEMO-009", succeeds=False)
        self.assertIn("run 'planctl locate DEMO-009' to search all configured hubs", hinted.stderr)

        self.write_config([{"name": "alpha", "path": str(alpha)}])
        plain = self.run_cli("--root", str(alpha), "show", "DEMO-009", succeeds=False)
        self.assertNotIn("planctl locate", plain.stderr)

        no_registry = self.run_cli(
            "--root", str(alpha), "show", "DEMO-009",
            env=self.env(HOME=str(self.empty_home)),
            succeeds=False,
        )
        self.assertNotIn("planctl locate", no_registry.stderr)


if __name__ == "__main__":
    unittest.main()
