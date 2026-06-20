#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/server-desk}"
SERVICE_NAME="${SERVICE_NAME:-server-desk}"
REMOTE_NAME="${REMOTE_NAME:-origin}"
BRANCH_NAME="${BRANCH_NAME:-main}"
BACKUP_SCRIPT="${BACKUP_SCRIPT:-${APP_DIR}/scripts/server_desk_backup.sh}"
LOCK_FILE="${LOCK_FILE:-/run/server-desk-git-sync.lock}"
APP_ENV="${APP_ENV:-/etc/server-desk/server-desk.env}"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "another sync is running"
  exit 0
fi

if [[ ! -d "$APP_DIR/.git" ]]; then
  echo "$APP_DIR is not a git work tree" >&2
  exit 1
fi

cd "$APP_DIR"
before="$(git rev-parse HEAD)"
git fetch --prune "$REMOTE_NAME" "$BRANCH_NAME"
after="$(git rev-parse "${REMOTE_NAME}/${BRANCH_NAME}")"

if [[ "$before" == "$after" ]]; then
  echo "already up to date at $before"
  exit 0
fi

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "working tree has local changes, refusing automatic update" >&2
  git status --short --untracked-files=no >&2
  exit 1
fi

if [[ -x "$BACKUP_SCRIPT" ]]; then
  "$BACKUP_SCRIPT"
else
  echo "backup script is missing or not executable: $BACKUP_SCRIPT" >&2
  exit 1
fi

was_active=0
if systemctl is-active --quiet "$SERVICE_NAME"; then
  was_active=1
fi

restart_service() {
  if [[ "$was_active" -eq 1 ]]; then
    systemctl start "$SERVICE_NAME" || true
  fi
}
trap restart_service EXIT

systemctl stop "$SERVICE_NAME"
git merge --ff-only "${REMOTE_NAME}/${BRANCH_NAME}"

if [[ -x .venv/bin/python ]]; then
  .venv/bin/python -m pip install -r requirements.txt
  .venv/bin/python -m pytest
else
  echo "python virtual environment not found: ${APP_DIR}/.venv" >&2
  exit 1
fi

systemctl start "$SERVICE_NAME"
trap - EXIT
systemctl is-active --quiet "$SERVICE_NAME"

if [[ -f "$APP_ENV" ]]; then
  sleep 2
  curl -fsS http://127.0.0.1:8090/api/health >/dev/null
fi

echo "updated $SERVICE_NAME from $before to $after"
