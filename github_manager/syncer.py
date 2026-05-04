from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .github import parse_github_remote
from .models import GitHubRepo


def sync_private_project(repo: GitHubRepo, project_path: Path, dry_run: bool = False, initialize_if_missing: bool = False) -> str:
    git_root = _git_root(project_path)
    if git_root is None:
        if not initialize_if_missing:
            raise RuntimeError("Private sync requires the project folder to be a Git repository.")
        if dry_run:
            return f"Would initialize local Git and push project contents to private repository {repo.full_name}."
        _run(["git", "init", "-b", "main"], cwd=project_path)
        git_root = project_path.resolve()
    branch = _current_branch(git_root)
    if branch is None:
        if not initialize_if_missing:
            raise RuntimeError("Private sync requires the project to be on a named Git branch.")
        if not dry_run:
            _run(["git", "checkout", "-B", "main"], cwd=git_root)
        branch = "main"
    origin_url = _origin_url(git_root)
    if origin_url:
        origin_repo = parse_github_remote(origin_url)
        if origin_repo and origin_repo.full_name != repo.full_name:
            raise RuntimeError(f"Project origin points to {origin_repo.full_name}, not {repo.full_name}.")
        if not origin_repo and origin_url != repo.url:
            raise RuntimeError("Project origin does not match the private GitHub repository.")
    elif initialize_if_missing:
        if not dry_run:
            _set_origin(git_root, repo.url)
    else:
        raise RuntimeError("Project does not have a GitHub origin for private sync.")

    status = _run(["git", "status", "--porcelain"], cwd=git_root).stdout.strip()
    changed_count = len(status.splitlines())
    if dry_run:
        if origin_url:
            if changed_count:
                return f"Would commit and push latest local changes to {repo.full_name}; {changed_count} file change(s) detected."
            return f"Would push current private branch to {repo.full_name}; no file changes detected."
        if changed_count:
            return f"Would add private origin, commit, and push to {repo.full_name}; {changed_count} file change(s) detected."
        return f"Would add private origin and push current branch to {repo.full_name}; no file changes detected."

    if status:
        _run(["git", "add", "-A"], cwd=git_root)
        staged_status = _run(["git", "status", "--porcelain"], cwd=git_root).stdout.strip()
        if staged_status:
            _run(
                [
                    "git",
                    "-c",
                    "user.name=GitHub Manager",
                    "-c",
                    "user.email=github-manager@local",
                    "commit",
                    "--no-verify",
                    "-m",
                    "Commit latest local changes",
                ],
                cwd=git_root,
            )
    _run(["git", "push", "origin", f"HEAD:{branch}"], cwd=git_root)
    if not status:
        return f"No private changes to commit for {repo.full_name}; pushed current branch."
    return f"Committed and pushed latest local changes to private repository {repo.full_name}."


def sync_staged_project(repo: GitHubRepo, staged_path: Path, workspace: Path, dry_run: bool = False) -> str:
    clone_root = workspace / "remote-clones"
    clone_root.mkdir(parents=True, exist_ok=True)
    clone_path = clone_root / repo.name
    if clone_path.exists():
        shutil.rmtree(clone_path)

    _run(["git", "clone", repo.url, str(clone_path)], cwd=workspace)
    _replace_tree(staged_path, clone_path)
    status = _run(["git", "status", "--porcelain"], cwd=clone_path).stdout.strip()
    if not status:
        return f"No changes to sync for {repo.full_name}."
    if dry_run:
        changed_count = len(status.splitlines())
        return f"Would sync sanitized copy to {repo.full_name}; {changed_count} file change(s) detected."
    _run(["git", "add", "-A"], cwd=clone_path)
    _run(
        [
            "git",
            "-c",
            "user.name=GitHub Manager",
            "-c",
            "user.email=github-manager@local",
            "commit",
            "-m",
            "Sync sanitized local source",
        ],
        cwd=clone_path,
    )
    branch = _current_branch(clone_path) or "main"
    _run(["git", "push", "origin", f"HEAD:{branch}"], cwd=clone_path)
    return f"Synced sanitized local source to {repo.full_name}."


def _replace_tree(source: Path, destination: Path) -> None:
    for child in destination.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    for child in source.iterdir():
        target = destination / child.name
        if child.is_dir():
            shutil.copytree(child, target)
        else:
            shutil.copy2(child, target)


def _current_branch(path: Path) -> str | None:
    result = subprocess.run(
        ["git", "symbolic-ref", "--short", "HEAD"],
        cwd=path,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _git_root(path: Path) -> Path | None:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=path,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def _origin_url(path: Path) -> str | None:
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=path,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _set_origin(path: Path, url: str) -> None:
    existing = _origin_url(path)
    if existing:
        result = subprocess.run(["git", "remote", "set-url", "origin", url], cwd=path, text=True, capture_output=True, check=False)
    else:
        result = subprocess.run(["git", "remote", "add", "origin", url], cwd=path, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result
