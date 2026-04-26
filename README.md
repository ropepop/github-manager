# GitHub Manager

GitHub Manager is a local, agent-guided tool for finding coding projects in `~/Documents`, preparing public-safe copies, and publishing or syncing them with GitHub.

It is designed to be repeatable:

- New projects are detected and prepared, but first publication requires approval.
- Already published projects are matched from local remotes or GitHub repository names.
- Clean updates to already published projects can be synced automatically.
- Original project folders are never edited during preparation.
- Each run writes a readable report under `.github-manager/reports`.

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

By default, new repositories are created as public repositories under the `ropepop` GitHub account. Existing repositories are treated as managed only when they belong to that account.

## Safety Rules

The strict checker blocks publishing and syncing when it finds likely secrets, local environment files, databases, private keys, production configs, archives, large files, local user paths, or unclear binary files.
