#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PM2_BIN="${PM2_BIN:-/home/curlycoffee3808/.nvm/versions/node/v24.18.0/bin/pm2}"

cd "$PROJECT_DIR"

"$PM2_BIN" startOrReload deploy/ecosystem.inference.config.cjs \
  --only curie-inference --update-env
"$PM2_BIN" startOrReload ecosystem.config.js \
  --only curie-main --update-env
"$PM2_BIN" startOrReload deploy/ecosystem.trained-voice.config.cjs \
  --update-env

# A cold boot resurrects this snapshot. Save only after every Curie process was
# rebuilt from its current ecosystem file so stale role mappings cannot return.
"$PM2_BIN" save
