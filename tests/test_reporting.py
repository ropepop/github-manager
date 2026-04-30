from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from github_manager.models import (
    Classification,
    PreparedProject,
    ProjectCandidate,
    ProjectRunResult,
    ReadmeRefreshResult,
)
from github_manager.reporting import write_run_report


class ReportingTests(unittest.TestCase):
    def test_report_includes_readme_generation_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            candidate = ProjectCandidate(path=tmp_path / "source", name="Source", slug="source")
            result = ProjectRunResult(
                candidate=candidate,
                classification=Classification(status="unpublished", match_method="none"),
                action="needs-approval",
                detail="Prepared clean copy.",
                prepared=PreparedProject(
                    source_path=tmp_path / "source",
                    staged_path=tmp_path / "workspace" / "staging" / "source",
                    findings=[],
                    copied_files=2,
                    skipped_files=0,
                    readme_result=ReadmeRefreshResult(
                        mode="ai-drafted",
                        detail="README drafted with AI model gpt-5-mini.",
                        model="gpt-5-mini",
                        archived_previous=True,
                    ),
                ),
            )

            report = write_run_report(tmp_path / "workspace", [result], dry_run=True)

            text = report.read_text(encoding="utf-8")
            self.assertIn("README generation: ai drafted (gpt-5-mini)", text)
            self.assertIn("Previous README archived: yes", text)


if __name__ == "__main__":
    unittest.main()
