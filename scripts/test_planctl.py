#!/usr/bin/env python3
"""Behavior tests for planctl and shared-plan validation."""

from __future__ import annotations

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

    def test_root_is_required_and_explicit_root_overrides_environment(self) -> None:
        environment = os.environ.copy()
        environment.pop("PLANS_ROOT", None)
        missing = subprocess.run(
            ["python3", str(SCRIPT), "validate"],
            text=True,
            capture_output=True,
            check=False,
            env=environment,
        )
        self.assertNotEqual(0, missing.returncode)
        self.assertIn("no plan hub selected", missing.stderr)

        environment["PLANS_ROOT"] = str(self.root / "missing")
        explicit = subprocess.run(
            ["python3", str(SCRIPT), "--root", str(self.root), "validate"],
            text=True,
            capture_output=True,
            check=False,
            env=environment,
        )
        self.assertEqual(0, explicit.returncode, explicit.stderr)

    def test_documented_existing_hub_onboarding_upgrades_and_uses_installed_wrapper(self) -> None:
        # The supported order is: select PLANS_ROOT, install, then invoke the full wrapper path.
        legacy_skill = self.root.resolve() / "skills" / "shared-plan-storage"
        self.assertFalse(legacy_skill.exists(), "exercise the dangling post-migration legacy link")

        with tempfile.TemporaryDirectory() as home_name:
            home = Path(home_name)
            destination = home / ".agents" / "skills" / "shared-plan-storage"
            destination.parent.mkdir(parents=True)
            destination.symlink_to(legacy_skill)
            environment = {**os.environ, "HOME": str(home), "PLANS_ROOT": str(self.root)}
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
                environment = {**os.environ, "HOME": str(home), "PLANS_ROOT": str(self.root)}

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
            environment = {**os.environ, "PLANS_ROOT": str(worker)}
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
                    env=environment,
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

        self.run_cli("show", "demo-001")
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


if __name__ == "__main__":
    unittest.main()
