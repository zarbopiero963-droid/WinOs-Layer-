#!/usr/bin/env bash
# N028: Generate release-dist/SHA256SUMS.txt without self-hash (fail-closed).
# Intended caller: .github/workflows/release.yml "Stage release files".
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="${1:-release-dist}"
cd "$ROOT"
mapfile -t ARTIFACTS < <(find "$DIST" -maxdepth 1 -type f ! -name 'SHA256SUMS.txt' | sort)
if [ "${#ARTIFACTS[@]}" -eq 0 ]; then
  echo "N028: no release artifacts to checksum under $DIST" >&2
  exit 1
fi
python scripts/build_installer.py checksums "${ARTIFACTS[@]}" -o "$DIST/SHA256SUMS.txt"
python scripts/build_installer.py verify-checksums "$DIST/SHA256SUMS.txt" --root "$DIST"
cat "$DIST/SHA256SUMS.txt"
ls -la "$DIST"
