#!/usr/bin/env bash
# Установка Post Bot на Ubuntu/Debian VPS. Запускать от root из папки репозитория:
#   bash postbot/deploy/install.sh
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"   # .../postbot
SERVICE=postbot

echo "==> Ставлю Python (если система сама обновляется — подожду до 15 минут)"
for i in $(seq 1 180); do
  pgrep -x 'apt|apt-get|dpkg|unattended-upgr' >/dev/null || break
  [ "$i" = 1 ] && echo "   Жду, пока система закончит своё обновление…"
  sleep 5
done
apt-get -o DPkg::Lock::Timeout=900 update -qq
apt-get -o DPkg::Lock::Timeout=900 install -y -qq python3 python3-venv python3-pip >/dev/null

echo "==> Виртуальное окружение и зависимости"
python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install -q --upgrade pip
"$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"

if [ ! -f "$DIR/.env" ]; then
  echo
  read -rp "Токен бота от @BotFather: " TOKEN
  read -rp "Твой Telegram ID (Enter — бот доступен всем): " ADMINS
  cat > "$DIR/.env" <<EOF
BOT_TOKEN=$TOKEN
ADMIN_IDS=$ADMINS
DB_PATH=$DIR/postbot.db
EOF
  chmod 600 "$DIR/.env"
fi

echo "==> Служба systemd"
cat > /etc/systemd/system/$SERVICE.service <<EOF
[Unit]
Description=Telegram Post Bot
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$DIR
ExecStart=$DIR/.venv/bin/python $DIR/bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now $SERVICE
sleep 3
systemctl --no-pager status $SERVICE | head -12
echo
echo "Готово. Логи: journalctl -u $SERVICE -f"
