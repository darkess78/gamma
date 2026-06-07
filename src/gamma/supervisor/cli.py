from __future__ import annotations

import argparse
import json
import sys
import webbrowser

from .manager import ProcessManager


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage Gamma background services.")
    parser.add_argument("command", choices=["start", "stop", "restart", "status"])
    parser.add_argument("service", choices=["dashboard", "shana", "all"])
    parser.add_argument("--open-browser", action="store_true", dest="open_browser")
    return parser


def run(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    manager = ProcessManager()
    services = ["dashboard", "shana"] if args.service == "all" else [args.service]

    results: dict[str, object] = {}
    for name in services:
        if args.command == "start":
            results[name] = manager.start(name)
        elif args.command == "stop":
            results[name] = manager.stop(name)
        elif args.command == "restart":
            results[name] = manager.restart(name)
        else:
            results[name] = manager.status(name)

    if args.open_browser and args.command in {"start", "restart"} and "dashboard" in services:
        webbrowser.open(manager.service("dashboard").url)

    json.dump(results, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
