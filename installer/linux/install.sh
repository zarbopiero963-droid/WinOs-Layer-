#!/usr/bin/env bash
# Install WinOs-Layer Linux portable binary + optional systemd unit.
#
# Linux package runs FakeBackend / OS-portable API server for non-Windows hosts.
# Windows EXE uses WindowsBackend when on Win32 — do not use this script there.
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

echo "Installing WinOs-Layer Linux portable → ${DEST}"
echo "  (FakeBackend / OS-portable API — not a Windows emulator)"
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

# Generate API key if missing
API_KEY_FILE="${DEST}/api_key.txt"
if [[ ! -f "${API_KEY_FILE}" ]]; then
  KEY="$(openssl rand -hex 24 2>/dev/null || python3 -c 'import secrets; print(secrets.token_hex(24))')"
  if [[ "${NEED_SUDO}" -eq 1 && "$(id -u)" -ne 0 ]]; then
    echo "${KEY}" | sudo tee "${API_KEY_FILE}" >/dev/null
    sudo chmod 600 "${API_KEY_FILE}"
  else
    echo "${KEY}" > "${API_KEY_FILE}"
    chmod 600 "${API_KEY_FILE}"
  fi
  echo "Generated API key → ${API_KEY_FILE}"
else
  KEY="$(cat "${API_KEY_FILE}" 2>/dev/null || true)"
  echo "Reusing existing API key at ${API_KEY_FILE}"
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

Start manually:
  WINOS_BACKEND=fake WINOS_API_KEYS='["${KEY:-see-api_key.txt}"]' \\
    ${DEST}/winos-api serve --host 127.0.0.1 --port 8765

Or with systemd:
  ${SYSTEMCTL[*]} enable --now winos-api
  ${SYSTEMCTL[*]} status winos-api

Open:
  http://127.0.0.1:8765/docs
  Header: X-API-Key: (contents of ${API_KEY_FILE})

This is the Linux portable FastAPI server (FakeBackend-capable), NOT a
Windows emulator. For real Windows automation use the Windows EXE/Setup
artifacts from GitHub Actions (dist-windows).
MSG
