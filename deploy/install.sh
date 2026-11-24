#!/usr/bin/env bash
# Установка XMR Miner на Ubuntu 22.04/24.04. Запуск: sudo bash deploy/install.sh your-domain.com
set -euo pipefail
DOMAIN="${1:?Укажите домен: sudo bash deploy/install.sh example.com}"
APP=/opt/xmrmine
SRC="$(cd "$(dirname "$0")/.." && pwd)"

apt-get update
apt-get install -y python3 python3-venv nginx certbot python3-certbot-nginx rsync

id xmrmine &>/dev/null || useradd -r -s /usr/sbin/nologin -d "$APP" xmrmine
mkdir -p "$APP"
rsync -a --exclude .git --exclude .env --exclude '*.db' "$SRC/" "$APP/"
[ -f "$APP/.env" ] || { cp "$APP/.env.example" "$APP/.env"; sed -i "s#^WEBAPP_URL=.*#WEBAPP_URL=https://$DOMAIN#" "$APP/.env"; }

python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"
chown -R xmrmine:xmrmine "$APP"; chmod 600 "$APP/.env"

cp "$APP/deploy/xmrmine.service" /etc/systemd/system/
sed "s/DOMAIN/$DOMAIN/g" "$APP/deploy/nginx.conf" > /etc/nginx/sites-available/xmrmine
ln -sf /etc/nginx/sites-available/xmrmine /etc/nginx/sites-enabled/xmrmine
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --register-unsafely-without-email --redirect

systemctl daemon-reload
systemctl enable xmrmine
echo
echo "Готово. Теперь впишите BOT_TOKEN:  sudo nano $APP/.env"
echo "Затем:  sudo systemctl restart xmrmine && sudo journalctl -u xmrmine -f"
