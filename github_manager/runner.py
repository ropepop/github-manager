from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .classifier import classify_project
from .github import GitHubClient
from .models import GitHubRepo, ProjectRunResult
from .naming import normalize_name
from .reporting import write_run_report
from .sanitizer import inspect_source, prepare_project
from .scanner import scan_projects
from .syncer import sync_private_project, sync_staged_project

CHAT_HANDOFF_ALLOWED_PATTERNS = ["README.md", "docs/**/*.md", "docs/assets/**/*.{png,jpg,jpeg,webp,svg}"]
CHAT_HANDOFF_ASSET_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".svg"}
MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
HTML_IMAGE_RE = re.compile(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"']", re.IGNORECASE)


@dataclass
class RunOptions:
    documents_root: Path
    workspace: Path
    owner: str
    dry_run: bool = False
    no_sync: bool = False
    drop_blocked_files: bool = False
    sanitized_counterparts: bool = False
    public_only: bool = False
    private_first: bool = True
    refresh_readme: bool = True
    approve_new: set[str] | None = None
    interactive: bool = True
    chat_handoff: bool = False
    chat_handoff_timeout_seconds: float = 1800
    chat_handoff_poll_seconds: float = 2
    chat_handoff_run_id: str | None = None


def run_manager(options: RunOptions) -> tuple[list[ProjectRunResult], Path]:
    workspace = options.workspace.expanduser().resolve()
    staging_root = workspace / "staging"
    workspace.mkdir(parents=True, exist_ok=True)
    staging_root.mkdir(parents=True, exist_ok=True)

    client = GitHubClient(options.owner)
    repos = client.list_repos()
    candidates = scan_projects(options.documents_root, excluded_roots=[workspace])
    if options.sanitized_counterparts:
        candidates = _with_public_counterpart_candidates(candidates, options.documents_root, workspace, repos)
    results: list[ProjectRunResult] = []
    pending_public_results: list[ProjectRunResult] = []
    targeted_repos: set[str] = set()
    targeted_private_repos: set[str] = set()

    for candidate in candidates:
        classification = classify_project(
            candidate,
            repos,
            options.owner,
            prefer_sanitized_counterpart=options.sanitized_counterparts,
            public_only=options.public_only,
        )
        result = ProjectRunResult(candidate=candidate, classification=classification)
        if options.private_first and (options.public_only or options.sanitized_counterparts):
            _run_private_first(result, options, client, repos, targeted_private_repos)
            if result.private_action == "failed":
                result.action = "skipped"
                result.detail = "Skipped public sanitized sync because the private repository update failed."
                results.append(result)
                continue
        if classification.repo:
            target = classification.repo.full_name
            if target in targeted_repos:
                result.action = "skipped"
                result.detail = "Another local project candidate already targeted this GitHub repository."
                results.append(result)
                continue
            if classification.status == "published":
                targeted_repos.add(target)
        if classification.status == "published-external":
            result.action = "skipped"
            result.detail = "Already published under a different GitHub owner; left untouched."
            results.append(result)
            continue
        if classification.status == "published-private":
            result.action = "skipped"
            if result.private_action not in {"none", "skipped"}:
                result.detail = "No public sanitized counterpart was selected; private repository was handled first."
            else:
                result.detail = "Private repository skipped because this run targets public sanitized repositories."
            results.append(result)
            continue
        if classification.repo and classification.repo.is_private:
            if result.private_action not in {"none", "skipped"}:
                result.action = "skipped"
                result.detail = "Private repository was handled before sanitized public work."
                results.append(result)
                continue
            if options.no_sync:
                result.action = "skipped"
                result.detail = "Sync disabled for this run."
            else:
                result.action = "private-sync-dry-run" if options.dry_run else "private-synced"
                try:
                    result.detail = sync_private_project(
                        classification.repo,
                        candidate.path,
                        dry_run=options.dry_run,
                    )
                except RuntimeError as exc:
                    result.action = "failed"
                    result.detail = f"Private repository sync failed: {exc}"
            results.append(result)
            continue

        prepared = prepare_project(
            candidate.path,
            staging_root,
            candidate.slug,
            drop_blocked_files=options.drop_blocked_files,
            refresh_readme_file=options.refresh_readme and not options.chat_handoff,
        )
        result.prepared = prepared
        if not prepared.clean:
            result.action = "blocked"
            result.detail = "Strict checks found files that need review before publishing or syncing."
            results.append(result)
            continue

        if classification.is_publishable_existing and classification.repo:
            if options.chat_handoff:
                result.action = "prepared"
                result.detail = "Prepared clean copy for chat handoff."
                pending_public_results.append(result)
            else:
                _finish_existing_public_result(result, options, workspace)
            results.append(result)
            continue

        if classification.is_unpublished:
            approved = _is_approved_for_new_publish(candidate.slug, options)
            if not approved:
                result.action = "needs-approval"
                result.detail = "Prepared clean copy, but first publication requires explicit approval."
                results.append(result)
                continue
            if options.dry_run:
                if options.chat_handoff:
                    result.action = "prepared"
                    result.detail = "Prepared clean copy for chat handoff."
                    pending_public_results.append(result)
                else:
                    result.action = "publish-dry-run"
                    result.detail = f"Would create public GitHub repository {options.owner}/{candidate.slug}."
            else:
                if options.chat_handoff:
                    result.action = "prepared"
                    result.detail = "Prepared clean copy for chat handoff."
                    pending_public_results.append(result)
                else:
                    _finish_new_public_result(result, options, client)
            results.append(result)
            continue

        result.action = "skipped"
        result.detail = "No supported action for this project state."
        results.append(result)

    if options.chat_handoff and pending_public_results:
        _run_chat_handoff(workspace, pending_public_results, options)
        for result in pending_public_results:
            if result.prepared and not result.prepared.clean:
                result.action = "blocked"
                result.detail = "Chat handoff output left safety findings in the staged copy."
            elif result.classification.is_publishable_existing and result.classification.repo:
                _finish_existing_public_result(result, options, workspace)
            elif result.classification.is_unpublished:
                if options.dry_run:
                    result.action = "publish-dry-run"
                    result.detail = f"Would create public GitHub repository {options.owner}/{result.candidate.slug}."
                else:
                    _finish_new_public_result(result, options, client)

    report_path = write_run_report(workspace, results, options.dry_run)
    return results, report_path


def _finish_existing_public_result(result: ProjectRunResult, options: RunOptions, workspace: Path) -> None:
    if options.no_sync:
        result.action = "skipped"
        result.detail = "Sync disabled for this run."
        return
    if not result.classification.repo or not result.prepared:
        result.action = "failed"
        result.detail = "Missing public repository or staged copy for sync."
        return
    result.action = "sync-dry-run" if options.dry_run else "synced"
    result.detail = sync_staged_project(result.classification.repo, result.prepared.staged_path, workspace, dry_run=options.dry_run)


def _finish_new_public_result(result: ProjectRunResult, options: RunOptions, client: GitHubClient) -> None:
    if not result.prepared:
        result.action = "failed"
        result.detail = "Missing staged copy for first publication."
        return
    repo = client.create_public_repo_from_path(result.candidate.slug, result.prepared.staged_path)
    result.classification.repo = repo
    result.action = "published"
    result.detail = f"Created public GitHub repository {repo.full_name}."


def _run_chat_handoff(workspace: Path, results: list[ProjectRunResult], options: RunOptions) -> None:
    handoff_dir = _write_chat_handoff(workspace, results, options)
    print(f"Chat handoff ready: {handoff_dir}", flush=True)
    print(f"Write completion marker to: {handoff_dir / 'complete.json'}", flush=True)
    completed = _wait_for_chat_handoff(handoff_dir, options)
    if not completed:
        for result in results:
            result.chat_handoff_action = "timed-out"
            result.chat_handoff_detail = "No chat handoff completion marker was found before timeout; continuing with staged copy as-is."
        return
    _apply_chat_handoff_drafts(handoff_dir, results)


def _write_chat_handoff(workspace: Path, results: list[ProjectRunResult], options: RunOptions) -> Path:
    run_id = options.chat_handoff_run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    handoff_dir = workspace / "chat-handoff" / run_id
    handoff_dir.mkdir(parents=True, exist_ok=True)
    drafts_dir = handoff_dir / "drafts"
    drafts_dir.mkdir(exist_ok=True)
    projects = []
    for result in results:
        if not result.prepared:
            continue
        (drafts_dir / result.candidate.slug).mkdir(parents=True, exist_ok=True)
        result.chat_handoff_path = handoff_dir
        projects.append(
            {
                "slug": result.candidate.slug,
                "name": result.candidate.name,
                "source_path": str(result.candidate.path),
                "staged_path": str(result.prepared.staged_path),
                "target_repo": result.classification.repo.full_name if result.classification.repo else f"{options.owner}/{result.candidate.slug}",
                "removed_risky_files": len(result.prepared.removed_findings),
                "allowed_draft_paths": CHAT_HANDOFF_ALLOWED_PATTERNS,
                "existing_image_assets": _existing_image_assets(result.prepared.staged_path),
                "readme_brief": _readme_brief(result),
                "draft_dir": str(drafts_dir / result.candidate.slug),
            }
        )
    manifest = {
        "run_id": run_id,
        "complete_marker": str(handoff_dir / "complete.json"),
        "projects": projects,
    }
    (handoff_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    (handoff_dir / "instructions.md").write_text(_chat_handoff_instructions(manifest), encoding="utf-8")
    return handoff_dir


def _chat_handoff_instructions(manifest: dict) -> str:
    lines = [
        "# GitHub Manager Chat Handoff",
        "",
        "Generate premium public-safe README, docs, and visual assets for the staged public copies only.",
        "",
        "Allowed output paths inside each project draft folder:",
        "",
        "- `README.md`",
        "- `docs/**/*.md`",
        "- `docs/assets/**/*.{png,jpg,jpeg,webp,svg}`",
        "",
        "Each README should include a strong title, concise positioning, a hero visual near the top, clear highlights, quick start, project map, testing, and links to generated docs where useful.",
        "",
        "When the drafts are ready, write `complete.json` in this handoff folder.",
        "",
        "Projects:",
        "",
    ]
    for project in manifest["projects"]:
        lines.extend(
            [
                f"- `{project['slug']}`",
                f"  - Staged copy: `{project['staged_path']}`",
                f"  - Draft folder: `{project['draft_dir']}`",
                f"  - Target repo: `{project['target_repo']}`",
                f"  - Brief: {project['readme_brief']}",
            ]
        )
    lines.extend(
        [
            "",
            "Example completion marker:",
            "",
            "```json",
            '{"status": "complete"}',
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _wait_for_chat_handoff(handoff_dir: Path, options: RunOptions) -> bool:
    marker = handoff_dir / "complete.json"
    deadline = time.monotonic() + max(0, options.chat_handoff_timeout_seconds)
    while True:
        if marker.exists():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(max(0.1, options.chat_handoff_poll_seconds))


def _apply_chat_handoff_drafts(handoff_dir: Path, results: list[ProjectRunResult]) -> None:
    drafts_dir = handoff_dir / "drafts"
    for result in results:
        if not result.prepared:
            continue
        result.chat_handoff_path = handoff_dir
        draft_dir = drafts_dir / result.candidate.slug
        if not draft_dir.exists():
            result.chat_handoff_action = "no-drafts"
            result.chat_handoff_detail = "No draft folder was found; continuing with staged copy as-is."
            continue
        invalid = _invalid_chat_draft_paths(draft_dir)
        if invalid:
            result.chat_handoff_action = "rejected"
            result.chat_handoff_detail = f"Rejected draft output because these paths are not allowed: {', '.join(invalid)}."
            continue
        draft_files = _allowed_chat_draft_files(draft_dir)
        if not draft_files:
            result.chat_handoff_action = "no-drafts"
            result.chat_handoff_detail = "No README.md, docs Markdown, or docs/assets image drafts were provided; continuing with staged copy as-is."
            continue
        if Path("README.md") not in draft_files:
            result.chat_handoff_action = "rejected"
            result.chat_handoff_detail = "Rejected draft output because README.md is required for premium handoff projects."
            continue
        with tempfile.TemporaryDirectory(prefix="github-manager-chat-handoff-") as tmp:
            temp_staged = Path(tmp) / "staged"
            shutil.copytree(result.prepared.staged_path, temp_staged)
            for relative in draft_files:
                source = draft_dir / relative
                target = temp_staged / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            findings = inspect_source(temp_staged)
            if findings:
                result.chat_handoff_action = "rejected"
                result.chat_handoff_detail = "Rejected draft output because safety checks found risky content."
                continue
            missing_links = _missing_markdown_image_links(temp_staged, _markdown_files_for_link_check(temp_staged))
            if missing_links:
                result.chat_handoff_action = "rejected"
                result.chat_handoff_detail = f"Rejected draft output because these image links are missing: {', '.join(missing_links)}."
                continue
        for relative in draft_files:
            source = draft_dir / relative
            target = result.prepared.staged_path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        result.prepared.findings = inspect_source(result.prepared.staged_path)
        result.chat_handoff_files = [relative.as_posix() for relative in draft_files if _is_markdown_draft_path(relative)]
        result.chat_handoff_assets = [relative.as_posix() for relative in draft_files if _is_asset_draft_path(relative)]
        result.chat_handoff_action = "applied"
        result.chat_handoff_detail = (
            f"Applied {len(result.chat_handoff_files)} chat-generated Markdown file(s) "
            f"and {len(result.chat_handoff_assets)} visual asset(s) to the staged copy."
        )


def _invalid_chat_draft_paths(draft_dir: Path) -> list[str]:
    invalid: list[str] = []
    for path in draft_dir.rglob("*"):
        relative = path.relative_to(draft_dir)
        if path.is_symlink():
            invalid.append(relative.as_posix())
            continue
        if path.is_dir():
            continue
        if not path.is_file() or not _is_allowed_chat_draft_path(relative):
            invalid.append(relative.as_posix())
    return sorted(invalid)


def _allowed_chat_draft_files(draft_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in draft_dir.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(draft_dir)
        if _is_allowed_chat_draft_path(relative):
            files.append(relative)
    return sorted(files, key=lambda path: path.as_posix())


def _is_allowed_chat_draft_path(relative: Path) -> bool:
    if relative.is_absolute() or ".." in relative.parts:
        return False
    return _is_markdown_draft_path(relative) or _is_asset_draft_path(relative)


def _is_markdown_draft_path(relative: Path) -> bool:
    if relative.as_posix() == "README.md":
        return True
    return len(relative.parts) >= 2 and relative.parts[0] == "docs" and relative.suffix.lower() == ".md"


def _is_asset_draft_path(relative: Path) -> bool:
    return (
        len(relative.parts) >= 3
        and relative.parts[0] == "docs"
        and relative.parts[1] == "assets"
        and relative.suffix.lower() in CHAT_HANDOFF_ASSET_SUFFIXES
    )


def _markdown_files_for_link_check(staged_path: Path) -> list[Path]:
    markdown_files: list[Path] = []
    for path in staged_path.rglob("*.md"):
        if path.is_file():
            markdown_files.append(path.relative_to(staged_path))
    return sorted(markdown_files, key=lambda path: path.as_posix())


def _missing_markdown_image_links(staged_path: Path, markdown_files: list[Path]) -> list[str]:
    missing: list[str] = []
    for relative in markdown_files:
        path = staged_path / relative
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                text = path.read_text(encoding="utf-8-sig")
            except UnicodeDecodeError:
                continue
        except OSError:
            continue
        for target in _markdown_image_targets(text):
            resolved = _resolve_markdown_image_target(staged_path, relative, target)
            if resolved is None:
                continue
            if not resolved.exists() or not resolved.is_file():
                missing.append(f"{relative.as_posix()} -> {target}")
    return sorted(set(missing))


def _markdown_image_targets(text: str) -> list[str]:
    targets = [match.group(1) for match in MARKDOWN_IMAGE_RE.finditer(text)]
    targets.extend(match.group(1) for match in HTML_IMAGE_RE.finditer(text))
    return [_clean_markdown_image_target(target) for target in targets if _clean_markdown_image_target(target)]


def _clean_markdown_image_target(target: str) -> str:
    target = target.strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    if not target:
        return ""
    if target[0] in {"'", '"'}:
        quote = target[0]
        end = target.find(quote, 1)
        return target[1:end] if end != -1 else target.strip(quote)
    return target.split()[0]


def _resolve_markdown_image_target(staged_path: Path, markdown_relative: Path, target: str) -> Path | None:
    if _is_external_or_anchor_target(target):
        return None
    target = target.split("#", 1)[0].split("?", 1)[0]
    if not target:
        return None
    target_path = Path(target)
    if target_path.is_absolute():
        return staged_path / "__missing_absolute_or_parent_link__"
    resolved = (staged_path / markdown_relative.parent / target_path).resolve()
    try:
        resolved.relative_to(staged_path.resolve())
    except ValueError:
        return staged_path / "__missing_absolute_or_parent_link__"
    return resolved


def _is_external_or_anchor_target(target: str) -> bool:
    lowered = target.lower()
    return (
        lowered.startswith("#")
        or lowered.startswith("http://")
        or lowered.startswith("https://")
        or lowered.startswith("mailto:")
        or lowered.startswith("data:")
    )


def _existing_image_assets(staged_path: Path) -> list[str]:
    assets: list[str] = []
    for path in staged_path.rglob("*"):
        if path.is_file() and path.suffix.lower() in CHAT_HANDOFF_ASSET_SUFFIXES:
            assets.append(path.relative_to(staged_path).as_posix())
    return sorted(assets)[:40]


def _readme_brief(result: ProjectRunResult) -> str:
    if not result.prepared:
        return "Create a polished public project README with a clear visual overview."
    staged_path = result.prepared.staged_path
    if (staged_path / "package.json").exists():
        package_text = _read_optional_text(staged_path / "package.json")
        if package_text and any(framework in package_text for framework in ('"next"', '"react"', '"vite"', '"vue"', '"svelte"')):
            return "Web/app project: use a real UI screenshot or high-fidelity product visual as the README hero."
        return "Node/tooling project: use a workflow, terminal, or data-output visual as the README hero."
    if (staged_path / "pyproject.toml").exists():
        return "Python CLI/library project: use a terminal, report, or workflow visual as the README hero."
    if (staged_path / "go.mod").exists() or (staged_path / "Cargo.toml").exists() or (staged_path / "Package.swift").exists():
        return "Systems/tooling project: use an architecture, terminal, or workflow visual as the README hero."
    existing_images = _existing_image_assets(staged_path)
    if existing_images:
        return "Project includes existing images: select the strongest safe image or create a presentation visual for the README hero."
    return "Create a polished public project README with a diagram, screenshot, or process visual as the README hero."


def _read_optional_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            return None
    except OSError:
        return None


def _run_private_first(
    result: ProjectRunResult,
    options: RunOptions,
    client: GitHubClient,
    repos: list[GitHubRepo],
    targeted_private_repos: set[str],
) -> None:
    private_repo, needs_confirmation = _private_repo_for_candidate(result.candidate, repos, options.owner)
    if private_repo is None:
        return
    result.private_repo = private_repo
    if private_repo.full_name in targeted_private_repos:
        result.private_action = "skipped"
        result.private_detail = "Another local project candidate already targeted this private repository."
        return
    targeted_private_repos.add(private_repo.full_name)
    if options.no_sync:
        result.private_action = "skipped"
        result.private_detail = "Private sync disabled for this run."
        return
    result.private_action = "private-sync-dry-run" if options.dry_run else "private-synced"
    try:
        sync_repo = private_repo
        prefix = ""
        if needs_confirmation:
            if options.dry_run:
                result.private_detail = (
                    f"Would create or confirm private repository {private_repo.full_name}, "
                    "then push the original project contents."
                )
                return
            sync_repo = client.ensure_private_repo(private_repo.name)
            result.private_repo = sync_repo
            if not any(repo.full_name == sync_repo.full_name for repo in repos):
                repos.append(sync_repo)
            prefix = f"Ensured private repository {sync_repo.full_name}. "
        result.private_detail = prefix + sync_private_project(
            sync_repo,
            result.candidate.path,
            dry_run=options.dry_run,
            initialize_if_missing=True,
        )
    except RuntimeError as exc:
        result.private_action = "failed"
        result.private_detail = f"Private repository update failed: {exc}"


def _private_repo_for_candidate(
    candidate,
    repos: list[GitHubRepo],
    owner: str,
) -> tuple[GitHubRepo | None, bool]:
    repos_by_name = {repo.name: repo for repo in repos}
    repos_by_exact = {normalize_name(repo.name): repo for repo in repos}

    if candidate.github_remote:
        if candidate.github_remote.owner != owner:
            return None, False
        remote_repo = repos_by_name.get(candidate.github_remote.name)
        if remote_repo:
            if remote_repo.is_private:
                return remote_repo, False
            return None, False
        return (
            GitHubRepo(
                owner=owner,
                name=candidate.github_remote.name,
                url=f"https://github.com/{owner}/{candidate.github_remote.name}",
                is_private=True,
            ),
            True,
        )

    exact = repos_by_exact.get(candidate.slug)
    if exact and exact.is_private:
        return exact, False
    return None, False


def _with_public_counterpart_candidates(
    top_level_candidates,
    documents_root: Path,
    workspace: Path,
    repos: list[GitHubRepo],
):
    public_slugs = {normalize_name(repo.name) for repo in repos if not repo.is_private}
    top_paths = {candidate.path for candidate in top_level_candidates}
    nested = scan_projects(
        documents_root,
        excluded_roots=[workspace],
        collapse_nested=False,
        respect_git_roots=False,
    )
    best_by_slug = {}
    for candidate in nested:
        if candidate.path in top_paths or candidate.slug not in public_slugs:
            continue
        previous = best_by_slug.get(candidate.slug)
        if previous is None or _freshness(candidate.path) > _freshness(previous.path):
            best_by_slug[candidate.slug] = candidate
    return list(top_level_candidates) + list(best_by_slug.values())


def _freshness(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _is_approved_for_new_publish(slug: str, options: RunOptions) -> bool:
    if options.approve_new and slug in options.approve_new:
        return True
    if options.dry_run:
        return bool(options.approve_new and slug in options.approve_new)
    if not options.interactive or not sys.stdin.isatty():
        return False
    answer = input(f"Publish new public GitHub repo {options.owner}/{slug}? [y/N] ").strip().lower()
    return answer in {"y", "yes"}
