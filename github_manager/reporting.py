from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .models import ProjectRunResult


def write_run_report(workspace: Path, results: list[ProjectRunResult], dry_run: bool) -> Path:
    report_dir = workspace / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_path = report_dir / f"run-{timestamp}.md"
    lines = [
        "# GitHub Manager Run Report",
        "",
        f"- Mode: {'dry run' if dry_run else 'live'}",
        f"- Projects scanned: {len(results)}",
        f"- Generated: {timestamp}",
        "",
        "## Summary",
        "",
    ]
    for status in sorted({result.classification.status for result in results}):
        count = sum(1 for result in results if result.classification.status == status)
        lines.append(f"- {status}: {count}")
    lines.extend(["", "## Projects", ""])
    for result in results:
        candidate = result.candidate
        classification = result.classification
        lines.extend(
            [
                f"### {candidate.name}",
                "",
                f"- Source: `{candidate.path}`",
                f"- Status: {classification.status}",
                f"- Match: {classification.match_method}",
                f"- Action: {result.action}",
                f"- Detail: {result.detail or classification.reason}",
            ]
        )
        if classification.repo:
            lines.append(f"- Repository: {classification.repo.url}")
        if result.private_action != "none":
            lines.append(f"- Private action: {result.private_action}")
            lines.append(f"- Private detail: {result.private_detail}")
            if result.private_repo:
                lines.append(f"- Private repository: {result.private_repo.url}")
        if result.chat_handoff_action != "none":
            lines.append(f"- Chat handoff action: {result.chat_handoff_action}")
            lines.append(f"- Chat handoff detail: {result.chat_handoff_detail}")
            if result.chat_handoff_path:
                lines.append(f"- Chat handoff folder: `{result.chat_handoff_path}`")
            if result.chat_handoff_files:
                lines.append(f"- Chat handoff files: {', '.join(f'`{path}`' for path in result.chat_handoff_files)}")
            if result.chat_handoff_assets:
                lines.append(f"- Chat handoff assets: {', '.join(f'`{path}`' for path in result.chat_handoff_assets)}")
        if result.prepared:
            prepared = result.prepared
            lines.extend(
                [
                    f"- Staged copy: `{prepared.staged_path}`",
                    f"- Copied files: {prepared.copied_files}",
                    f"- Skipped files: {prepared.skipped_files}",
                    f"- Removed risky files: {len(prepared.removed_findings)}",
                    f"- Strict check: {'passed' if prepared.clean else 'blocked'}",
                ]
            )
            if prepared.readme_result:
                readme_result = prepared.readme_result
                label = readme_result.mode.replace("-", " ")
                if readme_result.model:
                    label = f"{label} ({readme_result.model})"
                lines.append(f"- README generation: {label}")
                lines.append(f"- README detail: {readme_result.detail}")
                lines.append(f"- Previous README archived: {'yes' if readme_result.archived_previous else 'no'}")
            if prepared.removed_findings:
                lines.append("")
                lines.append("Removed during sanitation:")
                for finding in prepared.removed_findings:
                    lines.append(f"- `{finding.path}`: {finding.message}")
            if prepared.findings:
                lines.append("")
                lines.append("Findings:")
                for finding in prepared.findings:
                    lines.append(f"- [{finding.severity}] `{finding.path}`: {finding.message}")
        lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path
