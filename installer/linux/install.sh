#!/usr/bin/env bash
# Install WinOs-Layer Linux portable binary + optional systemd unit.
#
# Linux package runs LinuxBackend / real OS API server for non-Windows hosts
# (WINOS_BACKEND=auto). Windows EXE uses WindowsBackend when on Win32 —
# do not use this script there.
#
# Usage:
#   ./install.sh              # system install to /opt/winos-api (needs sudo)
#   ./install.sh --user       # user install to ~/.local/opt/winos-api
#   ./install.sh --no-systemd # skip unit install

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
# When packaged, binary sits next to installer/linux/ or in package root
BINARY=""
for candidate in \
  "${PKG_ROOT}/winos-api" \
  "${SCRIPT_DIR}/../winos-api" \
  "${SCRIPT_DIR}/winos-api" \
  "./winos-api"
do
  if [[ -f "${candidate}" && -x "${candidate}" ]]; then
    BINARY="$(cd "$(dirname "${candidate}")" && pwd)/$(basename "${candidate}")"
    break
  fi
done

USER_MODE=0
WITH_SYSTEMD=1
for arg in "$@"; do
  case "${arg}" in
    --user) USER_MODE=1 ;;
    --no-systemd) WITH_SYSTEMD=0 ;;
    -h|--help)
      echo "Usage: $0 [--user] [--no-systemd]"
      exit 0
      ;;
  esac
done

if [[ -z "${BINARY}" ]]; then
  echo "ERROR: winos-api binary not found next to this package." >&2
  echo "Expected winos-api in the portable zip root." >&2
  exit 1
fi

if [[ "${USER_MODE}" -eq 1 ]]; then
  DEST="${HOME}/.local/opt/winos-api"
  BIN_LINK="${HOME}/.local/bin/winos-api"
  UNIT_DIR="${HOME}/.config/systemd/user"
  SYSTEMCTL=(systemctl --user)
  NEED_SUDO=0
else
  DEST="/opt/winos-api"
  BIN_LINK="/usr/local/bin/winos-api"
  UNIT_DIR="/etc/systemd/system"
  SYSTEMCTL=(systemctl)
  NEED_SUDO=1
fi

run() {
  if [[ "${NEED_SUDO}" -eq 1 && "$(id -u)" -ne 0 ]]; then
    sudo "$@"
  else
    "$@"
  fi
}

# Denylist mirrors windows_os_api.installer.identity.WEAK_API_KEYS (fail closed).
is_weak_api_key() {
  local k
  k="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  case "${k}" in
    ""|dev|admin|test|password|secret|changeme|dev-key-change-me|winos-dev|winos-admin)
      return 0
      ;;
  esac
  return 1
}

echo "Installing WinOs-Layer Linux portable → ${DEST}"
echo "  (LinuxBackend / real OS API — WINOS_BACKEND=auto)"
if [[ "${USER_MODE}" -eq 1 ]]; then
  echo "  Install mode: user (~/.local/opt/winos-api)"
else
  echo "  Install mode: system (/opt/winos-api)"
fi
run mkdir -p "${DEST}"
run cp -f "${BINARY}" "${DEST}/winos-api"
run chmod 755 "${DEST}/winos-api"

# Docs + scripts
for f in LINUX.md README.md VERSION; do
  if [[ -f "${PKG_ROOT}/${f}" ]]; then
    run cp -f "${PKG_ROOT}/${f}" "${DEST}/"
  elif [[ -f "${SCRIPT_DIR}/${f}" ]]; then
    run cp -f "${SCRIPT_DIR}/${f}" "${DEST}/"
  fi
done
if [[ -d "${SCRIPT_DIR}" ]]; then
  run mkdir -p "${DEST}/installer"
  run cp -f "${SCRIPT_DIR}/install.sh" "${DEST}/installer/" 2>/dev/null || true
  run cp -f "${SCRIPT_DIR}/uninstall.sh" "${DEST}/installer/" 2>/dev/null || true
  run cp -f "${SCRIPT_DIR}/winos-api.service" "${DEST}/installer/" 2>/dev/null || true
fi

# Generate or reuse API key (never print the secret value)
API_KEY_FILE="${DEST}/api_key.txt"
if [[ ! -f "${API_KEY_FILE}" ]]; then
  KEY="$(openssl rand -hex 24 2>/dev/null || python3 -c 'import secrets; print(secrets.token_hex(24))')"
  if is_weak_api_key "${KEY}"; then
    echo "ERROR: generated API key matched weak/dev denylist (internal failure)." >&2
    exit 1
  fi
  if [[ "${NEED_SUDO}" -eq 1 && "$(id -u)" -ne 0 ]]; then
    echo "${KEY}" | sudo tee "${API_KEY_FILE}" >/dev/null
    sudo chmod 600 "${API_KEY_FILE}"
  else
    echo "${KEY}" > "${API_KEY_FILE}"
    chmod 600 "${API_KEY_FILE}"
  fi
  unset KEY
  echo "Generated API key → ${API_KEY_FILE} (chmod 600; value not printed)"
else
  EXISTING="$(cat "${API_KEY_FILE}" 2>/dev/null || true)"
  if [[ -z "$(printf '%s' "${EXISTING}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')" ]]; then
    echo "ERROR: existing API key file is empty: ${API_KEY_FILE}" >&2
    echo "ERROR: refuse to install with empty key (fail closed)." >&2
    exit 1
  fi
  if is_weak_api_key "${EXISTING}"; then
    echo "ERROR: existing API key at ${API_KEY_FILE} matches weak/dev denylist." >&2
    echo "ERROR: refuse to reuse (dev, admin, test, password, secret, changeme," >&2
    echo "ERROR:   dev-key-change-me, winos-dev, winos-admin). Replace the file." >&2
    exit 1
  fi
  unset EXISTING
  echo "Reusing existing API key at ${API_KEY_FILE} (value not printed)"
fi

# Symlink into PATH
run mkdir -p "$(dirname "${BIN_LINK}")"
run ln -sfn "${DEST}/winos-api" "${BIN_LINK}"

# Optional systemd unit
if [[ "${WITH_SYSTEMD}" -eq 1 ]]; then
  UNIT_SRC="${SCRIPT_DIR}/winos-api.service"
  if [[ -f "${UNIT_SRC}" ]]; then
    run mkdir -p "${UNIT_DIR}"
    # Rewrite ExecStart/WorkingDirectory for user installs
    TMP_UNIT="$(mktemp)"
    sed \
      -e "s|/opt/winos-api/winos-api|${DEST}/winos-api|g" \
      -e "s|WorkingDirectory=/opt/winos-api|WorkingDirectory=${DEST}|g" \
      -e "s|ReadWritePaths=/opt/winos-api|ReadWritePaths=${DEST}|g" \
      "${UNIT_SRC}" > "${TMP_UNIT}"
    if [[ "${USER_MODE}" -eq 1 ]]; then
      # User units should not use multi-user.target
      sed -i 's|WantedBy=multi-user.target|WantedBy=default.target|' "${TMP_UNIT}" || true
    fi
    run cp -f "${TMP_UNIT}" "${UNIT_DIR}/winos-api.service"
    rm -f "${TMP_UNIT}"
    "${SYSTEMCTL[@]}" daemon-reload || true
    echo "Installed systemd unit at ${UNIT_DIR}/winos-api.service"
  fi
fi

cat <<MSG

Install complete.

Paths:
  system install: /opt/winos-api  (default; needs sudo)
  user install:   ~/.local/opt/winos-api  (--user)

Start manually:
  WINOS_BACKEND=auto \\
    ${DEST}/winos-api serve --host 127.0.0.1 --port 8765 \\
    --api-key-file ${API_KEY_FILE}

Or with systemd:
  ${SYSTEMCTL[*]} enable --now winos-api
  ${SYSTEMCTL[*]} status winos-api

Open:
  http://127.0.0.1:8765/docs
  Header: X-API-Key: (contents of ${API_KEY_FILE} — do not paste into shell history)

This is the Linux portable FastAPI server (LinuxBackend / real OS via
WINOS_BACKEND=auto), NOT a Windows emulator. For real Windows automation
use the Windows EXE/Setup artifacts from GitHub Actions (dist-windows).
MSG
