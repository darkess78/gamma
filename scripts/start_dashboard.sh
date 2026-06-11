#!/usr/bin/env bash
# Start Gamma Dashboard
# This script starts the dashboard and makes it accessible from other machines

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${REPO_ROOT}/.venv"
PORT="${SHANA_DASHBOARD_PORT:-${DASHBOARD_PORT:-8001}}"
HOST="${SHANA_DASHBOARD_BIND_HOST:-${DASHBOARD_HOST:-0.0.0.0}}"
export PYTHONPATH="${REPO_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"

echo "Starting Gamma Dashboard..."
echo "  Host: ${HOST}"
echo "  Port: ${PORT}"

if [[ ! -x "${VENV}/bin/python" ]]; then
    echo "Missing repo virtualenv at ${VENV}." >&2
    echo "Create it first with: python3 -m venv .venv && .venv/bin/python -m pip install -e '.[dev]'" >&2
    exit 1
fi

echo "Starting uvicorn server..."
exec "${VENV}/bin/python" -m uvicorn gamma.dashboard.main:app \
    --host "${HOST}" \
    --port "${PORT}" \
    --reload \
    --log-level info
