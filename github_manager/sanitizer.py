from __future__ import annotations

import fnmatch
import re
import shutil
from pathlib import Path

from .models import Finding, PreparedProject
from .readme import refresh_readme


GENERATED_DIR_NAMES = {
    ".agent",
    ".agents",
    ".git",
    ".codex",
    ".codex-tmp",
    ".github-manager",
    ".build",
    ".gradle",
    ".kotlin",
    ".swiftpm",
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
    ".tmp-playwright-interactive",
    ".trae",
    ".artifacts",
    "coverage",
    "dogfood-output",
    "evidence",
    "log",
    "logs",
    "output",
    "releases",
    "state",
    "tmp",
    "temp",
}

SKIPPED_FILE_NAMES = {
    ".DS_Store",
    '"$TMP"',
    "AGENTS.md",
    "Thumbs.db",
    "skills-lock.json",
}

SKIPPED_FILE_PATTERNS = [
    "*.tsbuildinfo",
    "~$*",
]

BLOCKED_FILE_PATTERNS = [
    ".env",
    ".env.*",
    "*.env",
    "local.env",
    "*.local",
    "*.db",
    "*.db-*",
    "*.sqlite",
    "*.sqlite3",
    "*.duckdb",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.secret",
    "*.secrets",
    "id_rsa",
    "id_ed25519",
    "*.mobileconfig",
    "*production*.json",
    "*production*.yaml",
    "*production*.yml",
    "*.tar",
    "*.tar.gz",
    "*.tgz",
    "*.zip",
    "*.7z",
    "*.dmg",
    "*.iso",
    "*.apk",
    "*.ipa",
    "*.exe",
    "*.bin",
]

ALLOWLISTED_BLOCKED_NAMES = {
    ".env.example",
    ".env.sample",
    "env.example",
    "env.sample",
}

ALLOWLISTED_BLOCKED_SUFFIXES = (
    ".example.env",
    ".sample.env",
)

SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{24,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"(?i)\b(password|api[_-]?key|secret|token)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{12,}"),
]

TEXT_EXTENSIONS = {
    "",
    ".bash",
    ".c",
    ".cc",
    ".cfg",
    ".conf",
    ".cpp",
    ".css",
    ".csv",
    ".dockerfile",
    ".go",
    ".gradle",
    ".h",
    ".hpp",
    ".html",
    ".java",
    ".js",
    ".json",
    ".jsx",
    ".cjs",
    ".mjs",
    ".cts",
    ".mts",
    ".kt",
    ".kts",
    ".lock",
    ".log",
    ".m",
    ".md",
    ".mod",
    ".mm",
    ".properties",
    ".pro",
    ".py",
    ".rb",
    ".rs",
    ".sh",
    ".bat",
    ".sql",
    ".swift",
    ".svg",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vue",
    ".svelte",
    ".webmanifest",
    ".code-snippets",
    ".plist",
    ".raw",
    ".service",
    ".sum",
    ".template",
    ".xml",
    ".yaml",
    ".yml",
}

SAFE_BINARY_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2", ".ttf", ".otf"}


def prepare_project(
    source_path: Path,
    staging_root: Path,
    slug: str,
    drop_blocked_files: bool = False,
    refresh_readme_file: bool = True,
) -> PreparedProject:
    source_path = source_path.resolve()
    staged_path = (staging_root / slug).resolve()
    if staged_path.exists():
        shutil.rmtree(staged_path)
    staged_path.mkdir(parents=True, exist_ok=True)

    source_findings = inspect_source(source_path)
    copied_files = 0
    skipped_files = 0
    for path in source_path.rglob("*"):
        if path == staged_path or staged_path in path.parents:
            continue
        relative = path.relative_to(source_path)
        if _is_generated_path(relative):
            if path.is_file():
                skipped_files += 1
            continue
        if path.is_dir():
            (staged_path / relative).mkdir(parents=True, exist_ok=True)
            continue
        if path.is_file():
            if _should_skip_file(path.name) or _is_blocked_filename(path.name):
                skipped_files += 1
                continue
            destination = staged_path / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            copied_files += 1

    removed_findings: list[Finding] = []
    staged_findings = inspect_source(staged_path)
    findings = _dedupe_findings(source_findings + staged_findings)
    if drop_blocked_files:
        removed_findings = _dedupe_findings(source_findings + staged_findings)
        for finding in staged_findings:
            target = staged_path / finding.path
            if target.exists() and target.is_file():
                target.unlink()
                skipped_files += 1
        staged_findings = inspect_source(staged_path)
        findings = staged_findings

    readme_result = None
    if refresh_readme_file:
        readme_result = refresh_readme(staged_path, slug, removed_findings, staged_findings=staged_findings)
        refreshed_findings = inspect_source(staged_path)
        findings = refreshed_findings if drop_blocked_files else _dedupe_findings(source_findings + refreshed_findings)

    return PreparedProject(
        source_path=source_path,
        staged_path=staged_path,
        findings=findings,
        copied_files=copied_files,
        skipped_files=skipped_files,
        removed_findings=removed_findings,
        readme_result=readme_result,
    )


def inspect_source(source_path: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in source_path.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(source_path)
        if _is_generated_path(relative):
            continue
        if _should_skip_file(path.name):
            continue
        relative_text = str(relative)
        if _is_blocked_filename(path.name):
            findings.append(Finding("block", relative_text, "The file name or type is not safe for public publishing."))
            continue
        try:
            size = path.stat().st_size
        except OSError:
            findings.append(Finding("block", relative_text, "The file could not be inspected."))
            continue
        if size > 5 * 1024 * 1024:
            findings.append(Finding("block", relative_text, "The file is larger than the public publishing limit."))
            continue
        if path.suffix.lower() in SAFE_BINARY_EXTENSIONS:
            continue
        if _looks_binary(path):
            if path.suffix.lower() not in SAFE_BINARY_EXTENSIONS:
                findings.append(Finding("block", relative_text, "The file appears to be binary and needs review."))
            continue
        text = _read_text(path)
        if text is None:
            findings.append(Finding("block", relative_text, "The text file could not be decoded safely."))
            continue
        if re.search(r"/Users/[A-Za-z0-9._-]+/", text):
            findings.append(Finding("block", relative_text, "The file contains a local user path."))
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                findings.append(Finding("block", relative_text, "The file appears to contain a secret or credential."))
                break
    return findings


def _is_generated_path(relative: Path) -> bool:
    return any(part in GENERATED_DIR_NAMES for part in relative.parts)


def _should_skip_file(name: str) -> bool:
    return name in SKIPPED_FILE_NAMES or any(fnmatch.fnmatch(name, pattern) for pattern in SKIPPED_FILE_PATTERNS)


def _is_blocked_filename(name: str) -> bool:
    if name in ALLOWLISTED_BLOCKED_NAMES or name.endswith(ALLOWLISTED_BLOCKED_SUFFIXES):
        return False
    return any(fnmatch.fnmatch(name, pattern) for pattern in BLOCKED_FILE_PATTERNS)


def _looks_binary(path: Path) -> bool:
    if path.suffix.lower() in SAFE_BINARY_EXTENSIONS:
        return False
    try:
        chunk = path.read_bytes()[:4096]
    except OSError:
        return True
    return b"\0" in chunk


def _read_text(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if (
        suffix not in TEXT_EXTENSIONS
        and path.name not in {"Dockerfile", "Makefile", "Gemfile"}
        and path.name not in ALLOWLISTED_BLOCKED_NAMES
        and not path.name.endswith(ALLOWLISTED_BLOCKED_SUFFIXES)
    ):
        return None
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            return None
    except OSError:
        return None


def _dedupe_findings(findings: list[Finding]) -> list[Finding]:
    seen: set[tuple[str, str]] = set()
    deduped: list[Finding] = []
    for finding in findings:
        key = (finding.path, finding.message)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(finding)
    return deduped
