from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from github_manager.models import GitHubRepo
from github_manager.syncer import sync_staged_project


class SyncerTests(unittest.TestCase):
    def test_sync_replaces_remote_only_files_with_local_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bare = tmp_path / "remote.git"
            seed = tmp_path / "seed"
            staged = tmp_path / "staged"
            workspace = tmp_path / "workspace"
            _run(["git", "init", "--bare", str(bare)], tmp_path)
            _run(["git", "clone", str(bare), str(seed)], tmp_path)
            (seed / "README.md").write_text("remote\n", encoding="utf-8")
            (seed / "remote-only.txt").write_text("remove me\n", encoding="utf-8")
            _run(["git", "add", "-A"], seed)
            _run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "seed"], seed)
            _run(["git", "push", "origin", "HEAD:main"], seed)
            _run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], bare)

            staged.mkdir()
            (staged / "README.md").write_text("local\n", encoding="utf-8")
            repo = GitHubRepo(owner="local", name="remote", url=str(bare))

            detail = sync_staged_project(repo, staged, workspace, dry_run=False)

            verify = tmp_path / "verify"
            _run(["git", "clone", str(bare), str(verify)], tmp_path)
            self.assertIn("Synced", detail)
            self.assertEqual((verify / "README.md").read_text(encoding="utf-8"), "local\n")
            self.assertFalse((verify / "remote-only.txt").exists())

    def test_dry_run_compares_without_pushing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bare = tmp_path / "remote.git"
            seed = tmp_path / "seed"
            staged = tmp_path / "staged"
            workspace = tmp_path / "workspace"
            _run(["git", "init", "--bare", str(bare)], tmp_path)
            _run(["git", "clone", str(bare), str(seed)], tmp_path)
            (seed / "README.md").write_text("remote\n", encoding="utf-8")
            _run(["git", "add", "-A"], seed)
            _run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "seed"], seed)
            _run(["git", "push", "origin", "HEAD:main"], seed)
            _run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], bare)

            staged.mkdir()
            (staged / "README.md").write_text("local\n", encoding="utf-8")
            repo = GitHubRepo(owner="local", name="remote", url=str(bare))

            detail = sync_staged_project(repo, staged, workspace, dry_run=True)

            verify = tmp_path / "verify"
            _run(["git", "clone", str(bare), str(verify)], tmp_path)
            self.assertIn("Would sync", detail)
            self.assertEqual((verify / "README.md").read_text(encoding="utf-8"), "remote\n")


def _run(args: list[str], cwd: Path) -> None:
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
