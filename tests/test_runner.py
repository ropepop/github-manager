from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from github_manager.models import GitHubRepo, PreparedProject, ProjectCandidate
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

    def test_public_counterpart_run_updates_private_source_before_public_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            documents = tmp_path / "Documents"
            workspace = tmp_path / "workspace"
            source = documents / "ops"
            source.mkdir(parents=True)
            candidate = ProjectCandidate(
                path=source,
                name="ops",
                slug="ops",
                github_remote=GitHubRepo(owner="ropepop", name="ops", url="https://github.com/ropepop/ops"),
            )
            private_repo = GitHubRepo(owner="ropepop", name="ops", url="https://github.com/ropepop/ops", is_private=True)
            public_repo = GitHubRepo(
                owner="ropepop",
                name="public-ops",
                url="https://github.com/ropepop/public-ops",
                is_private=False,
            )
            prepared = PreparedProject(
                source_path=source,
                staged_path=workspace / "staging" / "ops",
                findings=[],
                copied_files=1,
                skipped_files=0,
            )
            events: list[str] = []

            def sync_private(*args, **kwargs):
                events.append("private")
                return "private updated"

            def prepare(*args, **kwargs):
                events.append("prepare")
                return prepared

            def sync_public(*args, **kwargs):
                events.append("public")
                return "public updated"

            with (
                patch("github_manager.runner.GitHubClient") as client,
                patch("github_manager.runner.scan_projects", return_value=[candidate]),
                patch("github_manager.runner._with_public_counterpart_candidates", return_value=[candidate]),
                patch("github_manager.runner.sync_private_project", side_effect=sync_private),
                patch("github_manager.runner.prepare_project", side_effect=prepare),
                patch("github_manager.runner.sync_staged_project", side_effect=sync_public),
            ):
                client.return_value.list_repos.return_value = [private_repo, public_repo]
                results, _ = run_manager(
                    RunOptions(
                        documents_root=documents,
                        workspace=workspace,
                        owner="ropepop",
                        drop_blocked_files=True,
                        sanitized_counterparts=True,
                        public_only=True,
                        refresh_readme=False,
                    )
                )

            self.assertEqual(events, ["private", "prepare", "public"])
            self.assertEqual(results[0].private_action, "private-synced")
            self.assertEqual(results[0].private_detail, "private updated")
            self.assertEqual(results[0].action, "synced")
            self.assertEqual(results[0].detail, "public updated")

    def test_public_only_private_repo_updates_private_then_skips_public(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            documents = tmp_path / "Documents"
            workspace = tmp_path / "workspace"
            source = documents / "links"
            source.mkdir(parents=True)
            candidate = ProjectCandidate(path=source, name="links", slug="links")
            private_repo = GitHubRepo(owner="ropepop", name="links", url="https://github.com/ropepop/links", is_private=True)
            events: list[str] = []

            def sync_private(*args, **kwargs):
                events.append("private")
                return "private updated"

            with (
                patch("github_manager.runner.GitHubClient") as client,
                patch("github_manager.runner.scan_projects", return_value=[candidate]),
                patch("github_manager.runner.sync_private_project", side_effect=sync_private),
            ):
                client.return_value.list_repos.return_value = [private_repo]
                results, _ = run_manager(
                    RunOptions(
                        documents_root=documents,
                        workspace=workspace,
                        owner="ropepop",
                        public_only=True,
                        refresh_readme=False,
                    )
                )

            self.assertEqual(events, ["private"])
            self.assertEqual(results[0].private_action, "private-synced")
            self.assertEqual(results[0].action, "skipped")
            self.assertIn("private repository was handled first", results[0].detail)


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
