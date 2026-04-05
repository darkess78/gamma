#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="${REPO_ROOT}/data/runtime/gpt_sovits.pid"

if [[ ! -f "${PID_FILE}" ]]; then
  echo "No GPT-SoVITS PID file found."
  exit 0
fi

PID="$(cat "${PID_FILE}")"
if [[ -z "${PID}" ]]; then
  rm -f "${PID_FILE}"
  echo "GPT-SoVITS PID file was empty."
  exit 0
fi

if ! kill -0 "${PID}" 2>/dev/null; then
  rm -f "${PID_FILE}"
  echo "No GPT-SoVITS process found for PID ${PID}."
  exit 0
fi

kill "${PID}" 2>/dev/null || true
sleep 2
if kill -0 "${PID}" 2>/dev/null; then
  kill -9 "${PID}" 2>/dev/null || true
fi

rm -f "${PID_FILE}"
echo "Stopped GPT-SoVITS PID ${PID}."
