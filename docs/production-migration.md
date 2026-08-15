# 财经日历生产迁移清单

当前主机 `/home/hong/stock_calendar` 是目标生产环境。14 天验收完成前只启用
shadow 文件投递，不启用真实 IM/邮件 adapter。确定性核心仍通过 `ports.py` 与平台能力
隔离。

## 运行边界

- 密钥：当前由权限 `0600` 的 gitignored 根目录 `.env` 提供；迁移到集中 secret manager
  时，进程环境变量会优先于 `.env`。
- 持久状态：systemd unit 设置 `FINCAL_DATA_DIR=$HOME/stock_calendar/runtime/data`；必须跨重启保留
  `state.json`、`events.json` 与 `snapshots/`。
- 日志/报告：`FINCAL_LOG_DIR=$HOME/stock_calendar/runtime/logs`。
- shadow 投递：`FINCAL_DELIVERY_DIR=$HOME/stock_calendar/runtime/shadow-delivery`；日期 + tier +
  内容哈希幂等，只有成功写入后才更新状态。
- 调度：`deploy/systemd/` 提供纽约时区的 weekday 07:00、Sunday 18:00、每月 1 日
  07:15 三个 user timer。
- 健康：每次运行写 `runtime/data/health.json`（`healthy` / `degraded` / `unhealthy`）；
  critical failure 返回 2，pipeline 失败返回 3，并使 systemd unit 失败。BLS 官方 403
  保持可见并标为 `degraded`，但不是 critical。

## 部署闸门

1. 按 `requirements.lock` 安装 Python 3.13 依赖。
2. 填写真实 `config/watchlist.yaml`。
3. 运行 bootstrap；确认 review 为 0，并由 operator 抽查 5 条 release ID。
4. 运行 `run.py --doctor`；除已知 BLS 403 外必须通过。
5. 以真实 watchlist 对 month/week/day 各执行一次 shadow run，检查长短版。
6. 执行 `deploy/systemd/install-shadow.sh`，开始不可压缩的 14 天验收。
7. shadow 验收通过后才实现/启用真实 DeliveryAdapter；首次投递保持人工审批。

当前主机已于 2026-08-14 完成第 1–6 步并启用三个 user timers，14 天 shadow
验收窗口由此开始；外部 IM/邮件投递仍保持关闭。

## 回退

- `systemctl --user disable --now financial-calendar-{day,week,month}.timer`，不删除 `runtime/`。
- 保留最后有效 snapshot；可用 `--no-fetch` 生成带陈旧警示的历史简报。
- DeliveryAdapter 失败不得修改 `events.json` 或 diff pending 状态；修复后使用相同幂等键重试。
