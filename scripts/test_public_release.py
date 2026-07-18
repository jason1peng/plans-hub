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
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "Test")
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

    def guard(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(GUARD), "--root", str(self.root), "--history"],
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
        self.assertIn("index: credential-bearing URL in README.md", result.stderr)

    def test_rejects_non_synthetic_identifier_without_forbid_option(self) -> None:
        private_identifier = "ACME" + "-999"
        (self.root / "README.md").write_text(f"Private plan: {private_identifier}\n", encoding="utf-8")
        self.git("add", "README.md")

        result = self.guard()

        self.assertNotEqual(0, result.returncode)
        self.assertIn("index: non-synthetic plan identifier", result.stderr)
        self.assertIn(private_identifier, result.stderr)

    def test_rejects_unapproved_project_marker_without_forbid_option(self) -> None:
        private_project = "acme" + "-service"
        (self.root / "README.md").write_text(f"Private project: {private_project}\n", encoding="utf-8")
        self.git("add", "README.md")

        result = self.guard()

        self.assertNotEqual(0, result.returncode)
        self.assertIn("index: unapproved project-like marker", result.stderr)
        self.assertIn(private_project, result.stderr)


if __name__ == "__main__":
    unittest.main()
