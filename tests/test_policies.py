from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from github_manager.models import GitHubRepo
from github_manager.policies import (
    classify_private_only,
    merge_candidates,
    private_only_candidates,
    private_only_repo_name,
)


class PolicyTests(unittest.TestCase):
    def test_private_only_repo_name_maps_configured_slugs(self) -> None:
        self.assertEqual(private_only_repo_name("my-workspace"), "my-workspace")
        self.assertEqual(private_only_repo_name("ios-activity"), "ios-activity")
        self.assertIsNone(private_only_repo_name("ops"))

    def test_private_only_candidates_include_existing_folders_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            documents = Path(tmp)
            (documents / "My Workspace").mkdir()
            (documents / "iOS activity").mkdir()
            candidates = private_only_candidates(documents)
            by_slug = {candidate.slug: candidate for candidate in candidates}
            self.assertEqual(set(by_slug), {"my-workspace", "ios-activity"})
            self.assertEqual(by_slug["my-workspace"].path, (documents / "My Workspace").resolve())
            self.assertEqual(by_slug["ios-activity"].path, (documents / "iOS activity").resolve())

    def test_private_only_candidates_skip_missing_folders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            documents = Path(tmp)
            candidates = private_only_candidates(documents)
            self.assertEqual(candidates, [])

    def test_merge_candidates_deduplicates_by_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            documents = Path(tmp)
            (documents / "My Workspace").mkdir()
            first = private_only_candidates(documents)
            merged = merge_candidates(first, private_only_candidates(documents))
            self.assertEqual([candidate.path for candidate in merged], [candidate.path for candidate in first])

    def test_classify_private_only_pending_when_repo_missing(self) -> None:
        classification = classify_private_only("my-workspace", [], "ropepop")
        self.assertEqual(classification.status, "private-only")
        self.assertEqual(classification.repo.full_name, "ropepop/my-workspace")
        self.assertTrue(classification.repo.is_private)

    def test_classify_private_only_matches_existing_private_repo(self) -> None:
        repo = GitHubRepo(owner="ropepop", name="my-workspace", url="https://github.com/ropepop/my-workspace", is_private=True)
        classification = classify_private_only("my-workspace", [repo], "ropepop")
        self.assertEqual(classification.status, "private-only")
        self.assertEqual(classification.repo, repo)

    def test_classify_private_only_raises_when_repo_is_public(self) -> None:
        repo = GitHubRepo(owner="ropepop", name="my-workspace", url="https://github.com/ropepop/my-workspace", is_private=False)
        with self.assertRaises(RuntimeError):
            classify_private_only("my-workspace", [repo], "ropepop")


if __name__ == "__main__":
    unittest.main()
