from __future__ import annotations

from difflib import SequenceMatcher

from .models import Classification, GitHubRepo, ProjectCandidate
from .naming import normalize_name


def classify_project(
    candidate: ProjectCandidate,
    repos: list[GitHubRepo],
    owner: str,
    prefer_sanitized_counterpart: bool = False,
    public_only: bool = False,
) -> Classification:
    repos_by_exact = {normalize_name(repo.name): repo for repo in repos}
    repos_by_name = {repo.name: repo for repo in repos}

    if candidate.github_remote:
        if candidate.github_remote.owner == owner:
            remote_repo = repos_by_name.get(candidate.github_remote.name)
            if prefer_sanitized_counterpart:
                counterpart = _public_counterpart(candidate.slug, repos_by_exact)
                if counterpart and (remote_repo is None or remote_repo.is_private):
                    return Classification(
                        status="published",
                        match_method="public counterpart",
                        repo=counterpart,
                        reason="A public sanitized counterpart exists for this private local repository.",
                    )
            if public_only and (remote_repo is None or remote_repo.is_private):
                return Classification(
                    status="published-private",
                    match_method="local remote",
                    repo=remote_repo or candidate.github_remote,
                    reason="The local project points to a private or unconfirmed repository, and this run targets public sanitized repos only.",
                )
            return Classification(
                status="published",
                match_method="local remote",
                repo=remote_repo or candidate.github_remote,
                reason="The local project already points to a GitHub repository for this account.",
            )
        return Classification(
            status="published-external",
            match_method="local remote",
            repo=candidate.github_remote,
            reason="The local project points to a GitHub repository owned by a different account.",
        )

    exact = repos_by_exact.get(candidate.slug)
    if exact:
        if public_only and exact.is_private:
            return Classification(
                status="published-private",
                match_method="exact name",
                repo=exact,
                reason="A matching repository exists, but it is private and this run targets public sanitized repos only.",
            )
        return Classification(
            status="published",
            match_method="exact name",
            repo=exact,
            reason="A GitHub repository has the same normalized name as this folder.",
        )

    fuzzy = _high_confidence_match(candidate.slug, repos)
    if fuzzy:
        if public_only and fuzzy.is_private:
            return Classification(
                status="published-private",
                match_method="high-confidence name",
                repo=fuzzy,
                reason="A matching repository exists, but it is private and this run targets public sanitized repos only.",
            )
        return Classification(
            status="published",
            match_method="high-confidence name",
            repo=fuzzy,
            reason="A GitHub repository name is an unusually close match for this folder.",
        )

    return Classification(
        status="unpublished",
        match_method="none",
        repo=None,
        reason="No GitHub remote or high-confidence repository match was found.",
    )


def _public_counterpart(slug: str, repos_by_exact: dict[str, GitHubRepo]) -> GitHubRepo | None:
    for name in (slug, f"public-{slug}", f"{slug}-public"):
        repo = repos_by_exact.get(name)
        if repo and not repo.is_private:
            return repo
    return None


def _high_confidence_match(slug: str, repos: list[GitHubRepo]) -> GitHubRepo | None:
    scored = sorted(
        (
            (SequenceMatcher(None, slug, normalize_name(repo.name)).ratio(), repo)
            for repo in repos
        ),
        key=lambda item: item[0],
        reverse=True,
    )
    if not scored:
        return None
    best_score, best_repo = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    if best_score >= 0.92 and best_score - second_score >= 0.05:
        return best_repo
    return None
