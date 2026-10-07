#!/usr/bin/env bash
set -euo pipefail

APP_DIR=/home/ubuntu/dayly
sudo apt update
sudo apt install -y python3 python3-venv python3-pip

sudo mkdir -p "$APP_DIR/data"
sudo chown -R ubuntu:ubuntu "$APP_DIR"

cd "$APP_DIR"
python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  echo "Created $APP_DIR/.env — edit it before starting the bot."
else
  echo "$APP_DIR/.env already exists; leaving it unchanged."
fi

sudo cp dayly.service /etc/systemd/system/dayly.service
sudo systemctl daemon-reload
sudo systemctl enable dayly

echo
 echo "Installation complete. Next: nano $APP_DIR/.env"
echo "Then: sudo systemctl start dayly"
echo "Status: sudo systemctl status dayly"
echo "Logs: sudo journalctl -u dayly -f"
