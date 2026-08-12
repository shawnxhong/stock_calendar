# 财经日历生产迁移清单

本机 Hermes 只用于开发和 shadow run，不承载最终调度或投递。生产环境复用
`financial-calendar/scripts` 的确定性核心，通过 `ports.py` 接入平台能力。

## 运行边界

- 密钥：生产 secret manager 注入 `FRED_API_KEY`、`FINNHUB_API_KEY`；不挂载本地 `.env`。
- 持久状态：设置 `FINCAL_DATA_DIR` 到持久卷；目录必须跨重启保留
  `state.json`、`events.json` 与 `snapshots/`。
- 日志/报告：设置 `FINCAL_LOG_DIR` 到独立持久卷或日志采集目录。
- 调度：平台 scheduler 调用 `run.py --tier=day|week|month`；不要把 Hermes 任务配置复制过去。
- 投递：实现 `ports.DeliveryAdapter`；用“日期 + tier + 内容版本”作为 provider 侧幂等键。
- 健康：实现 `ports.HealthReporter`；doctor 非零、critical failure、陈旧超过 3 天均告警。

## 部署闸门

1. 在生产镜像中按 `requirements.lock` 安装依赖。
2. 挂载空的持久数据卷并运行 `run.py --doctor`。
3. 运行 bootstrap；人工处理 review 并抽查 5 条 release ID。
4. 以真实 watchlist 执行一次 month/week/day dry run，检查两种输出。
5. 生产环境重新执行 14 天 shadow run；本机结果不能替代这一步。
6. shadow 验收通过后才启用真实 DeliveryAdapter；首次投递保持人工审批。

## 回退

- 停止 scheduler，不删除持久卷。
- 保留最后有效 snapshot；可用 `--no-fetch` 生成带陈旧警示的历史简报。
- DeliveryAdapter 失败不得修改 `events.json` 或 diff pending 状态；修复后使用相同幂等键重试。
