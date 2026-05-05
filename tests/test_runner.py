from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from github_manager.models import Classification, GitHubRepo, PreparedProject, ProjectCandidate, ProjectRunResult
from github_manager.runner import RunOptions, _apply_chat_handoff_drafts, run_manager


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

    def test_chat_handoff_skips_builtin_readme_refresh_and_times_out_to_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            documents = tmp_path / "Documents"
            workspace = tmp_path / "workspace"
            source = documents / "public-proj"
            source.mkdir(parents=True)
            candidate = ProjectCandidate(path=source, name="public-proj", slug="public-proj")
            repo = GitHubRepo(owner="ropepop", name="public-proj", url="https://github.com/ropepop/public-proj", is_private=False)
            prepared = PreparedProject(
                source_path=source,
                staged_path=workspace / "staging" / "public-proj",
                findings=[],
                copied_files=1,
                skipped_files=0,
            )
            events: list[str] = []
            prepare_kwargs: list[dict] = []

            def prepare(*args, **kwargs):
                events.append("prepare")
                prepare_kwargs.append(kwargs)
                return prepared

            def sync_public(*args, **kwargs):
                events.append("public")
                return "public updated"

            with (
                patch("github_manager.runner.GitHubClient") as client,
                patch("github_manager.runner.scan_projects", return_value=[candidate]),
                patch("github_manager.runner.prepare_project", side_effect=prepare),
                patch("github_manager.runner.sync_staged_project", side_effect=sync_public),
            ):
                client.return_value.list_repos.return_value = [repo]
                results, _ = run_manager(
                    RunOptions(
                        documents_root=documents,
                        workspace=workspace,
                        owner="ropepop",
                        chat_handoff=True,
                        chat_handoff_timeout_seconds=0,
                        chat_handoff_poll_seconds=0,
                        chat_handoff_run_id="test-run",
                    )
                )

            self.assertEqual(events, ["prepare", "public"])
            self.assertEqual(prepare_kwargs[0]["refresh_readme_file"], False)
            self.assertEqual(results[0].chat_handoff_action, "timed-out")
            self.assertEqual(results[0].action, "synced")
            self.assertTrue((workspace / "chat-handoff" / "test-run" / "manifest.json").exists())

    def test_chat_handoff_applies_readme_and_nested_docs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            handoff = tmp_path / "handoff"
            draft = handoff / "drafts" / "public-proj"
            staged = tmp_path / "staged"
            (draft / "docs" / "nested").mkdir(parents=True)
            staged.mkdir()
            (staged / "README.md").write_text("old\n", encoding="utf-8")
            (draft / "README.md").write_text("# Public Project\n", encoding="utf-8")
            (draft / "docs" / "nested" / "guide.md").write_text("# Guide\n", encoding="utf-8")
            result = _handoff_result(staged)

            _apply_chat_handoff_drafts(handoff, [result])

            self.assertEqual(result.chat_handoff_action, "applied")
            self.assertEqual(result.chat_handoff_files, ["README.md", "docs/nested/guide.md"])
            self.assertEqual((staged / "README.md").read_text(encoding="utf-8"), "# Public Project\n")
            self.assertEqual((staged / "docs" / "nested" / "guide.md").read_text(encoding="utf-8"), "# Guide\n")

    def test_chat_handoff_applies_readme_docs_and_visual_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            handoff = tmp_path / "handoff"
            draft = handoff / "drafts" / "public-proj"
            staged = tmp_path / "staged"
            (draft / "docs" / "assets").mkdir(parents=True)
            staged.mkdir()
            (draft / "README.md").write_text(
                "# Public Project\n\n![Hero](docs/assets/hero.png)\n\n![Diagram](docs/assets/diagram.svg)\n",
                encoding="utf-8",
            )
            (draft / "docs" / "nested").mkdir()
            (draft / "docs" / "guide.md").write_text("![Nested](assets/hero.webp)\n", encoding="utf-8")
            (draft / "docs" / "nested" / "deep.md").write_text("![Parent](../assets/hero.webp)\n", encoding="utf-8")
            (draft / "docs" / "assets" / "hero.png").write_bytes(b"png")
            (draft / "docs" / "assets" / "screen.jpg").write_bytes(b"jpg")
            (draft / "docs" / "assets" / "screen.jpeg").write_bytes(b"jpeg")
            (draft / "docs" / "assets" / "hero.webp").write_bytes(b"webp")
            (draft / "docs" / "assets" / "diagram.svg").write_text("<svg xmlns=\"http://www.w3.org/2000/svg\" />\n", encoding="utf-8")
            result = _handoff_result(staged)

            _apply_chat_handoff_drafts(handoff, [result])

            self.assertEqual(result.chat_handoff_action, "applied")
            self.assertEqual(result.chat_handoff_files, ["README.md", "docs/guide.md", "docs/nested/deep.md"])
            self.assertEqual(
                result.chat_handoff_assets,
                [
                    "docs/assets/diagram.svg",
                    "docs/assets/hero.png",
                    "docs/assets/hero.webp",
                    "docs/assets/screen.jpeg",
                    "docs/assets/screen.jpg",
                ],
            )
            self.assertTrue((staged / "docs" / "assets" / "hero.png").exists())
            self.assertTrue((staged / "docs" / "assets" / "diagram.svg").exists())

    def test_chat_handoff_rejects_invalid_draft_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            handoff = tmp_path / "handoff"
            draft = handoff / "drafts" / "public-proj"
            staged = tmp_path / "staged"
            draft.mkdir(parents=True)
            staged.mkdir()
            (draft / "README.md").write_text("# Public Project\n", encoding="utf-8")
            (draft / "app.py").write_text("print('changed')\n", encoding="utf-8")
            result = _handoff_result(staged)

            _apply_chat_handoff_drafts(handoff, [result])

            self.assertEqual(result.chat_handoff_action, "rejected")
            self.assertIn("app.py", result.chat_handoff_detail)
            self.assertFalse((staged / "README.md").exists())
            self.assertFalse((staged / "app.py").exists())

    def test_chat_handoff_rejects_images_outside_docs_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            handoff = tmp_path / "handoff"
            draft = handoff / "drafts" / "public-proj"
            staged = tmp_path / "staged"
            (draft / "docs").mkdir(parents=True)
            staged.mkdir()
            (draft / "README.md").write_text("# Public Project\n", encoding="utf-8")
            (draft / "docs" / "hero.png").write_bytes(b"png")
            result = _handoff_result(staged)

            _apply_chat_handoff_drafts(handoff, [result])

            self.assertEqual(result.chat_handoff_action, "rejected")
            self.assertIn("docs/hero.png", result.chat_handoff_detail)
            self.assertFalse((staged / "docs" / "hero.png").exists())

    def test_chat_handoff_rejects_unsupported_asset_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            handoff = tmp_path / "handoff"
            draft = handoff / "drafts" / "public-proj"
            staged = tmp_path / "staged"
            (draft / "docs" / "assets").mkdir(parents=True)
            staged.mkdir()
            (draft / "README.md").write_text("# Public Project\n", encoding="utf-8")
            (draft / "docs" / "assets" / "loop.gif").write_bytes(b"gif")
            result = _handoff_result(staged)

            _apply_chat_handoff_drafts(handoff, [result])

            self.assertEqual(result.chat_handoff_action, "rejected")
            self.assertIn("docs/assets/loop.gif", result.chat_handoff_detail)
            self.assertFalse((staged / "docs" / "assets" / "loop.gif").exists())

    def test_chat_handoff_rejects_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            handoff = tmp_path / "handoff"
            draft = handoff / "drafts" / "public-proj"
            staged = tmp_path / "staged"
            draft.mkdir(parents=True)
            staged.mkdir()
            (draft / "README.md").write_text("# Public Project\n", encoding="utf-8")
            (tmp_path / "outside.md").write_text("outside\n", encoding="utf-8")
            (draft / "docs").mkdir()
            (draft / "docs" / "guide.md").symlink_to(tmp_path / "outside.md")
            result = _handoff_result(staged)

            _apply_chat_handoff_drafts(handoff, [result])

            self.assertEqual(result.chat_handoff_action, "rejected")
            self.assertIn("docs/guide.md", result.chat_handoff_detail)
            self.assertFalse((staged / "README.md").exists())

    def test_chat_handoff_rejects_unsafe_drafts_before_copying(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            handoff = tmp_path / "handoff"
            draft = handoff / "drafts" / "public-proj"
            staged = tmp_path / "staged"
            draft.mkdir(parents=True)
            staged.mkdir()
            (staged / "README.md").write_text("safe\n", encoding="utf-8")
            fake_token = "ghp_" + "123456789012345678901234567890"
            (draft / "README.md").write_text(f"token {fake_token}\n", encoding="utf-8")
            result = _handoff_result(staged)

            _apply_chat_handoff_drafts(handoff, [result])

            self.assertEqual(result.chat_handoff_action, "rejected")
            self.assertIn("safety checks", result.chat_handoff_detail)
            self.assertEqual((staged / "README.md").read_text(encoding="utf-8"), "safe\n")

    def test_chat_handoff_rejects_unsafe_svg_before_copying(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            handoff = tmp_path / "handoff"
            draft = handoff / "drafts" / "public-proj"
            staged = tmp_path / "staged"
            (draft / "docs" / "assets").mkdir(parents=True)
            staged.mkdir()
            (draft / "README.md").write_text("![Hero](docs/assets/hero.svg)\n", encoding="utf-8")
            fake_token = "ghp_" + "123456789012345678901234567890"
            (draft / "docs" / "assets" / "hero.svg").write_text(f"<svg>{fake_token}</svg>\n", encoding="utf-8")
            result = _handoff_result(staged)

            _apply_chat_handoff_drafts(handoff, [result])

            self.assertEqual(result.chat_handoff_action, "rejected")
            self.assertIn("safety checks", result.chat_handoff_detail)
            self.assertFalse((staged / "docs" / "assets" / "hero.svg").exists())

    def test_chat_handoff_rejects_missing_markdown_image_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            handoff = tmp_path / "handoff"
            draft = handoff / "drafts" / "public-proj"
            staged = tmp_path / "staged"
            draft.mkdir(parents=True)
            staged.mkdir()
            (draft / "README.md").write_text("# Public Project\n\n![Missing](docs/assets/missing.png)\n", encoding="utf-8")
            result = _handoff_result(staged)

            _apply_chat_handoff_drafts(handoff, [result])

            self.assertEqual(result.chat_handoff_action, "rejected")
            self.assertIn("README.md -> docs/assets/missing.png", result.chat_handoff_detail)
            self.assertFalse((staged / "README.md").exists())

    def test_chat_handoff_rejects_projects_without_readme_draft(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            handoff = tmp_path / "handoff"
            draft = handoff / "drafts" / "public-proj"
            staged = tmp_path / "staged"
            (draft / "docs").mkdir(parents=True)
            staged.mkdir()
            (draft / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
            result = _handoff_result(staged)

            _apply_chat_handoff_drafts(handoff, [result])

            self.assertEqual(result.chat_handoff_action, "rejected")
            self.assertIn("README.md is required", result.chat_handoff_detail)
            self.assertFalse((staged / "docs" / "guide.md").exists())


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


def _handoff_result(staged: Path) -> ProjectRunResult:
    candidate = ProjectCandidate(path=staged.parent / "source", name="public-proj", slug="public-proj")
    return ProjectRunResult(
        candidate=candidate,
        classification=Classification(
            status="published",
            match_method="exact name",
            repo=GitHubRepo(owner="ropepop", name="public-proj", url="https://github.com/ropepop/public-proj"),
        ),
        prepared=PreparedProject(
            source_path=candidate.path,
            staged_path=staged,
            findings=[],
            copied_files=1,
            skipped_files=0,
        ),
    )


def _run(args: list[str], cwd: Path) -> None:
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
