#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NGINX_BIN="${NGINX_BIN:-}"

if [[ -z "${NGINX_BIN}" ]]; then
  if command -v openresty >/dev/null 2>&1; then
    NGINX_BIN="$(command -v openresty)"
  elif command -v nginx >/dev/null 2>&1; then
    NGINX_BIN="$(command -v nginx)"
  else
    echo "Neither openresty nor nginx was found in PATH." >&2
    exit 1
  fi
fi

mkdir -p "${REPO_ROOT}/data/runtime"
"${NGINX_BIN}" -p "${REPO_ROOT}/" -c deploy/nginx/nginx.conf -t

PID_FILE="${REPO_ROOT}/data/runtime/nginx.pid"
if [[ -f "${PID_FILE}" ]] && kill -0 "$(cat "${PID_FILE}")" 2>/dev/null; then
  exec "${NGINX_BIN}" -p "${REPO_ROOT}/" -c deploy/nginx/nginx.conf -s reload
fi

exec "${NGINX_BIN}" -p "${REPO_ROOT}/" -c deploy/nginx/nginx.conf
