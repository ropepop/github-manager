from __future__ import annotations

import argparse
from pathlib import Path

from .classifier import classify_project
from .github import GitHubClient
from .runner import RunOptions, run_manager
from .scanner import scan_projects


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="github-manager",
        description="Find, sanitize, publish, and sync local coding projects with GitHub.",
    )
    parser.add_argument(
        "--documents-root",
        default=str(Path.home() / "Documents"),
        help="Folder to scan. Defaults to ~/Documents.",
    )
    parser.add_argument(
        "--workspace",
        default=".github-manager",
        help="Folder for staging copies and run reports. Defaults to ./.github-manager.",
    )
    parser.add_argument("--owner", default="ropepop", help="GitHub owner/account to manage. Defaults to ropepop.")

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("scan", help="List detected coding projects and publication status.")
    run_parser = subparsers.add_parser("run", help="Prepare projects, publish approved new repos, and sync existing repos.")
    run_parser.add_argument("--dry-run", action="store_true", help="Prepare and report, but do not publish or push.")
    run_parser.add_argument("--no-sync", action="store_true", help="Do not sync already-published repositories.")
    run_parser.add_argument(
        "--drop-blocked-files",
        action="store_true",
        help="Remove files that trigger strict checks from the staged copy, then check again before publishing.",
    )
    run_parser.add_argument(
        "--sanitized-counterparts",
        action="store_true",
        help="Prefer public sanitized counterpart repos such as public-ops, and include matching nested projects.",
    )
    run_parser.add_argument(
        "--public-only",
        action="store_true",
        help="Skip private repositories and only update public sanitized repositories.",
    )
    run_parser.add_argument(
        "--no-readme-refresh",
        action="store_true",
        help="Do not create or update README notes in staged sanitized copies.",
    )
    run_parser.add_argument(
        "--approve-new",
        action="append",
        default=[],
        metavar="SLUG",
        help="Approve first publication for a project slug. Can be repeated.",
    )
    run_parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Never prompt for publication approval.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    documents_root = Path(args.documents_root).expanduser().resolve()
    workspace = Path(args.workspace).expanduser().resolve()

    if args.command == "scan":
        return _scan(documents_root, workspace, args.owner)
    if args.command == "run":
        options = RunOptions(
            documents_root=documents_root,
            workspace=workspace,
            owner=args.owner,
            dry_run=args.dry_run,
            no_sync=args.no_sync,
            drop_blocked_files=args.drop_blocked_files,
            sanitized_counterparts=args.sanitized_counterparts,
            public_only=args.public_only,
            refresh_readme=not args.no_readme_refresh,
            approve_new=set(args.approve_new),
            interactive=not args.non_interactive,
        )
        results, report_path = run_manager(options)
        _print_results(results)
        print(f"\nReport written to: {report_path}")
        return 0
    parser.error("Unknown command.")
    return 2


def _scan(documents_root: Path, workspace: Path, owner: str) -> int:
    client = GitHubClient(owner)
    repos = client.list_repos()
    candidates = scan_projects(documents_root, excluded_roots=[workspace])
    for candidate in candidates:
        classification = classify_project(candidate, repos, owner)
        repo = f" -> {classification.repo.url}" if classification.repo else ""
        print(f"{candidate.slug}: {classification.status} ({classification.match_method}){repo}")
        print(f"  {candidate.path}")
    print(f"\nDetected {len(candidates)} project(s).")
    return 0


def _print_results(results) -> None:
    for result in results:
        print(f"{result.candidate.slug}: {result.action}")
        print(f"  {result.detail}")
        if result.prepared and result.prepared.findings:
            print(f"  Blocked findings: {len(result.prepared.findings)}")
        if result.prepared and result.prepared.readme_result:
            readme_result = result.prepared.readme_result
            label = readme_result.mode.replace("-", " ")
            if readme_result.model:
                label = f"{label} ({readme_result.model})"
            print(f"  README: {label}")


if __name__ == "__main__":
    raise SystemExit(main())
