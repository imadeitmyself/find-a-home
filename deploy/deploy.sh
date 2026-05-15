#!/usr/bin/env bash
# deploy.sh — push local changes to GitHub then pull + reload on the VPS
set -euo pipefail

VPS="ovh"
APP_DIR="~/find-a-home"
VENV="$APP_DIR/venv/bin/activate"

echo "=== Pushing to GitHub ==="
git push origin main

echo "=== Deploying to VPS ==="
ssh "$VPS" bash <<EOF
set -euo pipefail
cd $APP_DIR
source $VENV
git pull
pip install -e . -q
echo "Deploy complete — \$(git log --oneline -1)"
EOF

echo "=== Done ==="
