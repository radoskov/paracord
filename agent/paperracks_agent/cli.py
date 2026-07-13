"""Command-line interface for the single local agent (SPEC §32.5).

The agent owns a persistent config + state; the CLI is a minimal, fast interface (the web GUI
offers the same and more). Typical flow:

    paracord-agent enroll --token <enrollment-token> --server http://server:8000 --name laptop
    # owner approves in the server UI, copies the agent token:
    paracord-agent set-token <agent-token>
    paracord-agent add-folder ~/papers --monitor --action index_only
    paracord-agent start          # monitor + sync (Ctrl-C to stop; --daemon to detach)
"""

import argparse
import asyncio
from pathlib import Path

from paperracks_agent import agent_ops
from paperracks_agent.client import PaRacORDServerClient
from paperracks_agent.config import ManagedFile, ManagedFolder, load_config, save_config
from paperracks_agent.manifest import build_manifest_item
from paperracks_agent.secrets import resolve_token, set_secret
from paperracks_agent.state import AgentState
from paperracks_agent.watcher import iter_pdf_files


def _client_and_state(args: argparse.Namespace):
    config = load_config(getattr(args, "config", None))
    server = getattr(args, "server", None) or config.server_url
    client = PaRacORDServerClient(
        server,
        token=resolve_token(getattr(args, "token", None)),
        allow_insecure_http=config.allow_insecure_http,
        ca_cert=config.ca_cert,
    )
    state = AgentState(getattr(args, "state", None))
    return config, client, state


# --- async operations -------------------------------------------------------


async def _enroll(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    server = args.server or config.server_url
    name = args.name or config.name
    result = await PaRacORDServerClient(
        server, allow_insecure_http=config.allow_insecure_http, ca_cert=config.ca_cert
    ).enroll(args.token, name)
    config.server_url = server
    config.name = name
    config.agent_id = result["agent_id"]
    save_config(config, args.config)
    print(
        f"Enrolled as pending agent {result['agent_id']} ('{name}').\n"
        "Have the owner approve it (server UI: Admin -> Agents), then run:\n"
        "  paracord-agent set-token <agent-token>"
    )


async def _sync(args: argparse.Namespace) -> None:
    config, client, state = _client_and_state(args)
    summary = await agent_ops.sync(config, state, client)
    print(
        f"Sync: indexed {summary['indexed']}, actions {summary['actions_applied']}, "
        f"requests fulfilled {summary['requests_fulfilled']}, removed {summary['removed']}"
    )


async def _reconcile(args: argparse.Namespace) -> None:
    config, client, state = _client_and_state(args)
    # Delete-on-disk mirrors the GUI's two-dialog gating: --delete-on-disk (tick the box) is only
    # honoured together with --confirm-delete (the one-shot enable dialog), which arms it here.
    if args.delete_on_disk and args.confirm_delete:
        agent_ops.arm_delete_on_disk(state)
    result = await agent_ops.reconcile(
        config, state, client, delete_on_disk=args.delete_on_disk, apply=args.apply
    )
    if not args.apply:
        head = f"Dry run: {result['would_un_index']} to un-index"
        if args.delete_on_disk:
            head += f", {result['would_delete']} to delete on disk"
        print(head + ". Re-run with --apply to apply.")
        for c in result["un_index_candidates"]:
            print(f"  un-index {c['local_file_id'][:12]}  {c['virtual_path'] or c['real_path']}")
        if args.delete_on_disk:
            for c in result["delete_candidates"]:
                print(f"  delete   {c['real_path']}")
        if result.get("refused"):
            print(f"REFUSED: {result['reason']}")
        return
    if result.get("refused"):
        print(f"Refused: {result['reason']}")
        raise SystemExit(2)
    print(f"Reconciled: {result['un_indexed']} un-indexed, {result['deleted']} deleted on disk")


async def _status(args: argparse.Namespace) -> None:
    config, client, state = _client_and_state(args)
    print(f"Server: {client.server_url}")
    try:
        me = await client.get_me()
        granted = [
            key
            for key in (
                "can_index",
                "can_extract",
                "can_teleport",
                "can_be_requested",
                "processing_visibility",
                "server_status_visibility",
            )
            if me.get(key)
        ]
        print(f"Agent:  {me['name']} [{me['status']}] id={me['agent_id']}")
        print(f"Privileges: {', '.join(granted) or 'none'}")
    except Exception as exc:  # noqa: BLE001
        print(f"Not reachable / not approved: {exc}")
    print(
        f"Managed: {len(config.folders)} folder(s), {len(config.files)} file(s); "
        f"{len(state.all_files())} indexed locally"
    )


async def _teleport(args: argparse.Namespace) -> None:
    _config, client, state = _client_and_state(args)
    ok = await agent_ops.approve_request(state, client, args.local_file_id)
    print("teleported" if ok else "not found in local index")


async def _request(args: argparse.Namespace) -> None:
    _config, client, state = _client_and_state(args)
    if args.approve:
        await agent_ops.approve_request(state, client, args.approve)
        print(f"approved {args.approve}")
    elif args.reject:
        await agent_ops.reject_request(state, client, args.reject, forever=args.forever)
        print(f"rejected {args.reject}" + (" (blocked)" if args.forever else ""))
    elif args.unblock:
        await agent_ops.unblock(state, client, args.unblock)
        print(f"unblocked {args.unblock}")
    else:
        pending = await client.get_pending_teleports()
        if not pending:
            print("No pending teleport requests.")
        for entry in pending:
            print(f"{entry['local_file_id']}  {entry.get('display_path') or ''}")


async def _start(args: argparse.Namespace) -> None:
    config, client, state = _client_and_state(args)
    interval = args.interval or config.refresh_interval
    if not config.folders and not config.files:
        print("No managed folders/files. Add some with `add-folder` / `add-file`.")
        return
    print(f"Agent monitoring every {interval}s → {client.server_url}. Ctrl-C to stop.")
    while True:
        try:
            summary = await agent_ops.sync(config, state, client)
            print(f"  sync: indexed {summary['indexed']}, actions {summary['actions_applied']}")
        except Exception as exc:  # noqa: BLE001 - keep the daemon alive across transient errors
            print(f"  cycle error: {exc}")
        await asyncio.sleep(interval)
        config = load_config(getattr(args, "config", None))  # pick up config edits live


# --- sync (config-mutation) operations --------------------------------------


def _set_token(args: argparse.Namespace) -> None:
    set_secret("agent_token", args.token)
    print("Agent token stored.")


def _set_server(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    config.server_url = args.url
    save_config(config, args.config)
    print(f"Server URL set to {args.url}")


def _add_folder(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    config.folders.append(
        ManagedFolder(
            path=args.path,
            mode="once" if args.once else "monitored",
            action=args.action or config.default_action,
            teleport_policy=args.teleport or config.default_teleport_policy,
        )
    )
    save_config(config, args.config)
    print(f"Added folder {args.path}")


def _add_file(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    config.files.append(
        ManagedFile(
            path=args.path,
            action=args.action or config.default_action,
            teleport_policy=args.teleport or config.default_teleport_policy,
        )
    )
    save_config(config, args.config)
    print(f"Added file {args.path}")


def _remove(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    target = str(Path(args.path).expanduser())
    config.folders = [f for f in config.folders if str(f.path.expanduser()) != target]
    config.files = [f for f in config.files if str(f.path.expanduser()) != target]
    save_config(config, args.config)
    print(f"Removed {args.path}")


def _prune_unwatched(args: argparse.Namespace) -> None:
    config = load_config(getattr(args, "config", None))
    state = AgentState(getattr(args, "state", None))
    if args.dry_run:
        ids = agent_ops.unwatched_ids(config, state)
        print(f"{len(ids)} unwatched entr{'y' if len(ids) == 1 else 'ies'} would be pruned.")
        return
    pruned = agent_ops.prune_unwatched(config, state)
    print(
        f"Pruned {len(pruned)} unwatched entr{'y' if len(pruned) == 1 else 'ies'} from the index."
    )


def _forget(args: argparse.Namespace) -> None:
    state = AgentState(getattr(args, "state", None))
    removed = state.forget_many(args.local_file_ids)
    print(f"Forgot {removed} file(s) from the index (on-disk files untouched).")


def _list(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    print(f"Server: {config.server_url}  (agent_id={config.agent_id or 'not enrolled'})")
    print(f"Defaults: action={config.default_action}, teleport={config.default_teleport_policy}")
    for folder in config.folders:
        print(
            f"  [folder/{folder.mode}] {folder.path}  action={folder.action} teleport={folder.teleport_policy}"
        )
    for managed in config.files:
        print(
            f"  [file] {managed.path}  action={managed.action} teleport={managed.teleport_policy}"
        )
    if not config.folders and not config.files:
        print("  (no managed items yet)")


def _scan(args: argparse.Namespace) -> None:
    for path in iter_pdf_files(args.roots):
        item = build_manifest_item(path)
        print(f"{item.local_file_id} {item.size_bytes} {item.path}")


def _report_error(exc: Exception, server: str | None) -> None:
    message = str(exc) or exc.__class__.__name__
    print(f"Error: {message}")
    server = server or ""
    if ("SSL" in message or "WRONG_VERSION" in message) and server.startswith("https://"):
        print(
            f"Hint: the server speaks plain HTTP — use --server {server.replace('https://', 'http://', 1)}"
        )
    elif "Connect" in exc.__class__.__name__ or "refused" in message.lower():
        print(f"Hint: is the server running and reachable at {server or 'the given URL'}?")


# --- argument wiring --------------------------------------------------------


def _common(p: argparse.ArgumentParser, *, server: bool = False) -> None:
    p.add_argument("--config", type=Path, default=None, help="Agent config path")
    p.add_argument("--state", type=Path, default=None, help="Agent state DB path")
    if server:
        p.add_argument("--server", default=None, help="Server URL (overrides config)")
        p.add_argument("--token", default=None, help="Agent token (or $PARACORD_AGENT_TOKEN)")


def _item_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument("path", type=Path)
    p.add_argument(
        "--action", choices=["index_only", "index_and_extract", "teleport"], default=None
    )
    p.add_argument("--teleport", choices=["ask", "allow"], default=None)


def main() -> None:
    parser = argparse.ArgumentParser(prog="paracord-agent")
    sub = parser.add_subparsers(dest="command")

    scan = sub.add_parser("scan", help="List indexed PDFs (local only)")
    scan.add_argument("roots", nargs="*", type=Path)

    enroll = sub.add_parser("enroll", help="Enroll with an owner-issued token")
    enroll.add_argument("--config", type=Path, default=None)
    enroll.add_argument("--server", default=None)
    enroll.add_argument("--token", required=True, help="Enrollment token from the server UI")
    enroll.add_argument("--name", default=None)

    set_token = sub.add_parser("set-token", help="Store the approved agent token")
    set_token.add_argument("token")

    set_server = sub.add_parser("set-server", help="Set the server URL")
    set_server.add_argument("url")
    set_server.add_argument("--config", type=Path, default=None)

    add_folder = sub.add_parser("add-folder", help="Manage a folder")
    add_folder.add_argument("--config", type=Path, default=None)
    add_folder.add_argument("--monitor", action="store_true", help="Continuously monitor (default)")
    add_folder.add_argument("--once", action="store_true", help="One-time import (no watch)")
    _item_opts(add_folder)

    add_file = sub.add_parser("add-file", help="Manage a single file")
    add_file.add_argument("--config", type=Path, default=None)
    _item_opts(add_file)

    remove = sub.add_parser("remove", help="Stop managing a folder/file")
    remove.add_argument("path", type=Path)
    remove.add_argument("--config", type=Path, default=None)

    lst = sub.add_parser("list", help="List managed folders/files")
    lst.add_argument("--config", type=Path, default=None)

    _common(sub.add_parser("sync", help="Scan + send manifest + apply actions once"), server=True)
    _common(
        sub.add_parser("status", help="Show connection, approval, privileges, counts"), server=True
    )
    _common(sub.add_parser("refresh", help="Force an off-schedule sync"), server=True)

    reconcile = sub.add_parser(
        "reconcile", help="Reverse sync: un-index files the server no longer has (preview first)"
    )
    _common(reconcile, server=True)
    reconcile.add_argument(
        "--apply", action="store_true", help="Apply (default is a dry-run preview)"
    )
    reconcile.add_argument(
        "--delete-on-disk",
        action="store_true",
        help="Also delete eligible files from disk (guarded; moves to a recoverable trash dir)",
    )
    reconcile.add_argument(
        "--confirm-delete",
        action="store_true",
        help="Required with --delete-on-disk to arm the one-shot guarded delete",
    )

    prune = sub.add_parser(
        "prune-unwatched", help="Remove unwatched entries from the index (local)"
    )
    prune.add_argument("--config", type=Path, default=None)
    prune.add_argument("--state", type=Path, default=None)
    prune.add_argument(
        "--dry-run", action="store_true", help="List what would be pruned; change nothing"
    )

    forget = sub.add_parser(
        "forget", help="Forget one or more indexed files (on-disk files untouched)"
    )
    forget.add_argument("local_file_ids", nargs="+", help="local_file_id(s) to forget")
    forget.add_argument("--state", type=Path, default=None)

    teleport = sub.add_parser("teleport", help="Push one indexed file now")
    _common(teleport, server=True)
    teleport.add_argument("local_file_id")

    req = sub.add_parser("request", help="Manage incoming teleport requests")
    _common(req, server=True)
    req.add_argument("--list", action="store_true")
    req.add_argument("--approve", metavar="ID", default=None)
    req.add_argument("--reject", metavar="ID", default=None)
    req.add_argument(
        "--forever", action="store_true", help="Block all future requests for the file"
    )
    req.add_argument("--unblock", metavar="ID", default=None)

    start = sub.add_parser("start", help="Run continuously: monitor + sync")
    _common(start, server=True)
    start.add_argument("--interval", type=int, default=None)
    start.add_argument(
        "--daemon", action="store_true", help="(reserved) detach; use systemd on Linux"
    )

    web = sub.add_parser("web", help="Local web GUI (up/down/status)")
    web_sub = web.add_subparsers(dest="web_command")
    web_up_p = web_sub.add_parser("up", help="Start the local web GUI (prints URL + token)")
    web_up_p.add_argument("--config", type=Path, default=None)
    web_up_p.add_argument("--state", type=Path, default=None)
    web_up_p.add_argument("--port", type=int, default=None, help="Override the configured web port")
    web_sub.add_parser("down", help="Stop the local web GUI")
    web_sub.add_parser("status", help="Report whether the web GUI is running")

    args = parser.parse_args()

    sync_handlers = {
        "set-token": _set_token,
        "set-server": _set_server,
        "add-folder": _add_folder,
        "add-file": _add_file,
        "remove": _remove,
        "list": _list,
        "scan": _scan,
        "prune-unwatched": _prune_unwatched,
        "forget": _forget,
    }
    async_handlers = {
        "enroll": _enroll,
        "sync": _sync,
        "status": _status,
        "refresh": _sync,
        "reconcile": _reconcile,
        "teleport": _teleport,
        "request": _request,
        "start": _start,
    }

    if args.command == "web":
        from paperracks_agent import web_server

        web_handlers = {
            "up": web_server.web_up,
            "down": web_server.web_down,
            "status": web_server.web_status,
        }
        handler = web_handlers.get(getattr(args, "web_command", None))
        if handler is None:
            web.print_help()
            return
        handler(args)
        return

    if args.command in sync_handlers:
        sync_handlers[args.command](args)
        return
    if args.command in async_handlers:
        try:
            asyncio.run(async_handlers[args.command](args))
        except KeyboardInterrupt:
            print("\nstopped")
        except Exception as exc:  # noqa: BLE001 - show connection errors cleanly
            _report_error(
                exc,
                getattr(args, "server", None)
                or load_config(getattr(args, "config", None)).server_url,
            )
            raise SystemExit(1) from None
        return
    parser.print_help()


if __name__ == "__main__":
    main()
