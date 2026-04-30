from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Callable

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on older system Python.
    tomllib = None

from .models import Finding, ReadmeRefreshResult


README_START = "<!-- github-manager-readme:start -->"
README_END = "<!-- github-manager-readme:end -->"
PREVIOUS_README_NAME = "README.previous.md"
DEFAULT_README_MODEL = "gpt-5-mini"
README_MODEL_ENV = "GITHUB_MANAGER_README_MODEL"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"

AIRequester = Callable[[dict[str, Any], str], dict[str, Any]]

LANGUAGE_BY_EXTENSION = {
    ".c": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".css": "CSS",
    ".go": "Go",
    ".h": "C/C++ headers",
    ".hpp": "C++ headers",
    ".html": "HTML",
    ".java": "Java",
    ".js": "JavaScript",
    ".jsx": "React",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".m": "Objective-C",
    ".mm": "Objective-C++",
    ".py": "Python",
    ".rb": "Ruby",
    ".rs": "Rust",
    ".sh": "Shell",
    ".swift": "Swift",
    ".ts": "TypeScript",
    ".tsx": "React / TypeScript",
    ".vue": "Vue",
}

DESCRIPTION_BY_TOP_LEVEL = {
    ".github/": "GitHub workflow and repository automation files.",
    "app/": "Application entry points and user-facing routes.",
    "automation/": "Automation jobs and supporting scripts.",
    "cmd/": "Command-line entry points.",
    "docs/": "Project documentation and reference material.",
    "github_manager/": "Core Python package for the manager.",
    "internal/": "Internal application packages.",
    "orchestrator/": "Runtime orchestration and deployment support.",
    "script/": "Build or launch helper scripts.",
    "scripts/": "Helper scripts for setup, maintenance, or verification.",
    "src/": "Primary source code.",
    "Sources/": "Swift source code.",
    "tests/": "Automated tests.",
    "Tests/": "Automated tests.",
    "tools/": "Developer and operations tooling.",
    "workloads/": "Runnable workload modules.",
}

DESCRIPTION_BY_FILE = {
    ".gitignore": "Ignore rules for generated and local-only files.",
    "LICENSE": "License for the public copy.",
    "package-lock.json": "NPM lockfile for reproducible installs.",
    "pnpm-lock.yaml": "PNPM lockfile for reproducible installs.",
    "yarn.lock": "Yarn lockfile for reproducible installs.",
}

SKIPPED_CONTENT_EXTENSIONS = {".gif", ".ico", ".jpeg", ".jpg", ".png", ".webp"}


def refresh_readme(
    staged_path: Path,
    slug: str,
    removed_findings: list[Finding],
    staged_findings: list[Finding] | None = None,
    ai_requester: AIRequester | None = None,
) -> ReadmeRefreshResult:
    readme_path = staged_path / "README.md"
    previous_text = _read_optional_text(readme_path)
    archived_previous = False
    if previous_text is not None:
        (staged_path / PREVIOUS_README_NAME).write_text(previous_text, encoding="utf-8")
        archived_previous = True

    context = _build_context(staged_path, slug, previous_text, removed_findings, archived_previous)
    model = os.environ.get(README_MODEL_ENV, DEFAULT_README_MODEL).strip() or DEFAULT_README_MODEL
    draft: dict[str, Any] | None = None
    detail = ""

    if staged_findings is None:
        detail = "Safety findings were not provided, so the local README generator was used."
    elif any(finding.blocks_publish for finding in staged_findings):
        detail = "The staged copy still has safety findings, so AI drafting was skipped."
    else:
        credential = os.environ.get("OPENAI_API_KEY", "").strip()
        if credential:
            draft, error = _draft_with_openai(context, credential, model, ai_requester)
            if draft:
                readme_path.write_text(_render_readme(draft, context), encoding="utf-8")
                return ReadmeRefreshResult(
                    mode="ai-drafted",
                    detail=f"README drafted with AI model {model}.",
                    model=model,
                    archived_previous=archived_previous,
                )
            detail = f"AI drafting was unavailable ({error}); the local README generator was used."
        else:
            detail = "OPENAI_API_KEY is not set, so the local README generator was used."

    draft = _local_draft(context)
    readme_path.write_text(_render_readme(draft, context), encoding="utf-8")
    return ReadmeRefreshResult(
        mode="local-generated",
        detail=detail,
        archived_previous=archived_previous,
    )


def _build_context(
    staged_path: Path,
    slug: str,
    previous_text: str | None,
    removed_findings: list[Finding],
    archived_previous: bool,
) -> dict[str, Any]:
    manifests = _manifest_summaries(staged_path)
    files = _visible_files(staged_path)
    top_level = _contents(staged_path)
    return {
        "slug": slug,
        "title": _best_title(slug, manifests, previous_text),
        "top_level_items": top_level,
        "project_map": _project_map(top_level, manifests),
        "manifests": manifests,
        "languages": _language_summary(files),
        "quick_start": _quick_start_commands(staged_path, manifests),
        "testing": _testing_commands(staged_path, manifests),
        "previous_readme_excerpt": _previous_readme_excerpt(previous_text),
        "previous_summary": _previous_summary(previous_text),
        "previous_what_it_does": _previous_section_items(previous_text, ("What It Does", "Overview", "Purpose")),
        "file_excerpts": _safe_file_excerpts(staged_path),
        "removed_risky_files_count": len(removed_findings),
        "archived_previous": archived_previous,
        "file_count": len(files),
    }


def _draft_with_openai(
    context: dict[str, Any],
    credential: str,
    model: str,
    ai_requester: AIRequester | None,
) -> tuple[dict[str, Any] | None, str]:
    payload = {
        "model": model,
        "input": [
            {
                "role": "system",
                "content": (
                    "Write portfolio-friendly public GitHub README content from sanitized project metadata. "
                    "Use only the supplied facts. Do not invent credentials, deployment targets, private business details, "
                    "or unlisted commands. Keep the tone clear, useful, and public-safe."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(context, ensure_ascii=True, sort_keys=True),
            },
        ],
        "max_output_tokens": 2500,
        "store": False,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "public_readme_draft",
                "strict": True,
                "schema": _readme_schema(),
            }
        },
    }
    try:
        response = ai_requester(payload, credential) if ai_requester else _openai_request(payload, credential)
    except Exception as exc:  # noqa: BLE001 - AI drafting must fall back cleanly.
        return None, str(exc)
    if response.get("status") == "incomplete":
        reason = response.get("incomplete_details", {}).get("reason", "incomplete response")
        return None, reason
    output_text = _extract_output_text(response)
    if not output_text:
        return None, "no text returned"
    try:
        raw_draft = json.loads(output_text)
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON returned: {exc}"
    draft = _normalize_draft(raw_draft, context)
    if not draft:
        return None, "draft did not contain enough usable content"
    return draft, ""


def _openai_request(payload: dict[str, Any], credential: str) -> dict[str, Any]:
    request = urllib.request.Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {credential}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI request failed with HTTP {exc.code}: {body[:400]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenAI request failed: {exc.reason}") from exc


def _readme_schema() -> dict[str, Any]:
    command_schema = {
        "type": "object",
        "properties": {
            "label": {"type": "string"},
            "command": {"type": "string"},
        },
        "required": ["label", "command"],
        "additionalProperties": False,
    }
    map_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "description": {"type": "string"},
        },
        "required": ["path", "description"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "pitch": {"type": "string"},
            "what_it_does": {"type": "array", "items": {"type": "string"}},
            "highlights": {"type": "array", "items": {"type": "string"}},
            "tech_stack": {"type": "array", "items": {"type": "string"}},
            "quick_start": {"type": "array", "items": command_schema},
            "project_map": {"type": "array", "items": map_schema},
            "testing": {"type": "array", "items": command_schema},
        },
        "required": [
            "title",
            "pitch",
            "what_it_does",
            "highlights",
            "tech_stack",
            "quick_start",
            "project_map",
            "testing",
        ],
        "additionalProperties": False,
    }


def _extract_output_text(response: dict[str, Any]) -> str | None:
    direct = response.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    for item in response.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if not isinstance(content, dict):
                continue
            if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                return content["text"]
    return None


def _normalize_draft(raw: Any, context: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    local = _local_draft(context)
    draft = {
        "title": _clean_text(raw.get("title")) or local["title"],
        "pitch": _clean_text(raw.get("pitch")) or local["pitch"],
        "what_it_does": _clean_string_list(raw.get("what_it_does")) or local["what_it_does"],
        "highlights": _clean_string_list(raw.get("highlights")) or local["highlights"],
        "tech_stack": _clean_string_list(raw.get("tech_stack")) or local["tech_stack"],
        "quick_start": _clean_commands(raw.get("quick_start")) or local["quick_start"],
        "project_map": _clean_map(raw.get("project_map")) or local["project_map"],
        "testing": _clean_commands(raw.get("testing")) or local["testing"],
    }
    if not draft["title"] or not draft["pitch"]:
        return None
    return draft


def _local_draft(context: dict[str, Any]) -> dict[str, Any]:
    title = context["title"]
    description = _manifest_description(context["manifests"]) or context["previous_summary"]
    languages = context["languages"]
    top_items = context["top_level_items"]
    stack = _tech_stack(context)
    pitch = description or f"{title} is a public GitHub project prepared from a sanitized working copy."
    what_it_does = [pitch]
    what_it_does.extend(item for item in context["previous_what_it_does"] if item.lower() != pitch.lower())
    if len(what_it_does) < 3 and top_items:
        what_it_does.append(f"Organizes the main public surface around {', '.join(top_items[:5])}.")
    if len(what_it_does) < 3:
        what_it_does.append(f"Includes {context['file_count']} public project file(s) in the sanitized copy.")
    highlights = [
        "Repository structure is summarized so visitors can quickly understand what is included.",
    ]
    if context["quick_start"]:
        highlights.append("Setup commands are inferred from the sanitized manifests and scripts.")
    if context["testing"]:
        highlights.append("Verification commands are listed for quick local checks.")
    if languages:
        highlights.append(f"Primary detected language signals: {', '.join(languages[:4])}.")
    if context["archived_previous"]:
        highlights.append(f"Previous public README content is available in {PREVIOUS_README_NAME}.")
    return {
        "title": title,
        "pitch": pitch,
        "what_it_does": what_it_does[:4],
        "highlights": highlights[:5],
        "tech_stack": stack,
        "quick_start": context["quick_start"],
        "project_map": context["project_map"],
        "testing": context["testing"],
    }


def _render_readme(draft: dict[str, Any], context: dict[str, Any]) -> str:
    lines = [
        f"# {_clean_heading(draft['title'])}",
        "",
        draft["pitch"].rstrip(".") + ".",
        "",
    ]
    _append_bullets(lines, "What It Does", draft["what_it_does"])
    _append_bullets(lines, "Highlights", draft["highlights"])
    _append_bullets(lines, "Tech Stack", draft["tech_stack"])
    _append_commands(lines, "Quick Start", draft["quick_start"], "No setup command was inferred from the sanitized project files.")
    _append_project_map(lines, draft["project_map"])
    _append_commands(lines, "Testing", draft["testing"], "No test command was inferred from the sanitized project files.")
    lines.extend(
        [
            README_START,
            "## Public Copy Notes",
            "",
            "- This README was generated by GitHub Manager for a sanitized public copy.",
            "- Files that look private, generated, or unsafe for public release are left out before each update.",
            f"- Risky files removed in the latest sanitation pass: {context['removed_risky_files_count']}",
        ]
    )
    if context["archived_previous"]:
        lines.append(f"- The previous sanitized README is preserved in `{PREVIOUS_README_NAME}`.")
    lines.extend([README_END, ""])
    return "\n".join(lines)


def _append_bullets(lines: list[str], title: str, items: list[str]) -> None:
    lines.extend([f"## {title}", ""])
    for item in items or ["No details were inferred from the sanitized project files."]:
        lines.append(f"- {item.rstrip('.')}.")
    lines.append("")


def _append_commands(lines: list[str], title: str, commands: list[dict[str, str]], fallback: str) -> None:
    lines.extend([f"## {title}", ""])
    if not commands:
        lines.extend([fallback, ""])
        return
    for index, command in enumerate(commands, 1):
        lines.extend(
            [
                f"{index}. {command['label'].rstrip('.')}.",
                "",
                "```bash",
                command["command"],
                "```",
                "",
            ]
        )


def _append_project_map(lines: list[str], project_map: list[dict[str, str]]) -> None:
    lines.extend(["## Project Map", ""])
    if not project_map:
        lines.extend(["No top-level project files were inferred from the sanitized project files.", ""])
        return
    for item in project_map:
        lines.append(f"- `{item['path']}`: {item['description'].rstrip('.')}.")
    lines.append("")


def _best_title(slug: str, manifests: list[dict[str, Any]], previous_text: str | None) -> str:
    previous_title = _previous_title(previous_text)
    if previous_title:
        return previous_title
    for manifest in manifests:
        name = manifest.get("name")
        if isinstance(name, str) and name.strip():
            return _title(name)
    return _title(slug)


def _previous_title(text: str | None) -> str | None:
    if not text:
        return None
    for line in text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            return title or None
    return None


def _manifest_summaries(staged_path: Path) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    pyproject = _read_toml(staged_path / "pyproject.toml")
    if pyproject:
        project = pyproject.get("project", {})
        summaries.append(
            {
                "kind": "Python package",
                "path": "pyproject.toml",
                "name": project.get("name"),
                "description": project.get("description"),
                "requires_python": project.get("requires-python"),
                "scripts": sorted((project.get("scripts") or {}).keys()),
            }
        )
    package = _read_json(staged_path / "package.json")
    if package:
        deps = sorted({**package.get("dependencies", {}), **package.get("devDependencies", {})}.keys())
        summaries.append(
            {
                "kind": "Node package",
                "path": "package.json",
                "name": package.get("name"),
                "description": package.get("description"),
                "package_manager": _package_manager(staged_path),
                "scripts": sorted((package.get("scripts") or {}).keys()),
                "dependencies": deps[:16],
            }
        )
    cargo = _read_toml(staged_path / "Cargo.toml")
    if cargo:
        package_data = cargo.get("package", {})
        summaries.append(
            {
                "kind": "Rust crate",
                "path": "Cargo.toml",
                "name": package_data.get("name"),
                "description": package_data.get("description"),
            }
        )
    go_mod = _read_optional_text(staged_path / "go.mod")
    if go_mod:
        module = _first_match(go_mod, r"^module\s+(.+)$")
        summaries.append({"kind": "Go module", "path": "go.mod", "name": module})
    package_swift = _read_optional_text(staged_path / "Package.swift")
    if package_swift is not None:
        name = _first_match(package_swift, r"name:\s*\"([^\"]+)\"")
        summaries.append({"kind": "Swift package", "path": "Package.swift", "name": name})
    requirements = _read_optional_text(staged_path / "requirements.txt")
    if requirements:
        deps = [
            line.strip()
            for line in requirements.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        summaries.append({"kind": "Python requirements", "path": "requirements.txt", "dependencies": deps[:16]})
    module_yaml = _read_optional_text(staged_path / "module.yaml")
    if module_yaml:
        name = _first_match(module_yaml, r"^name:\s*(.+)$")
        summaries.append({"kind": "Module manifest", "path": "module.yaml", "name": name})
    return summaries


def _manifest_description(manifests: list[dict[str, Any]]) -> str | None:
    for manifest in manifests:
        description = manifest.get("description")
        if isinstance(description, str) and description.strip():
            return description.strip()
    return None


def _tech_stack(context: dict[str, Any]) -> list[str]:
    stack: list[str] = []
    for manifest in context["manifests"]:
        kind = manifest["kind"]
        if kind == "Python package":
            suffix = f" ({manifest['requires_python']})" if manifest.get("requires_python") else ""
            stack.append(f"Python package{suffix}")
        elif kind == "Node package":
            deps = set(manifest.get("dependencies", []))
            frameworks = [name for name in ("next", "react", "vite", "vue", "svelte", "express") if name in deps]
            label = "Node.js package"
            if frameworks:
                label += f" with {', '.join(frameworks)}"
            stack.append(label)
        else:
            stack.append(kind)
    for language in context["languages"]:
        if language not in " ".join(stack):
            stack.append(language)
    return stack[:8] or ["Project files and documentation"]


def _project_map(top_level: list[str], manifests: list[dict[str, Any]]) -> list[dict[str, str]]:
    mapped: list[dict[str, str]] = []
    manifest_paths = {manifest["path"]: manifest["kind"] for manifest in manifests if manifest.get("path")}
    for item in top_level[:10]:
        description = DESCRIPTION_BY_TOP_LEVEL.get(item) or DESCRIPTION_BY_FILE.get(item)
        if not description and item in manifest_paths:
            kind = manifest_paths[item]
            if kind == "Module manifest":
                description = "Module manifest and public runtime metadata."
            else:
                description = f"{kind} manifest and package metadata."
        if not description:
            description = "Public project file or directory included in the sanitized copy."
        mapped.append({"path": item, "description": description})
    return mapped


def _quick_start_commands(staged_path: Path, manifests: list[dict[str, Any]]) -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []
    for manifest in manifests:
        if manifest["kind"] == "Python package":
            commands.append({"label": "Install the package in editable mode", "command": "python3 -m pip install -e ."})
            scripts = manifest.get("scripts") or []
            if scripts:
                commands.append({"label": f"Run the `{scripts[0]}` command help", "command": f"{scripts[0]} --help"})
            elif (staged_path / manifest.get("name", "").replace("-", "_") / "__main__.py").exists():
                commands.append(
                    {
                        "label": "Run the package module",
                        "command": f"python3 -m {manifest['name'].replace('-', '_')} --help",
                    }
                )
        elif manifest["kind"] == "Node package":
            manager = manifest.get("package_manager") or "npm"
            commands.append({"label": "Install dependencies", "command": f"{manager} install"})
            scripts = manifest.get("scripts") or []
            preferred = _preferred_script(scripts, ["dev", "start", "preview"])
            if preferred:
                commands.append({"label": f"Run `{preferred}`", "command": _package_script_command(manager, preferred)})
        elif manifest["kind"] == "Rust crate":
            commands.append({"label": "Run the Rust project", "command": "cargo run"})
        elif manifest["kind"] == "Go module":
            commands.append({"label": "Run Go packages", "command": "go run ./..."})
        elif manifest["kind"] == "Swift package":
            commands.append({"label": "Build the Swift package", "command": "swift build"})
    return _dedupe_commands(commands)[:4]


def _testing_commands(staged_path: Path, manifests: list[dict[str, Any]]) -> list[dict[str, str]]:
    commands: list[dict[str, str]] = []
    for manifest in manifests:
        if manifest["kind"] == "Python package" and (staged_path / "tests").exists():
            commands.append({"label": "Run Python tests", "command": "python3 -m unittest discover -q"})
        elif manifest["kind"] == "Node package":
            manager = manifest.get("package_manager") or "npm"
            scripts = manifest.get("scripts") or []
            preferred = _preferred_script(scripts, ["test", "lint", "typecheck"], allow_fallback=False)
            if preferred:
                commands.append({"label": f"Run `{preferred}`", "command": _package_script_command(manager, preferred)})
        elif manifest["kind"] == "Rust crate":
            commands.append({"label": "Run Rust tests", "command": "cargo test"})
        elif manifest["kind"] == "Go module":
            commands.append({"label": "Run Go tests", "command": "go test ./..."})
        elif manifest["kind"] == "Swift package":
            commands.append({"label": "Run Swift tests", "command": "swift test"})
    return _dedupe_commands(commands)[:4]


def _package_manager(staged_path: Path) -> str:
    if (staged_path / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (staged_path / "yarn.lock").exists():
        return "yarn"
    if (staged_path / "bun.lockb").exists() or (staged_path / "bun.lock").exists():
        return "bun"
    return "npm"


def _package_script_command(manager: str, script: str) -> str:
    if manager in {"npm", "bun"}:
        return f"{manager} run {script}"
    return f"{manager} {script}"


def _preferred_script(scripts: list[str], preferred: list[str], allow_fallback: bool = True) -> str | None:
    for name in preferred:
        if name in scripts:
            return name
    return scripts[0] if allow_fallback and scripts else None


def _visible_files(staged_path: Path) -> list[Path]:
    files: list[Path] = []
    for path in staged_path.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(staged_path)
        if _is_hidden_or_archive(relative):
            continue
        files.append(relative)
    return files


def _language_summary(files: list[Path]) -> list[str]:
    counts = Counter(LANGUAGE_BY_EXTENSION.get(path.suffix.lower()) for path in files)
    counts.pop(None, None)
    return [language for language, _ in counts.most_common(8)]


def _safe_file_excerpts(staged_path: Path) -> list[dict[str, str]]:
    excerpts: list[dict[str, str]] = []
    for name in (
        "pyproject.toml",
        "package.json",
        "Cargo.toml",
        "go.mod",
        "Package.swift",
        "requirements.txt",
        "module.yaml",
    ):
        text = _read_optional_text(staged_path / name)
        if text:
            excerpts.append({"path": name, "text": text[:1800]})
    return excerpts


def _previous_readme_excerpt(text: str | None) -> str:
    if not text:
        return ""
    cleaned = _remove_managed_note(text)
    lines = [line.rstrip() for line in cleaned.splitlines()]
    return "\n".join(lines[:80])[:4000]


def _previous_summary(text: str | None) -> str:
    if not text:
        return ""
    cleaned = _remove_managed_note(text)
    paragraph: list[str] = []
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if not line:
            if paragraph:
                break
            continue
        if line.startswith("#") or line.startswith("```"):
            continue
        if line.startswith("- ") or line.startswith("* "):
            continue
        paragraph.append(line.replace("`", ""))
    return _clean_text(" ".join(paragraph)).rstrip(".")


def _previous_section_items(text: str | None, section_names: tuple[str, ...]) -> list[str]:
    if not text:
        return []
    cleaned = _remove_managed_note(text)
    target_names = {name.lower() for name in section_names}
    in_section = False
    items: list[str] = []
    paragraph: list[str] = []
    for raw_line in cleaned.splitlines():
        raw = raw_line.rstrip()
        stripped = raw.strip()
        if stripped.startswith("## "):
            if in_section and paragraph:
                items.append(_clean_previous_item(" ".join(paragraph)))
                paragraph = []
            name = stripped.lstrip("#").strip().lower()
            in_section = name in target_names
            continue
        if not in_section:
            continue
        if stripped.startswith("### "):
            continue
        if not stripped:
            if paragraph:
                items.append(_clean_previous_item(" ".join(paragraph)))
                paragraph = []
            continue
        if raw.startswith(("  - ", "  * ", "\t- ", "\t* ")):
            continue
        if raw.startswith(("- ", "* ")):
            if paragraph:
                items.append(_clean_previous_item(" ".join(paragraph)))
                paragraph = []
            items.append(_clean_previous_item(raw[2:]))
            continue
        if stripped.startswith("```"):
            continue
        paragraph.append(stripped)
    if in_section and paragraph:
        items.append(_clean_previous_item(" ".join(paragraph)))
    return [item for item in items if _is_useful_previous_item(item)][:6]


def _clean_previous_item(value: str) -> str:
    return _clean_text(value.replace("`", "")).rstrip(".")


def _is_useful_previous_item(value: str) -> bool:
    if not value or value.endswith(":"):
        return False
    return len(value.split()) >= 3


def _remove_managed_note(text: str) -> str:
    if README_START not in text or README_END not in text:
        return text
    before, rest = text.split(README_START, 1)
    _, after = rest.split(README_END, 1)
    return before.rstrip() + "\n\n" + after.lstrip()


def _contents(staged_path: Path) -> list[str]:
    try:
        children = sorted(staged_path.iterdir(), key=lambda path: (not path.is_dir(), path.name.lower()))
    except OSError:
        return []
    names: list[str] = []
    visible_children = [child for child in children if not _skip_top_level_item(child)]
    manifest_names = {"pyproject.toml", "package.json", "Cargo.toml", "go.mod", "Package.swift", "requirements.txt", "module.yaml"}
    priority = [child for child in visible_children if child.is_dir() or child.name in DESCRIPTION_BY_FILE or child.name in manifest_names]
    rest = [child for child in visible_children if child not in priority]
    for child in priority + rest:
        suffix = "/" if child.is_dir() else ""
        names.append(f"{child.name}{suffix}")
    return names[:12]


def _skip_top_level_item(child: Path) -> bool:
    if child.name in {"README.md", PREVIOUS_README_NAME}:
        return True
    if child.suffix.lower() in SKIPPED_CONTENT_EXTENSIONS:
        return True
    if child.name.startswith(".") and child.name not in {".github", ".gitignore"}:
        return True
    if child.is_file() and child.name.startswith("test_") and child.suffix == ".sh":
        return True
    return False


def _is_hidden_or_archive(relative: Path) -> bool:
    if relative.name in {"README.md", PREVIOUS_README_NAME}:
        return True
    return any(part.startswith(".") and part not in {".github", ".gitignore"} for part in relative.parts)


def _read_json(path: Path) -> dict[str, Any] | None:
    text = _read_optional_text(path)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _read_toml(path: Path) -> dict[str, Any] | None:
    if tomllib is None:
        return _read_simple_toml(path)
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _read_simple_toml(path: Path) -> dict[str, Any] | None:
    text = _read_optional_text(path)
    if text is None:
        return None
    data: dict[str, Any] = {}
    section: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = [part.strip() for part in line.strip("[]").split(".") if part.strip()]
            target = data
            for part in section:
                target = target.setdefault(part, {})
            continue
        if "=" not in line:
            continue
        key, raw_value = [part.strip() for part in line.split("=", 1)]
        value = raw_value.strip().strip("\"'")
        target = data
        for part in section:
            target = target.setdefault(part, {})
        target[key] = value
    return data


def _read_optional_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _first_match(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.MULTILINE)
    if not match:
        return None
    value = match.group(1).strip()
    return value.strip("\"'") or None


def _clean_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = re.sub(r"\s+", " ", value).strip()
    return value[:500]


def _clean_heading(value: str) -> str:
    return value.replace("\n", " ").strip("# ").strip() or "Project"


def _clean_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned = [_clean_text(item).rstrip(".") for item in value]
    return [item for item in cleaned if item][:8]


def _clean_commands(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    commands: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        label = _clean_text(item.get("label"))
        command = _clean_text(item.get("command"))
        if label and command:
            commands.append({"label": label.rstrip("."), "command": command})
    return _dedupe_commands(commands)[:6]


def _clean_map(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    mapped: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        path = _clean_text(item.get("path")).strip("`")
        description = _clean_text(item.get("description"))
        if path and description:
            mapped.append({"path": path, "description": description.rstrip(".")})
    return mapped[:12]


def _dedupe_commands(commands: list[dict[str, str]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for command in commands:
        key = command["command"]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(command)
    return deduped


def _title(slug: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9 _.-]+", " ", slug)
    words = [word for word in clean.replace("_", "-").split("-") if word]
    return " ".join(word[:1].upper() + word[1:] for word in words) or "Project"
