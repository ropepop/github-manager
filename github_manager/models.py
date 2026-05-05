from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class GitHubRepo:
    owner: str
    name: str
    url: str
    is_private: bool = False

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


@dataclass
class ProjectCandidate:
    path: Path
    name: str
    slug: str
    markers: list[str] = field(default_factory=list)
    score: int = 0
    git_root: Path | None = None
    github_remote: GitHubRepo | None = None


@dataclass
class Classification:
    status: str
    match_method: str
    repo: GitHubRepo | None = None
    reason: str = ""

    @property
    def is_publishable_existing(self) -> bool:
        return self.status == "published"

    @property
    def is_unpublished(self) -> bool:
        return self.status == "unpublished"


@dataclass
class Finding:
    severity: str
    path: str
    message: str

    @property
    def blocks_publish(self) -> bool:
        return self.severity == "block"


@dataclass(frozen=True)
class ReadmeRefreshResult:
    mode: str
    detail: str
    model: str | None = None
    archived_previous: bool = False


@dataclass
class PreparedProject:
    source_path: Path
    staged_path: Path
    findings: list[Finding]
    copied_files: int
    skipped_files: int
    removed_findings: list[Finding] = field(default_factory=list)
    readme_result: ReadmeRefreshResult | None = None

    @property
    def clean(self) -> bool:
        return not any(finding.blocks_publish for finding in self.findings)


@dataclass
class ProjectRunResult:
    candidate: ProjectCandidate
    classification: Classification
    prepared: PreparedProject | None = None
    private_repo: GitHubRepo | None = None
    private_action: str = "none"
    private_detail: str = ""
    chat_handoff_action: str = "none"
    chat_handoff_detail: str = ""
    chat_handoff_files: list[str] = field(default_factory=list)
    chat_handoff_assets: list[str] = field(default_factory=list)
    chat_handoff_path: Path | None = None
    action: str = "none"
    detail: str = ""
