from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from github_manager.sanitizer import prepare_project


class SanitizerTests(unittest.TestCase):
    def test_blocks_risky_files_and_keeps_source_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "project"
            staging = tmp_path / "staging"
            source.mkdir()
            (source / "README.md").write_text("# Project\n", encoding="utf-8")
            fake_token = "ghp_" + "123456789012345678901234567890"
            (source / ".env").write_text(f"TOKEN={fake_token}\n", encoding="utf-8")
            (source / "app.py").write_text("print('ok')\n", encoding="utf-8")
            (source / "data.db").write_bytes(b"sqlite data")
            (source / "node_modules").mkdir()
            (source / "node_modules" / "dep.js").write_text("junk\n", encoding="utf-8")

            prepared = prepare_project(source, staging, "project")

            self.assertFalse(prepared.clean)
            self.assertTrue((source / ".env").exists())
            self.assertTrue((source / "data.db").exists())
            self.assertTrue((prepared.staged_path / "README.md").exists())
            self.assertTrue((prepared.staged_path / "app.py").exists())
            self.assertFalse((prepared.staged_path / ".env").exists())
            self.assertFalse((prepared.staged_path / "data.db").exists())
            self.assertFalse((prepared.staged_path / "node_modules").exists())
            self.assertIsNotNone(prepared.readme_result)
            self.assertEqual(prepared.readme_result.mode, "local-generated")

    def test_drop_blocked_files_cleans_staged_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "project"
            staging = tmp_path / "staging"
            source.mkdir()
            (source / "README.md").write_text("# Project\n", encoding="utf-8")
            local_path = "/Users/" + "example/private"
            (source / "notes.md").write_text(f"local path {local_path}\n", encoding="utf-8")
            (source / "app.py").write_text("print('ok')\n", encoding="utf-8")

            prepared = prepare_project(source, staging, "project", drop_blocked_files=True)

            self.assertTrue(prepared.clean)
            self.assertTrue((source / "notes.md").exists())
            self.assertFalse((prepared.staged_path / "notes.md").exists())
            self.assertTrue((prepared.staged_path / "app.py").exists())
            self.assertEqual(len(prepared.removed_findings), 1)

    def test_allows_env_example(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "project"
            staging = tmp_path / "staging"
            source.mkdir()
            (source / ".env.example").write_text("TOKEN=\n", encoding="utf-8")
            (source / "service.example.env").write_text("TOKEN=\n", encoding="utf-8")
            (source / "README.md").write_text("# Project\n", encoding="utf-8")

            prepared = prepare_project(source, staging, "project")

            self.assertTrue(prepared.clean)
            self.assertTrue((prepared.staged_path / ".env.example").exists())
            self.assertTrue((prepared.staged_path / "service.example.env").exists())


if __name__ == "__main__":
    unittest.main()
