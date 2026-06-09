#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-${REPO_ROOT}/.venv}"

cd "${REPO_ROOT}"

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "Python not found: ${PYTHON_BIN}" >&2
  exit 1
fi

"${PYTHON_BIN}" -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/python" -m pip install --upgrade pip
"${VENV_DIR}/bin/python" -m pip install -e .

chmod +x "${REPO_ROOT}"/scripts/*.sh

if [[ ! -f "${REPO_ROOT}/.env" ]]; then
  cp "${REPO_ROOT}/.env.example" "${REPO_ROOT}/.env"
  echo "Created ${REPO_ROOT}/.env from .env.example"
fi

mkdir -p "${REPO_ROOT}/data/runtime"

cat <<EOF
Gamma Linux install complete.

Next steps:
1. Edit ${REPO_ROOT}/.env
2. Start services manually:
   ${REPO_ROOT}/.venv/bin/python -m uvicorn gamma.main:app --host 0.0.0.0 --port 8000 --no-access-log
   ${REPO_ROOT}/.venv/bin/python -m uvicorn gamma.dashboard.main:app --host 0.0.0.0 --port 8001 --no-access-log
3. Optional systemd templates:
   ${REPO_ROOT}/deploy/systemd/gamma-shana.service
   ${REPO_ROOT}/deploy/systemd/gamma-dashboard.service
EOF
