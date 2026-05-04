# GitHub Manager

GitHub Manager is a local, agent-guided tool for finding coding projects in `~/Documents`, preparing public-safe copies, and publishing or syncing them with GitHub.

It is designed to be repeatable:

- New projects are detected and prepared, but first publication requires approval.
- Already published projects are matched from local remotes or GitHub repository names.
- Clean updates to already published projects can be synced automatically.
- Existing private repositories are committed and pushed directly from their local folders before public sanitized copies are prepared.
- If a local folder points at a same-account private repository that is missing or not yet confirmed locally, the manager can create or confirm that private repository before pushing.
- Original project folders are never edited during preparation.
- Each run writes a readable report under `.github-manager/reports`.

## Public Publishing Policy

GitHub Manager keeps local source folders and public repositories separate:

- A native Git remote is the source folder's own GitHub connection. The manager may read it to understand where a folder belongs, but it does not remove or replace that link.
- A public project is a cleaned copy produced by GitHub Manager. It is staged under `.github-manager/staging`, checked for unsafe files, and then synced to GitHub.
- Private source repositories are updated first during public-only sanitized runs, then skipped for public publishing unless a public sanitized counterpart is selected.
- During public-only runs, a native Git remote is not enough by itself: the manager must confirm a public repository or select a public sanitized counterpart.

The current public outputs managed by this project are:

- `cases-parser`
- `public-codex-json-manager`
- `github-manager`
- `public-ops`
- `pixel-phone`
- `task-executor`

For public-only publishing, `links`, `jolkins.id.lv`, and `ticket-remote` are treated as private-only, and `Qwen3-TTS-Mac-GeneLab` is treated as external and unmanaged.

## Quick Start

```bash
python3 -m github_manager scan
python3 -m github_manager run --dry-run --non-interactive
```

To approve first publication of a clean unpublished project:

```bash
python3 -m github_manager run --approve-new pixel-phone
```

To publish a sanitized copy that drops files flagged by the safety checker:

```bash
python3 -m github_manager run --drop-blocked-files --no-sync --approve-new pixel-phone
```

To update public sanitized counterpart repositories, such as `public-ops` or nested project repos like `task-executor`:

```bash
python3 -m github_manager run --drop-blocked-files --sanitized-counterparts --public-only
```

That full-cycle command now updates matching private source repositories first, then prepares and syncs scrubbed public copies. Use `--no-private-first` only when you explicitly want to skip the private-source update pass.

Private repositories that already exist under the managed GitHub account are handled differently from public sanitized repositories: `run` commits local changes in the original folder and pushes the current branch. Use `--dry-run` to preview those commits or `--no-sync` to disable them.

Each prepared update refreshes `README.md` by default. Existing READMEs are preserved with a managed public-copy note, and missing READMEs are generated from the sanitized repository contents.

By default, new repositories are created as public repositories under the `ropepop` GitHub account. Existing repositories are treated as managed only when they belong to that account.

## Safety Rules

The strict checker blocks publishing and syncing when it finds likely secrets, local environment files, databases, private keys, production configs, archives, large files, local user paths, or unclear binary files.
