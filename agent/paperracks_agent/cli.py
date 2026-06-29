"""Command-line interface for the local agent."""

import argparse
import asyncio
import os
from pathlib import Path

from paperracks_agent.client import PaRacORDServerClient
from paperracks_agent.index import AgentIndex
from paperracks_agent.manifest import build_manifest_item
from paperracks_agent.teleport import open_file_for_teleport
from paperracks_agent.watcher import iter_pdf_files


def _client(args: argparse.Namespace) -> PaRacORDServerClient:
    token = args.token or os.environ.get("PARACORD_AGENT_TOKEN")
    return PaRacORDServerClient(args.server, token=token)


async def _sync(args: argparse.Namespace) -> None:
    index = AgentIndex(args.roots).scan()
    await _client(args).send_manifest(index.manifest_payload())
    print(f"Sent manifest with {len(index.items())} file(s)")


async def _teleport(args: argparse.Namespace) -> None:
    index = AgentIndex(args.roots).scan()
    client = _client(args)
    pending = await client.get_pending_teleports()
    if not pending:
        print("No teleports requested.")
        return
    for entry in pending:
        local_file_id = entry["local_file_id"]
        try:
            handle = open_file_for_teleport(index, local_file_id)
        except KeyError:
            print(f"skip {local_file_id}: not in local index")
            continue
        with handle:
            result = await client.upload_teleport_content(local_file_id, handle)
        print(f"teleported {local_file_id} -> file {result.get('file_id')}")


def _add_server_args(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("--server", default="http://127.0.0.1:8000", help="Server base URL")
    subparser.add_argument("--token", default=None, help="Agent token (or $PARACORD_AGENT_TOKEN)")
    subparser.add_argument("roots", nargs="*", type=Path, help="Allowed root folders to index")


def main() -> None:
    parser = argparse.ArgumentParser(prog="paracord-agent")
    subparsers = parser.add_subparsers(dest="command")

    scan_parser = subparsers.add_parser("scan", help="Scan configured folders")
    scan_parser.add_argument("roots", nargs="*", type=Path)

    subparsers.add_parser("register", help="Register this agent with a server")
    _add_server_args(subparsers.add_parser("sync", help="Send latest manifest to server"))
    _add_server_args(
        subparsers.add_parser("teleport", help="Upload any files the server requested")
    )

    args = parser.parse_args()
    if args.command == "scan":
        for path in iter_pdf_files(args.roots):
            item = build_manifest_item(path)
            print(f"{item.local_file_id} {item.size_bytes} {item.path}")
    elif args.command == "sync":
        asyncio.run(_sync(args))
    elif args.command == "teleport":
        asyncio.run(_teleport(args))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
