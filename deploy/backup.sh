#!/bin/bash
# Nightly encrypted DB backup — PRED Agent
# Grounded in kit/09_CONFIG_SECURITY_DEPLOY.md (Backups section)
#
# Usage:
#   cp deploy/secrets/backup_key.example deploy/secrets/backup.key
#   chmod 600 deploy/secrets/backup.key
#   Add to root crontab:
#     0 2 * * * /path/to/pred-agent/deploy/backup.sh
#
# Requires: openssl, postgres-client, aws-cli or rclone (for off-box upload)

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${PROJECT_DIR}/deploy/backups"
BACKUP_KEY="${PROJECT_DIR}/deploy/secrets/backup.key"
TIMESTAMP="$(date +%Y%m%d_%H%M%SZ)"
BACKUP_FILE="${BACKUP_DIR}/pred-agent-db-${TIMESTAMP}.sql.gz.enc"

mkdir -p "${BACKUP_DIR}"

if [ ! -f "${BACKUP_KEY}" ]; then
  echo "ERROR: Backup key not found at ${BACKUP_KEY}" >&2
  exit 1
fi

# Dump the database
docker compose -f "${PROJECT_DIR}/docker-compose.yml" exec -T db \
  pg_dump -U predagent predagent | gzip | \
  openssl enc -aes-256-cbc -salt -pbkdf2 -pass file:"${BACKUP_KEY}" \
  > "${BACKUP_FILE}"

echo "Backup created: ${BACKUP_FILE}"

# Optional: upload to off-box storage (S3, rclone, etc.)
# aws s3 cp "${BACKUP_FILE}" s3://your-backup-bucket/pred-agent/
# or:
# rclone copy "${BACKUP_FILE}" remote:pred-agent-backups/

# Retain only last 7 days locally
find "${BACKUP_DIR}" -name "pred-agent-db-*.sql.gz.enc" -mtime +7 -delete

echo "Backup complete."
