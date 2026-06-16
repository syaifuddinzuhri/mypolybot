#!/bin/bash
# Setup Polybot di VPS Ubuntu/Debian
# Jalankan sebagai root: bash setup_vps.sh

set -e
echo "=== Polybot VPS Setup ==="

# 1. Update sistem
apt update && apt upgrade -y

# 2. Install dependencies
apt install -y python3 python3-pip python3-venv nginx certbot python3-certbot-nginx git ufw

# 3. Buat user polybot (jangan pakai root)
if ! id "polybot" &>/dev/null; then
    useradd -m -s /bin/bash polybot
    echo "User polybot dibuat"
fi

# 4. Clone / copy project
mkdir -p /opt/polybot
chown polybot:polybot /opt/polybot
echo ""
echo "=== Copy project ke /opt/polybot/ ==="
echo "Jalankan dari PC lokal:"
echo "  scp -r /path/to/polybot/* user@VPS_IP:/opt/polybot/"
echo ""

# 5. Setup venv
su - polybot -c "
cd /opt/polybot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
mkdir -p data
"

# 6. Install systemd service
cp /opt/polybot/deploy/polybot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable polybot
echo "Service polybot terdaftar di systemd"

# 7. Firewall
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable
echo "Firewall dikonfigurasi"

echo ""
echo "=== Setup selesai! Langkah selanjutnya: ==="
echo "1. Copy .env ke /opt/polybot/.env"
echo "2. Edit /opt/polybot/deploy/nginx.conf — ganti YOURDOMAIN.COM"
echo "3. cp /opt/polybot/deploy/nginx.conf /etc/nginx/sites-available/polybot"
echo "4. ln -s /etc/nginx/sites-available/polybot /etc/nginx/sites-enabled/"
echo "5. certbot --nginx -d YOURDOMAIN.COM"
echo "6. systemctl start polybot"
echo "7. Di MT5 EA — ganti URL ke https://YOURDOMAIN.COM"
