from __future__ import annotations

import unittest

from github_manager.github import parse_github_remote


class GitHubTests(unittest.TestCase):
    def test_parse_remote_allows_dots_in_repo_names(self) -> None:
        repo = parse_github_remote("https://github.com/ropepop/jolkins.id.lv.git")

        self.assertIsNotNone(repo)
        self.assertEqual(repo.full_name, "ropepop/jolkins.id.lv")


if __name__ == "__main__":
    unittest.main()
