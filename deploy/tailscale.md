# Tailscale deployment guide — PRED Agent
# Grounded in kit/09_CONFIG_SECURITY_DEPLOY.md (Deploy section)
#
# The Control API must NEVER be exposed to the public internet.
# Tailscale creates a private mesh VPN between the VPS and your phone.

## 1. Install Tailscale on VPS (Ubuntu)

curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# Note the tailnet IP assigned (e.g. 100.64.0.5)
sudo tailscale ip

## 2. Install Tailscale on phone

# iOS: App Store — Tailscale
# Android: Play Store — Tailscale
# Log in with the same account, connect to the same tailnet.

## 3. Bind Control API to Tailscale interface

# Edit backend/.env on the VPS:
CONTROL_API_BIND=100.x.y.z   # your VPS tailnet IP from `tailscale ip`

# Or use tailscale serve:
sudo tailscale serve 8000   # exposes Control API on tailnet only

## 4. Firewall rules (UFW example)

sudo ufw default deny incoming
sudo ufw allow from any to any app OpenSSH   # SSH only
sudo ufw allow out                            # outbound for PRED API, updates
sudo ufw enable

# Alternatively: deny all except SSH (key-only) or Tailscale SSH
# sudo tailscale up --ssh

## 5. Start the agent

cd /path/to/pred-agent
docker compose up -d

## 6. Verify

curl http://<tailscale-ip>:8000/health   # from phone on same tailnet
# Should return: {"ok":true,"status":"healthy"}

## 7. Pair phone (Control API)

# On the VPS:
docker compose exec pred-agent python scripts/pair_device.py --name "my-phone"

# Enter the printed code in the Expo app Pairing screen.

## Security reminders

- Never publish port 8000 to the public internet.
- SSH: key-only, or use Tailscale SSH.
- .env mode 600; never commit .env.
- Disk encryption on the VPS where possible.
