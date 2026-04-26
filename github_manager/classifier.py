from __future__ import annotations

from difflib import SequenceMatcher

from .models import Classification, GitHubRepo, ProjectCandidate
from .naming import normalize_name


def classify_project(candidate: ProjectCandidate, repos: list[GitHubRepo], owner: str) -> Classification:
    repos_by_exact = {normalize_name(repo.name): repo for repo in repos}

    if candidate.github_remote:
        if candidate.github_remote.owner == owner:
            return Classification(
                status="published",
                match_method="local remote",
                repo=candidate.github_remote,
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
        return Classification(
            status="published",
            match_method="exact name",
            repo=exact,
            reason="A GitHub repository has the same normalized name as this folder.",
        )

    fuzzy = _high_confidence_match(candidate.slug, repos)
    if fuzzy:
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

