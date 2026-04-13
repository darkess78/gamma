#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RVC_ROOT="${RVC_ROOT:-}"
RVC_VENV_DIR="${RVC_VENV_DIR:-}"
PYTHON_BIN="${PYTHON_BIN:-}"

find_python() {
  if [[ -n "${PYTHON_BIN}" ]] && command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
    printf '%s\n' "${PYTHON_BIN}"
    return 0
  fi

  local candidate
  for candidate in python3.11 python3.10 python3; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done

  return 1
}

discover_rvc_root() {
  if [[ -n "${RVC_ROOT}" && -d "${RVC_ROOT}" ]]; then
    printf '%s\n' "${RVC_ROOT}"
    return 0
  fi

  local base
  local candidate
  for base in \
    "${REPO_ROOT}" \
    "$(dirname "${REPO_ROOT}")" \
    "$(dirname "$(dirname "${REPO_ROOT}")")" \
    "${HOME}" \
    "${HOME}/Projects"; do
    for candidate in \
      "${base}/RVC/Retrieval-based-Voice-Conversion-WebUI-main" \
      "${base}/Retrieval-based-Voice-Conversion-WebUI-main" \
      "${base}/data/RVC/Retrieval-based-Voice-Conversion-WebUI-main"; do
      if [[ -f "${candidate}/tools/infer_cli.py" && -d "${candidate}/assets/weights" ]]; then
        printf '%s\n' "${candidate}"
        return 0
      fi
    done
  done

  return 1
}

PYTHON="$(find_python)" || {
  echo "Could not find a suitable Python interpreter. Set PYTHON_BIN to python3.11 or python3." >&2
  exit 1
}

RVC_ROOT="$(discover_rvc_root)" || {
  echo "Could not find the RVC checkout." >&2
  echo "Set RVC_ROOT to your Retrieval-based-Voice-Conversion-WebUI-main directory." >&2
  exit 1
}

if [[ -z "${RVC_VENV_DIR}" ]]; then
  RVC_VENV_DIR="$(dirname "${RVC_ROOT}")/.venv"
fi

REQ_FILE="${RVC_ROOT}/requirements-py311.txt"
if [[ ! -f "${REQ_FILE}" ]]; then
  REQ_FILE="${RVC_ROOT}/requirements.txt"
fi

if [[ ! -f "${REQ_FILE}" ]]; then
  echo "Could not find requirements-py311.txt or requirements.txt in ${RVC_ROOT}." >&2
  exit 1
fi

echo "Using Python: ${PYTHON}"
echo "Using RVC root: ${RVC_ROOT}"
echo "Using RVC venv: ${RVC_VENV_DIR}"
echo "Using requirements file: ${REQ_FILE}"

"${PYTHON}" -m venv "${RVC_VENV_DIR}"
"${RVC_VENV_DIR}/bin/python" -m pip install --upgrade pip setuptools wheel
"${RVC_VENV_DIR}/bin/python" -m pip install -r "${REQ_FILE}"
"${RVC_VENV_DIR}/bin/python" "${REPO_ROOT}/scripts/patch_rvc_for_gamma.py" --repo-root "${RVC_ROOT}"

cat <<EOF
RVC Linux install complete.

Detected checkout:
  ${RVC_ROOT}

Detected virtualenv:
  ${RVC_VENV_DIR}

Suggested Gamma .env values:
  SHANA_RVC_PROJECT_ROOT=${RVC_ROOT}
  SHANA_RVC_PYTHON=${RVC_VENV_DIR}/bin/python

Notes:
- Gamma can auto-discover SHANA_RVC_PROJECT_ROOT and SHANA_RVC_PYTHON if this layout stays intact.
- SHANA_RVC_INDEX_PATH can often be omitted when the selected model has a matching .index file under assets/indices or logs.
- You still need to choose SHANA_RVC_MODEL_NAME explicitly.
EOF
