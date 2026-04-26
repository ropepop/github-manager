from __future__ import annotations

import unittest
from pathlib import Path

from github_manager.classifier import classify_project
from github_manager.models import GitHubRepo, ProjectCandidate


class ClassifierTests(unittest.TestCase):
    def test_exact_name_match_marks_project_as_published(self) -> None:
        candidate = ProjectCandidate(path=Path("/tmp/cases parser"), name="cases parser", slug="cases-parser")
        repos = [GitHubRepo(owner="ropepop", name="cases-parser", url="https://github.com/ropepop/cases-parser")]

        result = classify_project(candidate, repos, "ropepop")

        self.assertEqual(result.status, "published")
        self.assertEqual(result.match_method, "exact name")

    def test_external_remote_is_not_managed_for_sync(self) -> None:
        candidate = ProjectCandidate(
            path=Path("/tmp/project"),
            name="project",
            slug="project",
            github_remote=GitHubRepo(owner="someone", name="project", url="https://github.com/someone/project"),
        )

        result = classify_project(candidate, [], "ropepop")

        self.assertEqual(result.status, "published-external")

    def test_private_remote_can_target_public_counterpart(self) -> None:
        candidate = ProjectCandidate(
            path=Path("/tmp/ops"),
            name="ops",
            slug="ops",
            github_remote=GitHubRepo(owner="ropepop", name="ops", url="https://github.com/ropepop/ops"),
        )
        repos = [
            GitHubRepo(owner="ropepop", name="ops", url="https://github.com/ropepop/ops", is_private=True),
            GitHubRepo(owner="ropepop", name="public-ops", url="https://github.com/ropepop/public-ops", is_private=False),
        ]

        result = classify_project(candidate, repos, "ropepop", prefer_sanitized_counterpart=True, public_only=True)

        self.assertEqual(result.status, "published")
        self.assertEqual(result.match_method, "public counterpart")
        self.assertEqual(result.repo.name, "public-ops")

    def test_public_only_skips_private_exact_match(self) -> None:
        candidate = ProjectCandidate(path=Path("/tmp/links"), name="links", slug="links")
        repos = [GitHubRepo(owner="ropepop", name="links", url="https://github.com/ropepop/links", is_private=True)]

        result = classify_project(candidate, repos, "ropepop", public_only=True)

        self.assertEqual(result.status, "published-private")


if __name__ == "__main__":
    unittest.main()
