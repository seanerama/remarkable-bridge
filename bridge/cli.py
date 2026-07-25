"""``remarkable-bridge`` console entry point — a thin subcommand dispatcher.

Subcommands:

* ``push`` — deliver a file (or markdown → PDF) to a tablet folder via the cloud
  (:mod:`bridge.push`).
* *(no subcommand)* — run the review-route watcher daemon (:func:`bridge.watcher.main`),
  preserving the pre-Stage-7 entry-point behavior.

Kept deliberately thin so future subcommands slot in without touching the command logic.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .push import DEFAULT_FOLDER, PushError, push


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="remarkable-bridge")
    sub = parser.add_subparsers(dest="command")

    p_push = sub.add_parser(
        "push", help="push a file (or markdown → PDF) to a tablet folder via the cloud"
    )
    p_push.add_argument(
        "file", nargs="?", help="an existing file to upload (e.g. a PDF)"
    )
    p_push.add_argument(
        "--md", metavar="FILE.md", help="render this markdown file to PDF, then upload"
    )
    p_push.add_argument(
        "--folder", default=DEFAULT_FOLDER, help=f"tablet folder (default {DEFAULT_FOLDER})"
    )
    p_push.add_argument(
        "--title", help="tablet document name (default: the input filename)"
    )
    return parser


def _cmd_push(args: argparse.Namespace) -> int:
    if bool(args.file) == bool(args.md):
        print(
            "push: provide exactly one of <file> or --md <file.md>", file=sys.stderr
        )
        return 2

    source = Path(args.md if args.md else args.file)
    try:
        result = push(
            source,
            folder=args.folder,
            is_markdown=bool(args.md),
            title=args.title,
        )
    except PushError as exc:
        print(f"push failed: {exc}", file=sys.stderr)
        return 1

    print(f"pushed: {result.visible_name} -> {result.folder}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "push":
        return _cmd_push(args)

    # No subcommand: run the watcher daemon (pre-Stage-7 behavior).
    from .watcher import main as watcher_main

    watcher_main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
