#!/usr/bin/env bash
set -euo pipefail
cd /home/ubuntu/dayly
./venv/bin/pip install -r requirements.txt
sudo systemctl restart dayly
sudo systemctl --no-pager status dayly
