from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .models import GitHubRepo


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


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result
