#!/bin/bash
# Reset data Polybot — dipakai saat ganti akun demo / mulai bersih.
# Membersihkan: trade_log, daily_stats, state (cooldown/pnl), dan log.
# Backup otomatis dibuat sebelum reset.
#
# Pakai:  bash deploy/reset_data.sh
#         bash deploy/reset_data.sh --no-backup   (tanpa backup)

set -e

# Cari root project (folder parent dari deploy/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
DATA="$ROOT/data"
SERVICE="polybot"

BACKUP=true
[ "$1" = "--no-backup" ] && BACKUP=false

echo "=== Reset Data Polybot ==="
echo "Data dir: $DATA"

# 1. Stop service jika ada (VPS pakai systemd)
SERVICE_WAS_RUNNING=false
if command -v systemctl &>/dev/null && systemctl is-active --quiet "$SERVICE" 2>/dev/null; then
    echo "→ Menghentikan service $SERVICE..."
    systemctl stop "$SERVICE"
    SERVICE_WAS_RUNNING=true
fi

# 2. Backup data lama
if [ "$BACKUP" = true ] && [ -d "$DATA" ]; then
    TS=$(date +%Y%m%d_%H%M%S)
    BACKUP_DIR="$DATA/backup_$TS"
    mkdir -p "$BACKUP_DIR"
    for f in trade_log.json daily_stats.json state.json; do
        [ -f "$DATA/$f" ] && cp "$DATA/$f" "$BACKUP_DIR/" && echo "→ Backup $f"
    done
    echo "→ Backup disimpan di: $BACKUP_DIR"
fi

# 3. Reset file data ke kondisi bersih
mkdir -p "$DATA"
echo "[]" > "$DATA/trade_log.json"
echo "{}" > "$DATA/daily_stats.json"
echo '{"today_pnl": 0, "today_loss_count": 0, "cooldown": {}}' > "$DATA/state.json"
echo "→ trade_log.json, daily_stats.json, state.json direset"

# 4. Kosongkan log
: > "$DATA/polybot.log" 2>/dev/null || true
: > "$DATA/access.log"  2>/dev/null || true
echo "→ Log dikosongkan"

# 5. Start lagi service jika sebelumnya jalan
if [ "$SERVICE_WAS_RUNNING" = true ]; then
    echo "→ Menjalankan ulang service $SERVICE..."
    systemctl start "$SERVICE"
    sleep 2
    systemctl is-active --quiet "$SERVICE" && echo "→ Service aktif kembali" || echo "⚠️  Service GAGAL start — cek: journalctl -u $SERVICE -n 50"
fi

echo "=== Reset selesai ✅ ==="
