"""Command-line interface for the local agent."""

import argparse
from pathlib import Path

from paperracks_agent.manifest import build_manifest_item
from paperracks_agent.watcher import iter_pdf_files


def main() -> None:
    parser = argparse.ArgumentParser(prog="paperracks-agent")
    subparsers = parser.add_subparsers(dest="command")

    scan_parser = subparsers.add_parser("scan", help="Scan configured folders")
    scan_parser.add_argument("roots", nargs="*", type=Path)

    subparsers.add_parser("register", help="Register this agent with a server")
    subparsers.add_parser("sync", help="Send latest manifest to server")
    subparsers.add_parser("serve", help="Run agent service loop")

    args = parser.parse_args()
    if args.command == "scan":
        for path in iter_pdf_files(args.roots):
            item = build_manifest_item(path)
            print(f"{item.local_file_id} {item.size_bytes} {item.path}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
