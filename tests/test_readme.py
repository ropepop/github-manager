from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from github_manager.readme import README_START, refresh_readme


class ReadmeTests(unittest.TestCase):
    def test_generates_readme_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            (project / "src").mkdir(parents=True)
            (project / ".agent").mkdir()
            (project / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")

            refresh_readme(project, "sample-project", [])

            text = (project / "README.md").read_text(encoding="utf-8")
            self.assertIn("# Sample Project", text)
            self.assertIn("Public Copy Notes", text)
            self.assertIn("`src/`", text)
            self.assertNotIn("`.agent/`", text)

    def test_replaces_existing_managed_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            project.mkdir()
            readme = project / "README.md"
            readme.write_text("# Existing\n\n" + README_START + "\nold\n<!-- github-manager-readme:end -->\n", encoding="utf-8")

            refresh_readme(project, "existing", [])
            refresh_readme(project, "existing", [])

            text = readme.read_text(encoding="utf-8")
            self.assertEqual(text.count(README_START), 1)
            self.assertIn("# Existing", text)


if __name__ == "__main__":
    unittest.main()
