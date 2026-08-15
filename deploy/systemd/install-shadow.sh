#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)
expected_root="$HOME/stock_calendar"
unit_dir="$HOME/.config/systemd/user"

if [ "$repo_root" != "$expected_root" ]; then
    echo "fatal: units expect the repo at $expected_root (found $repo_root)" >&2
    exit 2
fi

if [ ! -f "$repo_root/.env" ]; then
    echo "fatal: missing $repo_root/.env" >&2
    exit 2
fi

env_mode=$(stat -c '%a' "$repo_root/.env")
if [ "$env_mode" != "600" ]; then
    echo "fatal: $repo_root/.env must have mode 0600 (found $env_mode)" >&2
    exit 2
fi

"$repo_root/.venv/bin/python" \
    "$repo_root/financial-calendar/scripts/run.py" --doctor

install -d -m 0700 "$repo_root/runtime"
install -d -m 0755 "$unit_dir"
install -m 0644 "$script_dir/financial-calendar@.service" "$unit_dir/"
install -m 0644 "$script_dir/financial-calendar-day.timer" "$unit_dir/"
install -m 0644 "$script_dir/financial-calendar-week.timer" "$unit_dir/"
install -m 0644 "$script_dir/financial-calendar-month.timer" "$unit_dir/"

systemctl --user daemon-reload
systemctl --user enable --now \
    financial-calendar-day.timer \
    financial-calendar-week.timer \
    financial-calendar-month.timer

systemctl --user list-timers 'financial-calendar-*'
