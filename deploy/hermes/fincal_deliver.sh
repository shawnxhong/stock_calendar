#!/bin/sh
set -eu
# Hermes no_agent cron 入口：注入生产 runtime/ 环境变量后调用分发器。
export FINCAL_DATA_DIR="$HOME/stock_calendar/runtime/data"
export FINCAL_LOG_DIR="$HOME/stock_calendar/runtime/logs"
exec "$HOME/stock_calendar/.venv/bin/python" \
  "$HOME/stock_calendar/financial-calendar/scripts/deliver_im.py" "$@"
