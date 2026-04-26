from __future__ import annotations

from pathlib import Path

from .models import Finding


README_START = "<!-- github-manager-readme:start -->"
README_END = "<!-- github-manager-readme:end -->"


def refresh_readme(staged_path: Path, slug: str, removed_findings: list[Finding]) -> None:
    readme_path = staged_path / "README.md"
    note = _managed_note(staged_path, removed_findings)
    if readme_path.exists():
        existing = readme_path.read_text(encoding="utf-8")
        readme_path.write_text(_replace_or_append_note(existing, note), encoding="utf-8")
        return
    readme_path.write_text(_generated_readme(slug, staged_path, note), encoding="utf-8")


def _generated_readme(slug: str, staged_path: Path, note: str) -> str:
    title = _title(slug)
    contents = _contents(staged_path)
    lines = [
        f"# {title}",
        "",
        "This repository is a public-safe copy maintained from a local working project.",
        "",
        "## Contents",
        "",
    ]
    if contents:
        lines.extend(f"- `{item}`" for item in contents)
    else:
        lines.append("- Project files are prepared during the next sanitized update.")
    lines.extend(["", note.rstrip(), ""])
    return "\n".join(lines)


def _managed_note(staged_path: Path, removed_findings: list[Finding]) -> str:
    contents = _contents(staged_path)
    lines = [
        README_START,
        "## Public Copy Notes",
        "",
        "This repository is refreshed by GitHub Manager from a sanitized staging copy.",
        "Files that look private, generated, or unsafe for public release are left out before each update.",
        "",
        f"- Risky files removed in the latest sanitation pass: {len(removed_findings)}",
        f"- Top-level items in this public copy: {', '.join(f'`{item}`' for item in contents[:8]) or 'none'}",
        README_END,
    ]
    return "\n".join(lines)


def _replace_or_append_note(existing: str, note: str) -> str:
    if README_START in existing and README_END in existing:
        before, rest = existing.split(README_START, 1)
        _, after = rest.split(README_END, 1)
        return before.rstrip() + "\n\n" + note + after.rstrip() + "\n"
    return existing.rstrip() + "\n\n" + note + "\n"


def _contents(staged_path: Path) -> list[str]:
    try:
        children = sorted(staged_path.iterdir(), key=lambda path: (not path.is_dir(), path.name.lower()))
    except OSError:
        return []
    names: list[str] = []
    for child in children:
        if child.name == "README.md":
            continue
        if child.name.startswith(".") and child.name not in {".github", ".gitignore"}:
            continue
        suffix = "/" if child.is_dir() else ""
        names.append(f"{child.name}{suffix}")
    return names[:12]


def _title(slug: str) -> str:
    words = [word for word in slug.replace("_", "-").split("-") if word]
    return " ".join(word.capitalize() for word in words) or "Project"
