#!/bin/bash
# Restore encrypted DB backup — PRED Agent
#
# Usage:
#   ./deploy/restore.sh deploy/backups/pred-agent-db-20261027_020000Z.sql.gz.enc
#
# Requires: openssl, postgres-client

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_KEY="${PROJECT_DIR}/deploy/secrets/backup.key"

if [ $# -lt 1 ]; then
  echo "Usage: $0 <encrypted-backup-file>" >&2
  exit 1
fi

BACKUP_FILE="$1"

if [ ! -f "${BACKUP_FILE}" ]; then
  echo "ERROR: Backup file not found: ${BACKUP_FILE}" >&2
  exit 1
fi

if [ ! -f "${BACKUP_KEY}" ]; then
  echo "ERROR: Backup key not found at ${BACKUP_KEY}" >&2
  exit 1
fi

echo "WARNING: This will overwrite the current database."
read -p "Continue? (yes/no): " confirm
if [ "${confirm}" != "yes" ]; then
  echo "Aborted."
  exit 0
fi

# Decrypt and restore
openssl enc -d -aes-256-cbc -pbkdf2 -pass file:"${BACKUP_KEY}" \
  -in "${BACKUP_FILE}" | gunzip | \
  docker compose -f "${PROJECT_DIR}/docker-compose.yml" exec -T db \
  psql -U predagent predagent

echo "Restore complete. Verify with:"
echo "  docker compose exec pred-agent python scripts/whoami.py"
