#!/usr/bin/env bash
# Re-lock app_unified deps: requirements.in -> requirements.txt (pinned + hashed).
#
#   bash app_unified/scripts/lock-deps.sh            # re-lock, NO version bumps
#   bash app_unified/scripts/lock-deps.sh --upgrade  # bump to latest allowed
#
# Run from repo root. Uses the project venv (Python 3.12, matches prod base).
# CI (.github/workflows/deps-lock.yml) re-runs the no-upgrade form and fails if
# the committed requirements.txt differs — so always commit the regenerated file.
set -euo pipefail

PIP_TOOLS_VERSION="7.5.3"
PIP="./venv/bin/pip"
PIP_COMPILE="./venv/bin/pip-compile"

# Ensure the exact pip-tools version (keeps local output identical to CI).
# Fixed-string + "version " anchor so e.g. a future 7.5.30 can't substring-false-match 7.5.3.
if ! "$PIP_COMPILE" --version 2>/dev/null | grep -qF "version ${PIP_TOOLS_VERSION}"; then
  "$PIP" install "pip-tools==${PIP_TOOLS_VERSION}"
fi

# Intentionally UNquoted below: empty string must expand to zero args, "--upgrade" to one.
# Quoting it would pass a spurious empty "" arg to pip-compile.
UPGRADE_FLAG=""
if [ "${1:-}" = "--upgrade" ]; then
  UPGRADE_FLAG="--upgrade"
fi

"$PIP_COMPILE" \
  --generate-hashes \
  --allow-unsafe \
  $UPGRADE_FLAG \
  --output-file app_unified/requirements.txt \
  app_unified/requirements.in

echo "OK: app_unified/requirements.txt regenerated. Review the diff and commit it."
