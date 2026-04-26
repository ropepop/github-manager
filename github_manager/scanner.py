from __future__ import annotations

import os
from pathlib import Path

from .github import get_git_root, get_origin_repo
from .models import ProjectCandidate
from .naming import normalize_name


MANIFEST_FILES = {
    "package.json",
    "pyproject.toml",
    "Cargo.toml",
    "go.mod",
    "Package.swift",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "requirements.txt",
    "composer.json",
    "Gemfile",
    "module.yaml",
}

PROJECT_HINT_FILES = {"README.md", "README", "LICENSE", "Makefile"}
PROJECT_HINT_DIRS = {
    ".github",
    "src",
    "app",
    "cmd",
    "internal",
    "tools",
    "scripts",
    "workloads",
    "automation",
    "orchestrator",
    "docs",
    "tests",
}

IGNORED_DIR_NAMES = {
    ".git",
    ".github-manager",
    ".hg",
    ".svn",
    "node_modules",
    ".next",
    "dist",
    "build",
    "target",
    ".venv",
    "venv",
    "__pycache__",
    ".cache",
    ".pytest_cache",
    ".playwright-cli",
    ".playwright-mcp",
    ".artifacts",
    "coverage",
    "output",
    "releases",
}


def scan_projects(
    documents_root: Path,
    excluded_roots: list[Path] | None = None,
    collapse_nested: bool = True,
    respect_git_roots: bool = True,
) -> list[ProjectCandidate]:
    documents_root = documents_root.expanduser().resolve()
    excluded = [path.expanduser().resolve() for path in (excluded_roots or [])]
    raw: dict[Path, ProjectCandidate] = {}

    for root, dirs, files in os.walk(documents_root):
        root_path = Path(root).resolve()
        if _is_excluded(root_path, excluded):
            dirs[:] = []
            continue
        dirs[:] = [name for name in dirs if name not in IGNORED_DIR_NAMES]

        file_names = set(files)
        dir_names = set(dirs)
        score, markers = _score_directory(file_names, dir_names)
        if score < 4:
            continue

        git_root = get_git_root(root_path) if respect_git_roots else None
        candidate_path = git_root if git_root and _is_inside(git_root, documents_root) else root_path
        if _is_excluded(candidate_path, excluded):
            continue
        candidate_score, candidate_markers = _score_path(candidate_path)
        if candidate_path in raw and raw[candidate_path].score >= candidate_score:
            continue
        github_remote = get_origin_repo(candidate_path) if git_root else None
        raw[candidate_path] = ProjectCandidate(
            path=candidate_path,
            name=candidate_path.name,
            slug=normalize_name(candidate_path.name),
            markers=candidate_markers or markers,
            score=max(candidate_score, score),
            git_root=git_root if git_root and git_root == candidate_path else None,
            github_remote=github_remote,
        )

    candidates = sorted(raw.values(), key=lambda item: (len(item.path.parts), str(item.path)))
    selected: list[ProjectCandidate] = []
    for candidate in candidates:
        if collapse_nested and any(_is_inside(candidate.path, chosen.path) and candidate.path != chosen.path for chosen in selected):
            continue
        selected.append(candidate)
    return selected


def _score_path(path: Path) -> tuple[int, list[str]]:
    try:
        entries = {child.name for child in path.iterdir()}
    except OSError:
        return 0, []
    files = {name for name in entries if (path / name).is_file()}
    dirs = {name for name in entries if (path / name).is_dir()}
    return _score_directory(files, dirs)


def _score_directory(files: set[str], dirs: set[str]) -> tuple[int, list[str]]:
    score = 0
    markers: list[str] = []
    manifests = sorted(files & MANIFEST_FILES)
    if manifests:
        score += 5 + min(len(manifests), 3)
        markers.extend(manifests)
    hint_files = sorted(files & PROJECT_HINT_FILES)
    if "README.md" in hint_files or "README" in hint_files:
        score += 2
    if "LICENSE" in hint_files:
        score += 1
    if "Makefile" in hint_files:
        score += 1
    markers.extend(hint_files)
    hint_dirs = sorted(dirs & PROJECT_HINT_DIRS)
    if ".github" in hint_dirs:
        score += 3
    if hint_dirs:
        score += 1
    markers.extend(f"{name}/" for name in hint_dirs)
    return score, markers


def _is_excluded(path: Path, excluded_roots: list[Path]) -> bool:
    return any(path == excluded or _is_inside(path, excluded) for excluded in excluded_roots)


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
