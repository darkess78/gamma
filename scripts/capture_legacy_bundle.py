#!/usr/bin/env python3
"""Read-only status by default; capture requires an explicit command/destination."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from gamma.coordination.legacy_bundle import (  # noqa: E402
    DEFAULT_SOURCE, CaptureError, capture, inspect_bundle, status,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    commands = parser.add_subparsers(dest='command')
    commands.add_parser('status', help='Read-only named-writer/path inspection.')
    inspect = commands.add_parser('inspect', help='Read declared bundle status without modifying it.')
    inspect.add_argument('--bundle', required=True, type=Path)
    command = commands.add_parser('capture', help='Explicit bounded private capture. Never stops services.')
    command.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'capture':
            result = capture(args.source, args.destination)
        elif args.command == 'inspect':
            result = inspect_bundle(args.bundle)
        else:
            result = status(args.source)
        print(json.dumps(result, sort_keys=True))
        return 0 if result['status'] in {'complete', 'drained_observed'} else 2
    except Exception as exc:
        code = exc.code if isinstance(exc, CaptureError) else 'legacy_capture_' + type(exc).__name__
        print(json.dumps({'status': 'failed', 'failure_code': code,
                          'partial_destination': getattr(exc, 'destination', None)}, sort_keys=True))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
