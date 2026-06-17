#!/bin/bash
# Deploy Polybot di VPS — git pull lalu restart service.
#
# Pakai:  bash deploy/deploy.sh

set -e

# Cari root project (folder parent dari deploy/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
SERVICE="polybot"

cd "$ROOT"

echo "=== Deploy Polybot ==="
echo "Dir: $ROOT"

# 1. Git pull
echo "→ git pull..."
BEFORE=$(git rev-parse --short HEAD)
git pull --ff-only
AFTER=$(git rev-parse --short HEAD)

if [ "$BEFORE" = "$AFTER" ]; then
    echo "→ Tidak ada perubahan baru ($AFTER)"
else
    echo "→ Update: $BEFORE → $AFTER"
    git --no-pager log --oneline "$BEFORE..$AFTER"
fi

# 2. Install dependency baru jika requirements berubah
if git --no-pager diff --name-only "$BEFORE" "$AFTER" 2>/dev/null | grep -q "requirements.txt"; then
    echo "→ requirements.txt berubah, install ulang..."
    .venv/bin/pip install -r requirements.txt
fi

# 3. Restart service
if command -v systemctl &>/dev/null; then
    echo "→ Restart service $SERVICE..."
    systemctl restart "$SERVICE"
    sleep 2
    if systemctl is-active --quiet "$SERVICE"; then
        echo "→ Service aktif ✅"
    else
        echo "⚠️  Service GAGAL start — cek: journalctl -u $SERVICE -n 50"
        exit 1
    fi
else
    echo "⚠️  systemctl tidak ada — jalankan manual: python main.py"
fi

echo "=== Deploy selesai ✅ ==="
