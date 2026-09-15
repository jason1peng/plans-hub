#!/usr/bin/env python3
"""Regression tests for the public release guard."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

GUARD = Path(__file__).with_name("check_public_release.py")


class PublicReleaseGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.git("init", "-q")
        self.git("config", "user.email", "demo" + "-user@example.invalid")
        self.git("config", "user.name", "demo" + "-user")
        (self.root / "README.md").write_text("# Synthetic public client\n", encoding="utf-8")
        self.git("add", "README.md")
        self.git("commit", "-qm", "initial")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def git(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.root), *arguments],
            text=True,
            capture_output=True,
            check=True,
        )

    def guard(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(GUARD), "--root", str(self.root), "--history", *arguments],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_scans_staged_blob_instead_of_safe_worktree_copy(self) -> None:
        readme = self.root / "README.md"
        credential_url = "https://user" + ":secret@example.invalid/private\n"
        readme.write_text(credential_url, encoding="utf-8")
        self.git("add", "README.md")
        readme.write_text("# Safe worktree copy\n", encoding="utf-8")

        result = self.guard()

        self.assertNotEqual(0, result.returncode)
        self.assertIn("index: README.md: credential-bearing URL", result.stderr)

    def test_scans_unsafe_worktree_copy_when_index_is_safe(self) -> None:
        readme = self.root / "README.md"
        credential_url = "https://user" + ":secret@example.invalid/private\n"
        readme.write_text(credential_url, encoding="utf-8")

        result = self.guard()

        self.assertNotEqual(0, result.returncode)
        self.assertIn("working tree: README.md: credential-bearing URL", result.stderr)
        self.assertNotIn("index: README.md: credential-bearing URL", result.stderr)

    def test_scans_unsafe_history_when_index_and_worktree_are_safe(self) -> None:
        readme = self.root / "README.md"
        credential_url = "https://user" + ":secret@example.invalid/private\n"
        readme.write_text(credential_url, encoding="utf-8")
        self.git("add", "README.md")
        self.git("commit", "-qm", "unsafe")
        readme.write_text("# Safe current copy\n", encoding="utf-8")
        self.git("add", "README.md")
        self.git("commit", "-qm", "safe")

        result = self.guard()

        self.assertNotEqual(0, result.returncode)
        self.assertIn("README.md: credential-bearing URL", result.stderr)
        self.assertNotIn("working tree: README.md: credential-bearing URL", result.stderr)
        self.assertNotIn("index: README.md: credential-bearing URL", result.stderr)

    def test_rejects_untracked_and_ignored_worktree_files(self) -> None:
        credential_url = "https://user" + ":secret@example.invalid/private\n"
        (self.root / "untracked-secret.txt").write_text(credential_url, encoding="utf-8")
        (self.root / ".gitignore").write_text("ignored-secret.txt\n", encoding="utf-8")
        (self.root / "ignored-secret.txt").write_text("private data\n", encoding="utf-8")

        result = self.guard()

        self.assertNotEqual(0, result.returncode)
        self.assertIn("working tree: unexpected top-level path: untracked-secret.txt", result.stderr)
        self.assertIn("working tree: untracked-secret.txt: credential-bearing URL", result.stderr)
        self.assertIn("working tree: unexpected top-level path: ignored-secret.txt", result.stderr)

    def test_allows_hyphenated_author_identity(self) -> None:
        result = self.guard()

        self.assertEqual(0, result.returncode, result.stderr)

    def test_still_rejects_forbidden_fragment_in_author_identity(self) -> None:
        private_fragment = "author" + "-secret"
        self.git("config", "user.name", private_fragment)
        self.git("commit", "--allow-empty", "-qm", "safe metadata check")

        result = self.guard("--forbid", private_fragment)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("commit metadata: forbidden private fragment", result.stderr)
        self.assertIn(private_fragment, result.stderr)

    def test_rejects_private_identifier_in_reachable_commit_message(self) -> None:
        private_identifier = "ACME" + "-999"
        self.git("commit", "--allow-empty", "-qm", f"mentions {private_identifier}")

        result = self.guard()

        self.assertNotEqual(0, result.returncode)
        self.assertIn("commit metadata message: non-synthetic plan identifier", result.stderr)
        self.assertIn(private_identifier, result.stderr)

    def test_rejects_non_synthetic_identifier_without_forbid_option(self) -> None:
        private_identifier = "ACME" + "-999"
        (self.root / "README.md").write_text(f"Private plan: {private_identifier}\n", encoding="utf-8")
        self.git("add", "README.md")

        result = self.guard()

        self.assertNotEqual(0, result.returncode)
        self.assertIn("index: README.md: non-synthetic plan identifier", result.stderr)
        self.assertIn(private_identifier, result.stderr)

    def test_allows_project_and_worktree_names_without_global_configuration(self) -> None:
        project_name = "acme" + "-service"
        worktree_name = "feature" + "-absolute-plan-path"
        (self.root / "README.md").write_text(
            f"Project: {project_name}\nWorktree: {worktree_name}\n",
            encoding="utf-8",
        )
        self.git("add", "README.md")

        result = self.guard()

        self.assertEqual(0, result.returncode, result.stderr)

    def test_rejects_caller_supplied_private_project_fragment(self) -> None:
        private_project = "acme" + "-service"
        (self.root / "README.md").write_text(f"Private project: {private_project}\n", encoding="utf-8")
        self.git("add", "README.md")

        result = self.guard("--forbid", private_project)

        self.assertNotEqual(0, result.returncode)
        self.assertIn("index: README.md: forbidden private fragment", result.stderr)
        self.assertIn(private_project, result.stderr)

    def test_rejects_private_research_state_paths_but_allows_public_template(self) -> None:
        template = self.root / "templates" / "hub"
        template.mkdir(parents=True)
        (template / "RESEARCH.md").write_text("# Synthetic registry\n", encoding="utf-8")
        self.git("add", "templates")
        self.git("commit", "-qm", "add public research template")

        private = self.root / "research"
        private.mkdir()
        research_id = "RES" + "-001"
        private_name = "open--" + research_id + "--private.md"
        (private / private_name).write_text("private\n", encoding="utf-8")
        (self.root / "RESEARCH.md").write_text("private registry\n", encoding="utf-8")

        result = self.guard()

        self.assertNotEqual(0, result.returncode)
        self.assertIn("private plan-state path: research/" + private_name, result.stderr)
        self.assertIn("private plan-state path: RESEARCH.md", result.stderr)
        self.assertNotIn("templates/hub/RESEARCH.md", result.stderr)

    def test_skill_distinguishes_discovery_locations_and_carries_resolved_root(self) -> None:
        repository = GUARD.parent.parent
        skill = (repository / "skills" / "shared-plan-storage" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("Skill location is not storage", skill)
        self.assertIn("~/.agents/skills/shared-plan-storage", skill)
        self.assertIn("~/.pi/agent/skills/shared-plan-storage", skill)
        self.assertIn("~/.config/plans-hub/roots.json", skill)
        self.assertIn("HUB_ROOT", skill)
        self.assertIn('planctl --root \"$HUB_ROOT\"', skill)
        self.assertIn('git -C \"$HUB_ROOT\"', skill)
        self.assertIn("planctl roots --json", skill)
        self.assertIn("planctl locate <ID> --json", skill)

    def test_readme_single_hub_agent_examples_carry_resolved_root(self) -> None:
        repository = GUARD.parent.parent
        readme = (repository / "README.md").read_text(encoding="utf-8")

        for command in ("validate", "scan", "repair"):
            self.assertNotRegex(
                readme,
                rf"(?m)^\s*(?:scripts/)?planctl(?:\.py)?\s+{command}(?:\s|$)",
                f"README contains an unflagged agent example for {command}",
            )
            self.assertRegex(
                readme,
                rf"(?m)^\s*scripts/planctl\.py --root \"\$HUB_ROOT\"\s+{command}(?:\s|$)",
                f"README is missing a carried HUB_ROOT example for {command}",
            )

    def test_lifecycle_docs_distinguish_transition_gate_claim_and_handoff(self) -> None:
        repository = GUARD.parent.parent
        documents = {
            "README.md": repository / "README.md",
            "canonical skill": repository / "skills" / "shared-plan-storage" / "SKILL.md",
            "hub agent template": repository / "templates" / "hub" / "AGENTS.md",
        }
        commands = (
            'planctl --root "$HUB_ROOT" status <ID> ready',
            'planctl --root "$HUB_ROOT" ready <ID>',
            'planctl --root "$HUB_ROOT" claim <ID> <agent>',
        )
        handoff_fields = (
            "Plan:",
            "Path:",
            "Owner:",
            "Phase:",
            "Scope:",
            "Non-goals:",
            "Acceptance evidence:",
            "Delivery requirements:",
            "Return artifacts:",
        )
        for name, path in documents.items():
            text = path.read_text(encoding="utf-8")
            lifecycle = text[text.index("## Plan lifecycle"):]
            positions = []
            for command in commands:
                self.assertIn(command, lifecycle, f"{name} is missing {command}")
                positions.append(lifecycle.index(command))
            self.assertEqual(sorted(positions), positions, f"{name} changes the lifecycle order")
            self.assertIn("read-only", lifecycle.lower(), f"{name} does not mark ready as read-only")
            self.assertIn("fresh worktree", lifecycle.lower(), f"{name} omits the fresh-worktree boundary")
            self.assertIn("delivery state machine", lifecycle.lower(), f"{name} omits delivery-state-machine guidance")
            self.assertIn("intercom", lifecycle.lower(), f"{name} omits the intercom boundary")
            self.assertIn(
                "intercom receipt is not completion evidence", lifecycle.lower(),
                f"{name} weakens the intercom boundary",
            )
            self.assertIn("Handoff version: 1", lifecycle, f"{name} omits handoff versioning")
            for field in handoff_fields:
                self.assertIn(field, lifecycle, f"{name} is missing handoff field {field}")

        readme = documents["README.md"].read_text(encoding="utf-8")
        self.assertIn("planctl roots --json", readme)
        self.assertIn("planctl locate <ID> --json", readme)
        self.assertIn('"$PLANCTL" --root "$HUB_ROOT"', readme)
        self.assertNotIn("status-ready", readme)

    def test_skill_allows_implementation_after_own_claim(self) -> None:
        repository = GUARD.parent.parent
        skill = (repository / "skills" / "shared-plan-storage" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn(
            "Do not implement blocked or planning work, or plans claimed by another agent.",
            skill,
        )
        self.assertIn(
            "After your own successful readiness gate and claim, implementation may begin.",
            skill,
        )
        self.assertNotIn("Do not implement blocked, planning, or claimed work.", skill)


if __name__ == "__main__":
    unittest.main()
