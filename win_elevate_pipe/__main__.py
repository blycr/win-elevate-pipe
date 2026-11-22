"""CLI entrypoint for win-elevate-pipe."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from .elevate import daemon_status, run_elevated, serve_daemon, stop_daemon


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="win-elevate",
        description="Run elevated commands inline in the current Windows terminal (no popup window).",
    )
    sub = parser.add_subparsers(dest="subcmd")

    p_run = sub.add_parser("run", help="Run a command with Administrator privileges inline")
    p_run.add_argument("--cwd", default=None, help="Working directory")
    p_run.add_argument("--state-file", default=None, help="Daemon state file path")
    p_run.add_argument("cmd", nargs=argparse.REMAINDER, help="Command to execute")

    p_stop = sub.add_parser("stop", help="Stop the hidden elevated daemon")
    p_stop.add_argument("--state-file", default=None)

    p_status = sub.add_parser("status", help="Show daemon status")
    p_status.add_argument("--state-file", default=None)

    p_daemon = sub.add_parser("_daemon", help=argparse.SUPPRESS)
    p_daemon.add_argument("--state-file", default=None)

    args = parser.parse_args(argv)
    sf = Path(args.state_file) if getattr(args, "state_file", None) else None

    if args.subcmd == "_daemon":
        return serve_daemon(state_file=sf)
    if args.subcmd == "stop":
        stopped = stop_daemon(state_file=sf)
        print("stopped" if stopped else "not running")
        return 0
    if args.subcmd == "status":
        st = daemon_status(state_file=sf)
        if st:
            print(f"running pid={st[2]} port={st[0]}")
            return 0
        print("not running")
        return 1
    if args.subcmd == "run":
        cmd = list(args.cmd)
        if cmd and cmd[0] == "--":
            cmd = cmd[1:]
        if not cmd:
            p_run.error("command is required")
        return run_elevated(cmd, cwd=args.cwd, state_file=sf)

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
