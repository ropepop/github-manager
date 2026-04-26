from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from github_manager.scanner import scan_projects


class ScannerTests(unittest.TestCase):
    def test_detects_monorepo_root_instead_of_nested_workload_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            documents = Path(tmp) / "Documents"
            root = documents / "pixel-phone"
            nested = root / "workloads" / "train-bot"
            (root / ".github" / "workflows").mkdir(parents=True)
            nested.mkdir(parents=True)
            (root / "README.md").write_text("# Pixel Phone\n", encoding="utf-8")
            (root / "LICENSE").write_text("MIT\n", encoding="utf-8")
            (nested / "go.mod").write_text("module train\n", encoding="utf-8")

            candidates = scan_projects(documents)

            self.assertEqual([candidate.path for candidate in candidates], [root.resolve()])
            self.assertEqual(candidates[0].slug, "pixel-phone")

    def test_can_include_nested_project_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            documents = Path(tmp) / "Documents"
            root = documents / "ops"
            nested = root / "automation" / "task-executor"
            nested.mkdir(parents=True)
            (root / "README.md").write_text("# Ops\n", encoding="utf-8")
            (root / ".github").mkdir()
            (nested / "module.yaml").write_text("name: task-executor\n", encoding="utf-8")
            (nested / "README.md").write_text("# Task Executor\n", encoding="utf-8")

            candidates = scan_projects(documents, collapse_nested=False, respect_git_roots=False)

            self.assertIn(root.resolve(), [candidate.path for candidate in candidates])
            self.assertIn(nested.resolve(), [candidate.path for candidate in candidates])


if __name__ == "__main__":
    unittest.main()
