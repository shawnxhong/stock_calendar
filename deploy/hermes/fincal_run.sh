#!/bin/sh
set -eu
# 生产管线统一入口：Hermes / 手动触发 run.py 时只允许经此 wrapper。
# 禁止直接运行 run.py --no-fetch —— 那会缺 FINCAL_DELIVERY_DIR，
# 渲染结果没有幂等键，且可能误读开发缓存或覆盖生产 health.json。
# 用法：fincal_run.sh --tier=day|week|month [--no-fetch]
export FINCAL_DATA_DIR="$HOME/stock_calendar/runtime/data"
export FINCAL_LOG_DIR="$HOME/stock_calendar/runtime/logs"
export FINCAL_DELIVERY_DIR="$HOME/stock_calendar/runtime/shadow-delivery"
exec "$HOME/stock_calendar/.venv/bin/python" \
  "$HOME/stock_calendar/financial-calendar/scripts/run.py" "$@"
