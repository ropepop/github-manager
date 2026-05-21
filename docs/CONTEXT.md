# GitHub Manager Context Guide

Use this file to keep GitHub Manager work focused on source docs instead of generated state.

## Always-read spine

- `../README.md`: product purpose, workflow, and safety policy.
- This file: context routing and generated-state boundaries.

## Product source context

Read source code and docs from the project root first. The tool's generated workspace is intentionally not part of normal context.

Useful source areas:

- `github_manager/`: implementation.
- `tests/`: behavior and safety checks when present.
- `README.md`: public workflow and supported commands.

## Generated state to ignore by default

Do not recurse into these unless the task specifically asks about a generated run, staged copy, or publishing artifact:

- `.github-manager/staging/`
- `.github-manager/remote-clones/`
- `.github-manager/reports/`
- `.github-manager/chat-handoff/`

These folders duplicate other projects and can dominate agent context with stale or generated Markdown.

## Placement rules for new Markdown

- Durable product docs: `docs/`.
- User-facing overview/commands: `README.md`.
- Generated run output: `.github-manager/reports/YYYY-MM/` or machine-readable JSON plus a short Markdown summary.
- Draft public copy docs from chat handoff stay under `.github-manager/chat-handoff/` until accepted into staged output.

If a lesson from a generated report becomes durable product behavior, promote the summary into `README.md` or `docs/` rather than leaving it only in `.github-manager/reports/`.
