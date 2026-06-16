# Deploy Polybot ke VPS

## Kebutuhan VPS
- Ubuntu 22.04 LTS (minimal)
- RAM: 512MB (1GB recommended)
- Storage: 5GB
- Domain yang sudah diarahkan ke IP VPS

## Langkah Deploy

### 1. Upload project ke VPS
```bash
# Dari PC lokal
scp -r /path/to/polybot user@VPS_IP:/opt/polybot
```

### 2. Jalankan setup script di VPS
```bash
ssh root@VPS_IP
bash /opt/polybot/deploy/setup_vps.sh
```

### 3. Setup password dashboard
```bash
# Buat username & password untuk login dashboard
apt install -y apache2-utils
htpasswd -c /etc/nginx/.polybot_htpasswd admin
# Masukkan password yang kamu mau
```

### 4. Setup Nginx
```bash
# Ganti YOURDOMAIN.COM di nginx.conf
sed -i 's/YOURDOMAIN.COM/tradingbot.domainmu.com/g' /opt/polybot/deploy/nginx.conf

cp /opt/polybot/deploy/nginx.conf /etc/nginx/sites-available/polybot
ln -s /etc/nginx/sites-available/polybot /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

### 5. SSL Certificate (gratis via Let's Encrypt)
```bash
certbot --nginx -d tradingbot.domainmu.com
```

### 6. Copy .env ke VPS
```bash
# Dari PC lokal
scp /path/to/polybot/.env user@VPS_IP:/opt/polybot/.env
```

### 7. Jalankan bot
```bash
systemctl start polybot
systemctl status polybot
```

### 8. Update EA di MT5
Di MetaEditor, ganti input `InpServerURL`:
```
Sebelum: http://127.0.0.1:47302
Sesudah: https://tradingbot.domainmu.com
```
Recompile dan restart EA.

## Monitoring
```bash
# Lihat log bot
journalctl -u polybot -f

# Restart bot
systemctl restart polybot

# Stop bot
systemctl stop polybot
```

## Akses Dashboard
Buka browser: `https://tradingbot.domainmu.com`
Login dengan username/password yang dibuat di langkah 3.

## Keamanan
- Dashboard dilindungi Basic Auth (username + password)
- Endpoint `/ea/*` terbuka tapi bisa di-whitelist IP PC kamu
- SSL wajib (HTTPS) — MT5 EA mendukung HTTPS
