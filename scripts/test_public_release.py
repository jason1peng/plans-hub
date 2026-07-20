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


if __name__ == "__main__":
    unittest.main()
