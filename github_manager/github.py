from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .models import GitHubRepo


GITHUB_REMOTE_RE = re.compile(
    r"(?:https://github\.com/|git@github\.com:)(?P<owner>[^/]+)/(?P<repo>[^/.]+)(?:\.git)?/?$"
)


def parse_github_remote(url: str) -> GitHubRepo | None:
    match = GITHUB_REMOTE_RE.search(url.strip())
    if not match:
        return None
    owner = match.group("owner")
    name = match.group("repo")
    return GitHubRepo(owner=owner, name=name, url=f"https://github.com/{owner}/{name}")


def run_command(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)


def get_git_root(path: Path) -> Path | None:
    result = run_command(["git", "-C", str(path), "rev-parse", "--show-toplevel"])
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def get_origin_repo(path: Path) -> GitHubRepo | None:
    result = run_command(["git", "-C", str(path), "remote", "get-url", "origin"])
    if result.returncode != 0:
        return None
    return parse_github_remote(result.stdout.strip())


class GitHubClient:
    def __init__(self, owner: str) -> None:
        self.owner = owner

    def list_repos(self) -> list[GitHubRepo]:
        result = run_command(
            [
                "gh",
                "repo",
                "list",
                self.owner,
                "--limit",
                "1000",
                "--json",
                "name,url,isPrivate",
            ]
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "Unable to list GitHub repositories.")
        payload = json.loads(result.stdout)
        repos: list[GitHubRepo] = []
        for item in payload:
            repos.append(
                GitHubRepo(
                    owner=self.owner,
                    name=item["name"],
                    url=item["url"],
                    is_private=bool(item.get("isPrivate")),
                )
            )
        return repos

    def create_public_repo_from_path(self, name: str, staged_path: Path) -> GitHubRepo:
        ensure_git_commit(staged_path, "Initial sanitized publish")
        full_name = f"{self.owner}/{name}"
        result = run_command(
            [
                "gh",
                "repo",
                "create",
                full_name,
                "--public",
                "--source",
                str(staged_path),
                "--remote",
                "origin",
                "--push",
            ],
            cwd=staged_path,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
        return GitHubRepo(owner=self.owner, name=name, url=f"https://github.com/{full_name}")


def ensure_git_commit(path: Path, message: str) -> None:
    if not (path / ".git").exists():
        result = run_command(["git", "init", "-b", "main"], cwd=path)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "git init failed")
    commands = [
        ["git", "add", "-A"],
        ["git", "-c", "user.name=GitHub Manager", "-c", "user.email=github-manager@local", "commit", "-m", message],
    ]
    for command in commands:
        result = run_command(command, cwd=path)
        if result.returncode != 0 and "nothing to commit" not in (result.stdout + result.stderr).lower():
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())

