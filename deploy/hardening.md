# Server hardening checklist — PRED Agent
# Grounded in kit/09_CONFIG_SECURITY_DEPLOY.md (Server hardening checklist)
#
# Run these on the Ubuntu VPS before starting the agent.

## 1. Run as non-root user

# The Dockerfile creates a `predagent` user and runs the container as that user.
# Verify:
docker compose exec pred-agent id
# Should show uid=999(predagent) gid=999(predagent)

## 2. .env file permissions

chmod 600 backend/.env
# Verify no group/other read:
stat -c "%a %n" backend/.env   # should be 600

## 3. Timezone UTC + NTP

timedatectl set-timezone UTC
sudo apt-get install -y chrony   # or systemd-timesyncd
timedatectl status               # should show UTC and synchronized

# Signing timestamps must stay within ±30 s of PRED server time.

## 4. Unattended security updates

sudo apt-get install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades

## 5. Disk encryption (where possible)

# For cloud VPS, enable encrypted boot volume at provisioning time.
# Verify:
lsblk -f   # should show crypto_LUKS on root or data volumes

## 6. SSH hardening

# /etc/ssh/sshd_config:
#   PermitRootLogin no
#   PasswordAuthentication no
#   PubkeyAuthentication yes
#   MaxAuthTries 3
sudo systemctl restart ssh

# Or use Tailscale SSH:
# sudo tailscale up --ssh

## 7. Firewall

sudo ufw default deny incoming
sudo ufw allow from any to any app OpenSSH
sudo ufw enable

# Control API (8000) is NOT opened publicly — only via Tailscale.

## 8. Fail2ban (optional but recommended)

sudo apt-get install -y fail2ban
sudo systemctl enable fail2ban

## 9. Verify no secrets in image

docker compose run --rm pred-agent grep -r "PRIVATE_KEY\|PRED_API_KEY\|TELEGRAM_BOT_TOKEN" /app/backend/src/ || true
# Should return nothing (secrets are in .env only, mounted at runtime)

## 10. Run pip-audit

docker compose exec pred-agent pip-audit
# Should report no known vulnerabilities.
