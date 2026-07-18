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


if __name__ == "__main__":
    unittest.main()
