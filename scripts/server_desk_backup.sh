#!/usr/bin/env bash
set -euo pipefail

APP_ENV="${APP_ENV:-/etc/server-desk/server-desk.env}"
BACKUP_ENV="${BACKUP_ENV:-/etc/server-desk/backup.env}"
BACKUP_ROOT="${BACKUP_ROOT:-/var/lib/server-desk-backups}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"

if [[ -f "$APP_ENV" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "$APP_ENV"
  set +a
fi

if [[ -f "$BACKUP_ENV" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "$BACKUP_ENV"
  set +a
fi

DB_PATH="${OPS_DB_PATH:-/var/lib/server-desk/ops.sqlite3}"
ENCRYPTION_KEY_FILE="${BACKUP_ENCRYPTION_KEY_FILE:-/etc/server-desk/backup.key}"
GIT_REMOTE="${BACKUP_GIT_REMOTE:-}"
GIT_BRANCH="${BACKUP_GIT_BRANCH:-main}"
GIT_AUTHOR_NAME="${BACKUP_GIT_AUTHOR_NAME:-server-desk-backup}"
GIT_AUTHOR_EMAIL="${BACKUP_GIT_AUTHOR_EMAIL:-server-desk-backup@localhost}"

if [[ ! -f "$DB_PATH" ]]; then
  echo "database not found: $DB_PATH" >&2
  exit 1
fi

if [[ ! -s "$ENCRYPTION_KEY_FILE" ]]; then
  echo "backup encryption key not found: $ENCRYPTION_KEY_FILE" >&2
  exit 1
fi

command -v openssl >/dev/null 2>&1 || {
  echo "openssl is required" >&2
  exit 1
}

install -d -m 0700 "$BACKUP_ROOT"
cd "$BACKUP_ROOT"

if [[ ! -d .git ]]; then
  git init
  git checkout -B "$GIT_BRANCH"
elif ! git symbolic-ref --quiet HEAD >/dev/null 2>&1; then
  git checkout -B "$GIT_BRANCH"
fi

if [[ -n "$GIT_REMOTE" ]]; then
  if git remote get-url origin >/dev/null 2>&1; then
    git remote set-url origin "$GIT_REMOTE"
  else
    git remote add origin "$GIT_REMOTE"
  fi
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

plain_db="$work_dir/ops.sqlite3"
python3 - "$DB_PATH" "$plain_db" <<'PY'
import sqlite3
import sys

source, target = sys.argv[1], sys.argv[2]
src = sqlite3.connect(source)
dst = sqlite3.connect(target)
with dst:
    src.backup(dst)
dst.close()
src.close()
PY

encrypted_name="ops.sqlite3.${stamp}.enc"
openssl enc -aes-256-cbc -pbkdf2 -salt \
  -in "$plain_db" \
  -out "$encrypted_name" \
  -pass "file:${ENCRYPTION_KEY_FILE}"

sha256sum "$encrypted_name" > "${encrypted_name}.sha256"
find . -maxdepth 1 -name 'ops.sqlite3.*.enc' -mtime "+${RETENTION_DAYS}" -delete
find . -maxdepth 1 -name 'ops.sqlite3.*.enc.sha256' -mtime "+${RETENTION_DAYS}" -delete

git config user.name "$GIT_AUTHOR_NAME"
git config user.email "$GIT_AUTHOR_EMAIL"
git add "$encrypted_name" "${encrypted_name}.sha256"
if git diff --cached --quiet; then
  echo "no backup changes to commit"
else
  git commit -m "Backup server-desk database ${stamp}"
fi

if [[ -n "$GIT_REMOTE" ]]; then
  git push origin "HEAD:${GIT_BRANCH}"
else
  echo "BACKUP_GIT_REMOTE is not configured, backup committed locally only"
fi
