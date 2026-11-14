"""Reset the dashboard login. See gamma.dashboard.credentials for details."""
from __future__ import annotations

import argparse
import getpass
import subprocess
import sys

from ..config import app_local_config_path, settings
from .credentials import MINIMUM_PASSWORD_LENGTH, reset_credentials


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Set a new dashboard username and password.")
    parser.add_argument("--username", help=f"login name (default: current, {settings.dashboard_auth_username or 'none set'})")
    parser.add_argument("--restart", action="store_true", help="restart gamma-dashboard.service afterwards")
    args = parser.parse_args(argv)

    username = args.username or settings.dashboard_auth_username or input("Username: ").strip()
    password = getpass.getpass(f"New password (at least {MINIMUM_PASSWORD_LENGTH} characters): ")
    if password != getpass.getpass("Repeat new password: "):
        print("Passwords did not match; nothing changed.", file=sys.stderr)
        return 1
    path = app_local_config_path()
    try:
        reset_credentials(path, username=username, password=password)
    except ValueError as exc:
        print(f"{exc}; nothing changed.", file=sys.stderr)
        return 1
    print(f"Saved a hashed password for {username!r} in {path} and signed out all dashboard sessions.")
    if args.restart:
        result = subprocess.run(["systemctl", "--user", "restart", "gamma-dashboard.service"], check=False)
        print("Dashboard restarted." if result.returncode == 0 else "Restart failed; run: systemctl --user restart gamma-dashboard")
        return result.returncode
    print("Restart the dashboard to use it: systemctl --user restart gamma-dashboard")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
