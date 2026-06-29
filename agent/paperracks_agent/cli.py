"""Command-line interface for the local agent.

Typical use on a workstation:

    pip install -e .                       # provides the `paracord-agent` command
    export PARACORD_AGENT_TOKEN=<token>    # from the server's Admin -> Agents approval
    paracord-agent serve --config ~/.config/paracord/agent.yaml

`serve` indexes the configured folders, keeps the server manifest fresh, and automatically
fulfils any teleports requested from the server UI — so day-to-day you drive everything from
the server (Admin -> Agents), and the agent does the pushing. `scan`/`sync`/`teleport` are the
one-shot equivalents. Folders to index come from the config file's `filesystem.allowed_roots`
(or positional args), which is how you add/remove folders.
"""

import argparse
import asyncio
from pathlib import Path

from paperracks_agent.client import PaRacORDServerClient
from paperracks_agent.config import AgentConfig, load_agent_config, resolve_token
from paperracks_agent.index import AgentIndex
from paperracks_agent.manifest import build_manifest_item
from paperracks_agent.teleport import open_file_for_teleport
from paperracks_agent.watcher import iter_pdf_files


def _resolve(args: argparse.Namespace) -> tuple[str, list[Path], str | None, int]:
    config = load_agent_config(args.config) if getattr(args, "config", None) else AgentConfig()
    server = args.server or config.server_url
    roots = list(args.roots) if args.roots else list(config.allowed_roots)
    token = resolve_token(args.token, config)
    interval = getattr(args, "interval", None) or config.poll_interval_seconds
    return server, roots, token, interval


async def _fulfil_pending(client: PaRacORDServerClient, index: AgentIndex) -> int:
    pending = await client.get_pending_teleports()
    count = 0
    for entry in pending:
        local_file_id = entry["local_file_id"]
        try:
            handle = open_file_for_teleport(index, local_file_id)
        except KeyError:
            print(f"skip {local_file_id}: not in local index")
            continue
        with handle:
            await client.upload_teleport_content(local_file_id, handle)
        print(f"teleported {local_file_id}")
        count += 1
    return count


async def _enroll(args: argparse.Namespace) -> None:
    config = load_agent_config(args.config) if getattr(args, "config", None) else AgentConfig()
    server = args.server or config.server_url
    name = args.name or config.name
    result = await PaRacORDServerClient(server).enroll(args.token, name)
    print(
        f"Enrolled as pending agent {result['agent_id']} ('{name}').\n"
        "Ask the owner to approve it in the server UI (Admin -> Agents), then set the returned\n"
        "token as PARACORD_AGENT_TOKEN and run `paracord-agent serve`."
    )


async def _sync(args: argparse.Namespace) -> None:
    server, roots, token, _ = _resolve(args)
    index = AgentIndex(roots).scan()
    await PaRacORDServerClient(server, token=token).send_manifest(index.manifest_payload())
    print(f"Sent manifest with {len(index.items())} file(s) from {len(roots)} root(s)")


async def _teleport(args: argparse.Namespace) -> None:
    server, roots, token, _ = _resolve(args)
    index = AgentIndex(roots).scan()
    client = PaRacORDServerClient(server, token=token)
    count = await _fulfil_pending(client, index)
    print("No teleports requested." if count == 0 else f"Teleported {count} file(s).")


async def _serve(args: argparse.Namespace) -> None:
    server, roots, token, interval = _resolve(args)
    if not roots:
        print("No folders configured. Pass roots or set filesystem.allowed_roots in --config.")
        return
    client = PaRacORDServerClient(server, token=token)
    print(f"Agent serving {len(roots)} folder(s) to {server} every {interval}s. Ctrl-C to stop.")
    while True:
        try:
            index = AgentIndex(roots).scan()
            await client.send_manifest(index.manifest_payload())
            await _fulfil_pending(client, index)
        except Exception as exc:  # noqa: BLE001 - keep the daemon alive across transient errors
            print(f"cycle error: {exc}")
        await asyncio.sleep(interval)


def _add_common(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("--config", type=Path, default=None, help="Path to agent YAML config")
    subparser.add_argument("--server", default=None, help="Server base URL (overrides config)")
    subparser.add_argument("--token", default=None, help="Agent token (or $PARACORD_AGENT_TOKEN)")
    subparser.add_argument("roots", nargs="*", type=Path, help="Folders to index (override config)")


def main() -> None:
    parser = argparse.ArgumentParser(prog="paracord-agent")
    subparsers = parser.add_subparsers(dest="command")

    scan_parser = subparsers.add_parser("scan", help="List indexed PDFs (local only)")
    scan_parser.add_argument("roots", nargs="*", type=Path)

    enroll_parser = subparsers.add_parser("enroll", help="Enroll with an owner-issued token")
    enroll_parser.add_argument(
        "--config", type=Path, default=None, help="Path to agent YAML config"
    )
    enroll_parser.add_argument("--server", default=None, help="Server base URL")
    enroll_parser.add_argument("--token", required=True, help="Enrollment token from the server UI")
    enroll_parser.add_argument("--name", default=None, help="A name for this agent")

    _add_common(subparsers.add_parser("sync", help="Send the manifest to the server once"))
    _add_common(subparsers.add_parser("teleport", help="Upload any files the server requested"))
    serve_parser = subparsers.add_parser("serve", help="Run continuously: sync + auto-teleport")
    _add_common(serve_parser)
    serve_parser.add_argument("--interval", type=int, default=None, help="Seconds between cycles")

    args = parser.parse_args()
    if args.command == "scan":
        for path in iter_pdf_files(args.roots):
            item = build_manifest_item(path)
            print(f"{item.local_file_id} {item.size_bytes} {item.path}")
    elif args.command == "enroll":
        asyncio.run(_enroll(args))
    elif args.command == "sync":
        asyncio.run(_sync(args))
    elif args.command == "teleport":
        asyncio.run(_teleport(args))
    elif args.command == "serve":
        try:
            asyncio.run(_serve(args))
        except KeyboardInterrupt:
            print("\nstopped")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
