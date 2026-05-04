from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from github_manager.models import GitHubRepo
from github_manager.syncer import sync_private_project, sync_staged_project


class SyncerTests(unittest.TestCase):
    def test_private_sync_commits_tracked_deletions_and_untracked_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bare = tmp_path / "remote.git"
            local = tmp_path / "local"
            _seed_private_repo(tmp_path, bare, local)

            (local / "README.md").write_text("changed\n", encoding="utf-8")
            (local / "remove.txt").unlink()
            (local / "new.txt").write_text("new\n", encoding="utf-8")
            repo = GitHubRepo(owner="local", name="private", url=str(bare), is_private=True)

            detail = sync_private_project(repo, local, dry_run=False)

            verify = tmp_path / "verify"
            _run(["git", "clone", str(bare), str(verify)], tmp_path)
            self.assertIn("Committed and pushed", detail)
            self.assertEqual((verify / "README.md").read_text(encoding="utf-8"), "changed\n")
            self.assertEqual((verify / "new.txt").read_text(encoding="utf-8"), "new\n")
            self.assertFalse((verify / "remove.txt").exists())

    def test_private_sync_reports_no_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bare = tmp_path / "remote.git"
            local = tmp_path / "local"
            _seed_private_repo(tmp_path, bare, local)
            repo = GitHubRepo(owner="local", name="private", url=str(bare), is_private=True)

            detail = sync_private_project(repo, local, dry_run=False)

            self.assertIn("No private changes", detail)

    def test_private_sync_dry_run_does_not_commit_or_push(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bare = tmp_path / "remote.git"
            local = tmp_path / "local"
            _seed_private_repo(tmp_path, bare, local)
            (local / "README.md").write_text("changed\n", encoding="utf-8")
            repo = GitHubRepo(owner="local", name="private", url=str(bare), is_private=True)

            detail = sync_private_project(repo, local, dry_run=True)

            verify = tmp_path / "verify"
            _run(["git", "clone", str(bare), str(verify)], tmp_path)
            self.assertIn("Would commit and push", detail)
            self.assertEqual((verify / "README.md").read_text(encoding="utf-8"), "original\n")

    def test_private_sync_bypasses_local_commit_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            bare = tmp_path / "remote.git"
            local = tmp_path / "local"
            _seed_private_repo(tmp_path, bare, local)
            hook = local / ".git" / "hooks" / "pre-commit"
            hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            hook.chmod(0o755)
            (local / "README.md").write_text("changed despite hook\n", encoding="utf-8")
            repo = GitHubRepo(owner="local", name="private", url=str(bare), is_private=True)

            detail = sync_private_project(repo, local, dry_run=False)

            verify = tmp_path / "verify-hook"
            _run(["git", "clone", str(bare), str(verify)], tmp_path)
            self.assertIn("Committed and pushed", detail)
            self.assertEqual((verify / "README.md").read_text(encoding="utf-8"), "changed despite hook\n")

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


def _seed_private_repo(tmp_path: Path, bare: Path, local: Path) -> None:
    _run(["git", "init", "--bare", str(bare)], tmp_path)
    _run(["git", "clone", str(bare), str(local)], tmp_path)
    _run(["git", "checkout", "-B", "main"], local)
    (local / "README.md").write_text("original\n", encoding="utf-8")
    (local / "remove.txt").write_text("remove\n", encoding="utf-8")
    _run(["git", "add", "-A"], local)
    _run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "seed"], local)
    _run(["git", "push", "origin", "HEAD:main"], local)
    _run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], bare)


if __name__ == "__main__":
    unittest.main()
