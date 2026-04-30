from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from github_manager.models import Finding
from github_manager.readme import DEFAULT_README_MODEL, PREVIOUS_README_NAME, README_START, refresh_readme


class ReadmeTests(unittest.TestCase):
    def test_generates_rich_local_readme_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            (project / "sample_project").mkdir(parents=True)
            (project / "tests").mkdir()
            (project / ".agent").mkdir()
            (project / "pyproject.toml").write_text(
                "\n".join(
                    [
                        "[project]",
                        'name = "sample-project"',
                        'description = "A compact sample project for testing README generation."',
                        'requires-python = ">=3.11"',
                        "",
                        "[project.scripts]",
                        'sample-project = "sample_project.cli:main"',
                    ]
                ),
                encoding="utf-8",
            )
            (project / "sample_project" / "__init__.py").write_text("", encoding="utf-8")
            (project / "tests" / "test_app.py").write_text("import unittest\n", encoding="utf-8")

            result = refresh_readme(project, "sample-project", [], staged_findings=[])

            text = (project / "README.md").read_text(encoding="utf-8")
            self.assertEqual(result.mode, "local-generated")
            self.assertIn("# Sample Project", text)
            self.assertIn("## What It Does", text)
            self.assertIn("## Highlights", text)
            self.assertIn("## Tech Stack", text)
            self.assertIn("## Quick Start", text)
            self.assertIn("## Project Map", text)
            self.assertIn("## Testing", text)
            self.assertIn("python3 -m unittest discover -q", text)
            self.assertIn("sample-project --help", text)
            self.assertIn("Public Copy Notes", text)
            self.assertIn("`tests/`", text)
            self.assertNotIn("`.agent/`", text)
            self.assertFalse((project / PREVIOUS_README_NAME).exists())

    def test_rewrites_existing_readme_and_archives_previous_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            project.mkdir()
            readme = project / "README.md"
            readme.write_text(
                "# Very Old README\n\nUseful public summary.\n\n" + README_START + "\nold\n<!-- github-manager-readme:end -->\n",
                encoding="utf-8",
            )

            result = refresh_readme(project, "new-public-name", [], staged_findings=[])

            text = readme.read_text(encoding="utf-8")
            previous = (project / PREVIOUS_README_NAME).read_text(encoding="utf-8")
            self.assertTrue(result.archived_previous)
            self.assertEqual(text.count(README_START), 1)
            self.assertIn("# Very Old README", previous)
            self.assertIn("Useful public summary.", previous)
            self.assertIn("previous sanitized README is preserved", text)
            self.assertIn("Useful public summary.", text)

    def test_uses_ai_draft_when_key_and_safe_findings_are_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            project.mkdir()
            (project / "pyproject.toml").write_text(
                '[project]\nname = "portfolio-tool"\ndescription = "A tool worth showing publicly."\n',
                encoding="utf-8",
            )
            seen_payloads: list[dict] = []

            def fake_ai_requester(payload: dict, api_key: str) -> dict:
                seen_payloads.append(payload)
                self.assertEqual(api_key, "test-key")
                draft = {
                    "title": "Portfolio Tool",
                    "pitch": "A polished public description.",
                    "what_it_does": ["Explains the project clearly"],
                    "highlights": ["Uses structured README generation"],
                    "tech_stack": ["Python"],
                    "quick_start": [{"label": "Install", "command": "python3 -m pip install -e ."}],
                    "project_map": [{"path": "pyproject.toml", "description": "Python package metadata"}],
                    "testing": [{"label": "Run tests", "command": "python3 -m unittest discover -q"}],
                }
                return {
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": json.dumps(draft)}],
                        }
                    ],
                }

            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False):
                result = refresh_readme(project, "portfolio-tool", [], staged_findings=[], ai_requester=fake_ai_requester)

            text = (project / "README.md").read_text(encoding="utf-8")
            self.assertEqual(result.mode, "ai-drafted")
            self.assertEqual(result.model, DEFAULT_README_MODEL)
            self.assertEqual(seen_payloads[0]["model"], DEFAULT_README_MODEL)
            self.assertEqual(seen_payloads[0]["text"]["format"]["type"], "json_schema")
            self.assertIn("A polished public description.", text)
            self.assertIn("Uses structured README generation", text)

    def test_skips_ai_when_staged_copy_has_blocking_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            project.mkdir()
            local_path = "/Users/" + "example/private"
            (project / "notes.md").write_text(f"local path {local_path}\n", encoding="utf-8")
            finding = Finding("block", "notes.md", "The file contains a local user path.")

            def failing_ai_requester(payload: dict, api_key: str) -> dict:
                raise AssertionError("AI requester should not be called")

            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False):
                result = refresh_readme(
                    project,
                    "unsafe-project",
                    [],
                    staged_findings=[finding],
                    ai_requester=failing_ai_requester,
                )

            text = (project / "README.md").read_text(encoding="utf-8")
            self.assertEqual(result.mode, "local-generated")
            self.assertIn("safety findings", result.detail)
            self.assertIn("## Public Copy Notes", text)


if __name__ == "__main__":
    unittest.main()
