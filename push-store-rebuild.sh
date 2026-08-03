#!/bin/bash
# Pushes the Store dashboard rebuild. Run from anywhere:
#   bash ~/Desktop/CoinPilotX/push-store-rebuild.sh
set -e
cd ~/Desktop/CoinPilotX
rm -f .git/index.lock
git checkout codex/store-dashboard-live
git pull --rebase origin codex/store-dashboard-live || true
git push origin codex/store-dashboard-live
git log --oneline -3
