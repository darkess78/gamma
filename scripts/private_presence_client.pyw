"""Open only the isolated presentation in an existing Chromium app window.

This client starts no Shana service and captures no microphone by itself.
Use an authenticated isolated review URL provided by the finite review controller.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit


PROJECT = Path(__file__).resolve().parents[1]


def validate_url(value: str) -> str:
    url = urlsplit(value)
    if (url.scheme != 'http' or url.hostname not in {'127.0.0.1', '::1'}
        or not url.port or not 1024 <= url.port <= 65535
        or url.port in {8000, 8001, 8080, 11434}
        or url.username or url.password or url.query or url.fragment
        or url.path not in {'', '/dashboard/presence'}):
        raise ValueError('provide a separate isolated localhost review URL')
    host = '[::1]' if url.hostname == '::1' else '127.0.0.1'
    return f'http://{host}:{url.port}/dashboard/presence'


def find_browser(name: str = 'brave') -> Path:
    names = {
        'brave': ('BraveSoftware/Brave-Browser/Application/brave.exe',),
        'edge': ('Microsoft/Edge/Application/msedge.exe',),
        'chrome': ('Google/Chrome/Application/chrome.exe',),
    }
    if name not in names:
        raise ValueError('select brave, edge or chrome')
    for variable in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA'):
        root = os.environ.get(variable)
        if root:
            for relative in names[name]:
                candidate = Path(root) / relative
                if candidate.is_file():
                    return candidate.resolve()
    raise FileNotFoundError(f'{name} app-window browser was not found')


def build_command(url: str, browser: Path, position: tuple[int, int] = (100, 100),
                  size: tuple[int, int] = (640, 780)) -> list[str]:
    url = validate_url(url)
    if (not browser.is_file() or browser.suffix.lower() != '.exe'
        or browser.name.lower() not in {'brave.exe', 'msedge.exe', 'chrome.exe'}):
        raise ValueError('select an installed supported browser executable')
    if (len(position) != 2 or any(type(n) is not int or not -32768 <= n <= 32767 for n in position)
        or len(size) != 2 or not 320 <= size[0] <= 3840 or not 240 <= size[1] <= 2160):
        raise ValueError('invalid window bounds')
    return [str(browser), f'--app={url}', f'--window-position={position[0]},{position[1]}',
            f'--window-size={size[0]},{size[1]}', '--no-first-run', '--no-default-browser-check']


def launch(url: str, browser: Path, position=(100, 100), size=(640, 780), *,
           log_dir: Path | None = None, popen=subprocess.Popen) -> dict:
    if sys.platform != 'win32':
        raise RuntimeError('this presentation launcher runs on Windows')
    command = build_command(url, browser, position, size)
    logs_root = (PROJECT / 'data' / 'coordination' / 'p5' / 'client').resolve()
    folder = (log_dir or logs_root / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')).resolve()
    if not folder.is_relative_to(logs_root):
        raise ValueError('client logs must remain in isolated P5 data')
    folder.mkdir(parents=True, exist_ok=False)
    with (folder / 'browser.log').open('ab') as output:
        process = popen(command, cwd=PROJECT, stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                        creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)
    receipt = {'started_utc': datetime.now(timezone.utc).isoformat(), 'browser_pid': process.pid,
               'browser': str(browser), 'url': validate_url(url), 'command': command,
               'console_hidden': True, 'microphone_auto_enabled': False,
               'starts_services': False, 'activation': False,
               'limits': ['Existing browser process may own the visible app window.',
                          'Window visibility and audible delivery require direct observation.']}
    (folder / 'launch.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--browser', choices=('brave', 'edge', 'chrome'), default='brave')
    parser.add_argument('--x', type=int, default=100)
    parser.add_argument('--y', type=int, default=100)
    parser.add_argument('--width', type=int, default=640)
    parser.add_argument('--height', type=int, default=780)
    parser.add_argument('--describe', action='store_true', help='Validate and print launch plan without opening a window')
    args = parser.parse_args()
    browser = find_browser(args.browser)
    command = build_command(args.url, browser, (args.x, args.y), (args.width, args.height))
    if args.describe:
        receipt = {'command': command, 'launches': False, 'starts_services': False,
                   'microphone_auto_enabled': False, 'activation': False}
    else:
        receipt = launch(args.url, browser, (args.x, args.y), (args.width, args.height))
    if sys.stdout:
        print(json.dumps(receipt))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
