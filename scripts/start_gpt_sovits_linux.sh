#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_DIR="${REPO_ROOT}/data/runtime"
mkdir -p "${RUNTIME_DIR}"

PORT="${GPT_SOVITS_PORT:-9881}"
PACKAGE_ROOT="${GPT_SOVITS_ROOT:-${REPO_ROOT}/data/GPT-SoVITS}"
PYTHON_BIN="${GPT_SOVITS_PYTHON:-}"
PID_FILE="${RUNTIME_DIR}/gpt_sovits.pid"
STDOUT_LOG="${RUNTIME_DIR}/gpt_sovits.stdout.log"
STDERR_LOG="${RUNTIME_DIR}/gpt_sovits.stderr.log"

if [[ -z "${PYTHON_BIN}" ]]; then
  if [[ -x "${PACKAGE_ROOT}/runtime/python" ]]; then
    PYTHON_BIN="${PACKAGE_ROOT}/runtime/python"
  elif [[ -x "${PACKAGE_ROOT}/runtime/bin/python" ]]; then
    PYTHON_BIN="${PACKAGE_ROOT}/runtime/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

if [[ ! -d "${PACKAGE_ROOT}" ]]; then
  echo "GPT-SoVITS package root not found: ${PACKAGE_ROOT}" >&2
  exit 1
fi

if [[ -f "${PID_FILE}" ]]; then
  EXISTING_PID="$(cat "${PID_FILE}")"
  if [[ -n "${EXISTING_PID}" ]] && kill -0 "${EXISTING_PID}" 2>/dev/null; then
    echo "GPT-SoVITS is already running with PID ${EXISTING_PID}."
    exit 0
  fi
  rm -f "${PID_FILE}"
fi

if command -v ss >/dev/null 2>&1; then
  if ss -ltn "( sport = :${PORT} )" | grep -q ":${PORT}"; then
    echo "Port ${PORT} is already in use." >&2
    exit 1
  fi
fi

cd "${PACKAGE_ROOT}"
rm -f "${STDOUT_LOG}" "${STDERR_LOG}"
nohup "${PYTHON_BIN}" api_v2.py -a 127.0.0.1 -p "${PORT}" -c GPT_SoVITS/configs/tts_infer.yaml >"${STDOUT_LOG}" 2>"${STDERR_LOG}" &
GPT_PID=$!
echo "${GPT_PID}" > "${PID_FILE}"

sleep 5
if ! kill -0 "${GPT_PID}" 2>/dev/null; then
  echo "GPT-SoVITS did not stay running. Check ${STDOUT_LOG} and ${STDERR_LOG}." >&2
  exit 1
fi

echo "GPT-SoVITS API is listening on http://127.0.0.1:${PORT}/tts"
