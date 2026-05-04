from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from github_manager.models import GitHubRepo
from github_manager.runner import RunOptions, run_manager


class RunnerTests(unittest.TestCase):
    def test_private_repo_uses_direct_sync_without_sanitizing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            documents = tmp_path / "Documents"
            workspace = tmp_path / "workspace"
            bare = tmp_path / "private.git"
            project = documents / "private-proj"
            _seed_repo(tmp_path, bare, project)
            (project / ".env").write_text("TOKEN=private\n", encoding="utf-8")
            repo = GitHubRepo(owner="ropepop", name="private-proj", url=str(bare), is_private=True)

            with patch("github_manager.runner.GitHubClient") as client:
                client.return_value.list_repos.return_value = [repo]
                results, _ = run_manager(
                    RunOptions(
                        documents_root=documents,
                        workspace=workspace,
                        owner="ropepop",
                        refresh_readme=False,
                    )
                )

            verify = tmp_path / "verify-private"
            _run(["git", "clone", str(bare), str(verify)], tmp_path)
            self.assertEqual(results[0].action, "private-synced")
            self.assertIsNone(results[0].prepared)
            self.assertTrue((verify / ".env").exists())
            self.assertFalse((workspace / "staging" / "private-proj").exists())

    def test_public_repo_still_uses_cleaned_staged_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            documents = tmp_path / "Documents"
            workspace = tmp_path / "workspace"
            project = documents / "public-proj"
            bare = tmp_path / "public.git"
            seed = tmp_path / "seed-public"
            project.mkdir(parents=True)
            (project / "README.md").write_text("public local\n", encoding="utf-8")
            (project / "pyproject.toml").write_text("[project]\nname = \"public-proj\"\n", encoding="utf-8")
            _run(["git", "init", "--bare", str(bare)], tmp_path)
            _run(["git", "clone", str(bare), str(seed)], tmp_path)
            _run(["git", "checkout", "-B", "main"], seed)
            (seed / "README.md").write_text("remote\n", encoding="utf-8")
            _run(["git", "add", "-A"], seed)
            _run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "seed"], seed)
            _run(["git", "push", "origin", "HEAD:main"], seed)
            _run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], bare)
            repo = GitHubRepo(owner="ropepop", name="public-proj", url=str(bare), is_private=False)

            with patch("github_manager.runner.GitHubClient") as client:
                client.return_value.list_repos.return_value = [repo]
                results, _ = run_manager(
                    RunOptions(
                        documents_root=documents,
                        workspace=workspace,
                        owner="ropepop",
                        refresh_readme=False,
                    )
                )

            verify = tmp_path / "verify-public"
            _run(["git", "clone", str(bare), str(verify)], tmp_path)
            self.assertEqual(results[0].action, "synced")
            self.assertIsNotNone(results[0].prepared)
            self.assertEqual((verify / "README.md").read_text(encoding="utf-8"), "public local\n")
            self.assertTrue((workspace / "staging" / "public-proj").exists())


def _seed_repo(tmp_path: Path, bare: Path, local: Path) -> None:
    _run(["git", "init", "--bare", str(bare)], tmp_path)
    local.parent.mkdir(parents=True, exist_ok=True)
    _run(["git", "clone", str(bare), str(local)], tmp_path)
    _run(["git", "checkout", "-B", "main"], local)
    (local / "README.md").write_text("original\n", encoding="utf-8")
    (local / "pyproject.toml").write_text("[project]\nname = \"private-proj\"\n", encoding="utf-8")
    _run(["git", "add", "-A"], local)
    _run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "seed"], local)
    _run(["git", "push", "origin", "HEAD:main"], local)
    _run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], bare)


def _run(args: list[str], cwd: Path) -> None:
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
