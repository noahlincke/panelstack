#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: ./deploy_panels.sh [--no-build] [--help]

Builds and deploys Panel Stack to https://noah.lincke.org/panels/.

Required for first authenticated deploy:
  APP_PASSWORD_HASH

Optional environment overrides:
  SSH_KEY        (default: ~/.ssh/id_ed25519_personal)
  SSH_TARGET     (default: noah#lincke.org@lincke.org)
  REMOTE_APP_DIR (default: /home/noah/panelstack)
  REMOTE_WEB_DIR (default: /home/noah/public_html/panels)
  REMOTE_PYTHON  (default: /home/noah/.local/python-3.11.11/bin/python)
  APP_SESSION_SECRET (default: generated on first deploy when APP_PASSWORD_HASH is provided)
EOF
}

SKIP_BUILD=0
case "${1:-}" in
  --no-build)
    SKIP_BUILD=1
    ;;
  --help|-h)
    usage
    exit 0
    ;;
  "")
    ;;
  *)
    echo "Unknown option: $1" >&2
    usage
    exit 1
    ;;
esac

SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519_personal}"
SSH_TARGET="${SSH_TARGET:-noah#lincke.org@lincke.org}"
REMOTE_APP_DIR="${REMOTE_APP_DIR:-/home/noah/panelstack}"
REMOTE_WEB_DIR="${REMOTE_WEB_DIR:-/home/noah/public_html/panels}"
REMOTE_PYTHON="${REMOTE_PYTHON:-/home/noah/.local/python-3.11.11/bin/python}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

command -v npm >/dev/null 2>&1 || { echo "npm is required." >&2; exit 1; }
command -v ssh >/dev/null 2>&1 || { echo "ssh is required." >&2; exit 1; }
command -v rsync >/dev/null 2>&1 || { echo "rsync is required." >&2; exit 1; }
command -v mktemp >/dev/null 2>&1 || { echo "mktemp is required." >&2; exit 1; }

if [[ "$SKIP_BUILD" -eq 0 ]]; then
  npm --prefix frontend run build -- --base=/panels/
fi

if ! grep -q 'src="/panels/assets/' frontend/dist/index.html; then
  echo "frontend/dist is not built with --base=/panels/. Run ./deploy_panels.sh without --no-build." >&2
  exit 1
fi

ssh -i "$SSH_KEY" "$SSH_TARGET" \
  "mkdir -p '$REMOTE_APP_DIR' '$REMOTE_APP_DIR/tmp' '$REMOTE_APP_DIR/frontend/dist' '$REMOTE_WEB_DIR'"

REMOTE_ENV_EXISTS="$(
  ssh -i "$SSH_KEY" "$SSH_TARGET" \
    "[ -f '$REMOTE_APP_DIR/.env' ] && echo yes || echo no"
)"

if [[ "$REMOTE_ENV_EXISTS" != "yes" ]]; then
  if [[ -z "${APP_PASSWORD_HASH:-}" ]]; then
    echo "Missing APP_PASSWORD_HASH for first authenticated deploy." >&2
    exit 1
  fi
  if [[ -z "${APP_SESSION_SECRET:-}" ]]; then
    command -v openssl >/dev/null 2>&1 || { echo "openssl is required to generate APP_SESSION_SECRET." >&2; exit 1; }
    APP_SESSION_SECRET="$(openssl rand -hex 32)"
  fi
  ENV_FILE="$(mktemp)"
  trap 'rm -f "$ENV_FILE"' EXIT
  {
    printf 'APP_PASSWORD_HASH=%s\n' "$APP_PASSWORD_HASH"
    printf 'APP_SESSION_SECRET=%s\n' "$APP_SESSION_SECRET"
  } > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  rsync -a -e "ssh -i $SSH_KEY" "$ENV_FILE" "$SSH_TARGET:$REMOTE_APP_DIR/.env"
  ssh -i "$SSH_KEY" "$SSH_TARGET" "chmod 600 '$REMOTE_APP_DIR/.env'"
fi

rsync -a --delete \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude 'backend/.venv/' \
  --exclude 'frontend/node_modules/' \
  --exclude 'frontend/dist/' \
  --exclude 'node_modules/' \
  --exclude 'downloads/' \
  --exclude 'fixture-downloads/' \
  --exclude 'backend/*.db' \
  --exclude 'backend/.panelstack-init.lock' \
  --exclude 'backend/data/cache/' \
  --exclude 'backend/data/app_settings.json' \
  -e "ssh -i $SSH_KEY" \
  backend comics.py passenger_wsgi.py requirements.txt "$SSH_TARGET:$REMOTE_APP_DIR/"

rsync -a --delete -e "ssh -i $SSH_KEY" frontend/dist/ "$SSH_TARGET:$REMOTE_APP_DIR/frontend/dist/"
rsync -a --delete -e "ssh -i $SSH_KEY" frontend/dist/ "$SSH_TARGET:$REMOTE_WEB_DIR/"

ssh -i "$SSH_KEY" "$SSH_TARGET" \
  "set -e
   cd '$REMOTE_APP_DIR'
   if [ -x .venv/bin/python ] && ! .venv/bin/python -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)'; then
     rm -rf .venv
   fi
   if [ ! -x .venv/bin/python ]; then
     '$REMOTE_PYTHON' -m venv .venv
   fi
   REQ_HASH=\"\$(.venv/bin/python - <<'PY'
from pathlib import Path
import hashlib

print(hashlib.sha256(Path('backend/requirements.txt').read_bytes()).hexdigest())
PY
)\"
   if [ ! -f .venv/.requirements.sha256 ] || [ \"\$(cat .venv/.requirements.sha256)\" != \"\$REQ_HASH\" ]; then
     if .venv/bin/python - <<'PY'
import a2wsgi
import fastapi
import requests
import sqlalchemy
PY
     then
       printf '%s\n' \"\$REQ_HASH\" > .venv/.requirements.sha256
     else
       PIP_USER=false .venv/bin/python -m pip install --upgrade pip
       PIP_USER=false .venv/bin/python -m pip install -r backend/requirements.txt
       printf '%s\n' \"\$REQ_HASH\" > .venv/.requirements.sha256
     fi
   fi
	   if grep -q '^SESSION_SECRET=' .env && ! grep -q '^APP_SESSION_SECRET=' .env; then
	     LEGACY_SESSION_SECRET=\"\$(sed -n 's/^SESSION_SECRET=//p' .env | tail -1)\"
	     printf 'APP_SESSION_SECRET=%s\n' \"\$LEGACY_SESSION_SECRET\" >> .env
	   fi
	   PANELSTACK_SYNC_PROVIDERS_ON_STARTUP=0 .venv/bin/python - <<'PY'
from backend.app.main import initialize_application

initialize_application()
PY
	   cat > '$REMOTE_WEB_DIR/.htaccess' <<EOF
PassengerEnabled on
PassengerAppRoot $REMOTE_APP_DIR
PassengerPython $REMOTE_APP_DIR/.venv/bin/python
PassengerStartupFile passenger_wsgi.py
PassengerBaseURI /panels
EOF
   touch tmp/restart.txt"

echo "Deployed to https://noah.lincke.org/panels/"
