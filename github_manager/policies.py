from __future__ import annotations

from pathlib import Path

from .models import Classification, GitHubRepo, ProjectCandidate


# Folders that must only ever live in private GitHub repositories.
# Maps the normalized project slug to (folder name under the documents root,
# private GitHub repository name). These folders are included in scans even
# when they would not otherwise score as coding projects, and the manager
# never prepares public sanitized copies for them.
PRIVATE_ONLY_PROJECTS: dict[str, tuple[str, str]] = {
    "my-workspace": ("My Workspace", "my-workspace"),
    "ios-activity": ("iOS activity", "ios-activity"),
}


def private_only_repo_name(slug: str) -> str | None:
    entry = PRIVATE_ONLY_PROJECTS.get(slug)
    return entry[1] if entry else None


def private_only_folder_name(slug: str) -> str | None:
    entry = PRIVATE_ONLY_PROJECTS.get(slug)
    return entry[0] if entry else None


def private_only_candidates(documents_root: Path) -> list[ProjectCandidate]:
    """Build explicit candidates for private-only folders under the documents root."""
    documents_root = documents_root.expanduser().resolve()
    candidates: list[ProjectCandidate] = []
    for slug, (folder, _) in PRIVATE_ONLY_PROJECTS.items():
        path = (documents_root / folder).resolve()
        if not path.is_dir():
            continue
        candidates.append(
            ProjectCandidate(
                path=path,
                name=path.name,
                slug=slug,
                markers=["private-only"],
            )
        )
    return candidates


def merge_candidates(candidates: list[ProjectCandidate], extra: list[ProjectCandidate]) -> list[ProjectCandidate]:
    """Append extra candidates whose paths are not already covered."""
    seen = {candidate.path for candidate in candidates}
    merged = list(candidates)
    for candidate in extra:
        if candidate.path not in seen:
            merged.append(candidate)
            seen.add(candidate.path)
    return merged


def classify_private_only(slug: str, repos: list[GitHubRepo], owner: str) -> Classification:
    """Classify a private-only project, raising when a same-named public repo exists."""
    repo_name = private_only_repo_name(slug)
    if repo_name is None:
        raise ValueError(f"{slug} is not a private-only project.")
    repo = next((candidate for candidate in repos if candidate.name == repo_name), None)
    if repo is not None and not repo.is_private:
        raise RuntimeError(f"{owner}/{repo_name} already exists, but it is not private.")
    if repo is None:
        repo = GitHubRepo(
            owner=owner,
            name=repo_name,
            url=f"https://github.com/{owner}/{repo_name}",
            is_private=True,
        )
        return Classification(
            status="private-only",
            match_method="private-only policy",
            repo=repo,
            reason="Private repository will be created on first sync; no public copy is published.",
        )
    return Classification(
        status="private-only",
        match_method="private-only policy",
        repo=repo,
        reason="Folder is private-only by policy; contents are synced to the private repository.",
    )
