#!/usr/bin/env bash
# Uninstall WinOs-Layer Linux portable install.
#
# Linux package runs FakeBackend / OS-portable API server for non-Windows hosts.
# Windows EXE uses WindowsBackend when on Win32.
#
# Usage:
#   ./uninstall.sh           # remove system install (/opt/winos-api)
#   ./uninstall.sh --user    # remove user install (~/.local/opt/winos-api)

set -euo pipefail

USER_MODE=0
for arg in "$@"; do
  case "${arg}" in
    --user) USER_MODE=1 ;;
    -h|--help)
      echo "Usage: $0 [--user]"
      exit 0
      ;;
  esac
done

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

echo "Stopping winos-api (if running)…"
"${SYSTEMCTL[@]}" stop winos-api 2>/dev/null || true
"${SYSTEMCTL[@]}" disable winos-api 2>/dev/null || true
run rm -f "${UNIT_DIR}/winos-api.service"
"${SYSTEMCTL[@]}" daemon-reload 2>/dev/null || true

run rm -f "${BIN_LINK}"
if [[ -d "${DEST}" ]]; then
  run rm -rf "${DEST}"
  echo "Removed ${DEST}"
else
  echo "Nothing at ${DEST}"
fi

echo "Uninstall complete."
