from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from .classifier import classify_project
from .github import GitHubClient
from .models import GitHubRepo, ProjectRunResult
from .naming import normalize_name
from .reporting import write_run_report
from .sanitizer import prepare_project
from .scanner import scan_projects
from .syncer import sync_private_project, sync_staged_project


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
            refresh_readme_file=options.refresh_readme,
        )
        result.prepared = prepared
        if not prepared.clean:
            result.action = "blocked"
            result.detail = "Strict checks found files that need review before publishing or syncing."
            results.append(result)
            continue

        if classification.is_publishable_existing and classification.repo:
            if options.no_sync:
                result.action = "skipped"
                result.detail = "Sync disabled for this run."
            else:
                result.action = "sync-dry-run" if options.dry_run else "synced"
                result.detail = sync_staged_project(classification.repo, prepared.staged_path, workspace, dry_run=options.dry_run)
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
                result.action = "publish-dry-run"
                result.detail = f"Would create public GitHub repository {options.owner}/{candidate.slug}."
            else:
                repo = client.create_public_repo_from_path(candidate.slug, prepared.staged_path)
                result.classification.repo = repo
                result.action = "published"
                result.detail = f"Created public GitHub repository {repo.full_name}."
            results.append(result)
            continue

        result.action = "skipped"
        result.detail = "No supported action for this project state."
        results.append(result)

    report_path = write_run_report(workspace, results, options.dry_run)
    return results, report_path


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
