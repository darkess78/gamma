from __future__ import annotations

import argparse
import json
import sys
import webbrowser

from .manager import ProcessManager


IMPROVEMENT_WORKER_SERVICE = "improvement-worker"
IMPROVEMENT_WORKER_PROCESS_NAME = "improvement_worker"
IMPROVEMENT_WORKER_MODULE = "gamma.improvement.worker"


def build_parser() -> argparse.ArgumentParser:
    """Build argument parser.
    
    Returns:
        argparse.ArgumentParser: Argument parser.
    """
    parser = argparse.ArgumentParser(description="Manage Gamma background services.")
    parser.add_argument("command", choices=["start", "stop", "restart", "status"])
    parser.add_argument(
        "service",
        choices=["dashboard", "shana", "audio-understanding", IMPROVEMENT_WORKER_SERVICE, "all"],
    )
    parser.add_argument("--open-browser", action="store_true", dest="open_browser")
    return parser


def run(argv: list[str] | None = None) -> int:
    """Run CLI.
    
    Args:
        argv: Command line args (default sys.argv[1:]).
    
    Returns:
        int: Exit code.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    manager = ProcessManager()
    services = ["dashboard", "shana", IMPROVEMENT_WORKER_SERVICE] if args.service == "all" else [args.service]

    results: dict[str, object] = {}
    for name in services:
        if name == IMPROVEMENT_WORKER_SERVICE:
            if args.command == "start":
                results[name] = manager.start_module(
                    IMPROVEMENT_WORKER_PROCESS_NAME,
                    IMPROVEMENT_WORKER_MODULE,
                    ["--idle-timeout-seconds", "300"],
                )
            elif args.command == "stop":
                results[name] = manager.stop_module(
                    IMPROVEMENT_WORKER_PROCESS_NAME,
                    IMPROVEMENT_WORKER_MODULE,
                )
            elif args.command == "restart":
                stopped = manager.stop_module(
                    IMPROVEMENT_WORKER_PROCESS_NAME,
                    IMPROVEMENT_WORKER_MODULE,
                )
                started = manager.start_module(
                    IMPROVEMENT_WORKER_PROCESS_NAME,
                    IMPROVEMENT_WORKER_MODULE,
                    ["--idle-timeout-seconds", "300"],
                )
                results[name] = {"ok": True, "detail": "restarted", "stop": stopped, "start": started}
            else:
                results[name] = manager.module_status(
                    IMPROVEMENT_WORKER_PROCESS_NAME,
                    IMPROVEMENT_WORKER_MODULE,
                )
            continue
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
