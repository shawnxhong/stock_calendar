# Financial Calendar

一个面向美国宏观事件与美股 watchlist 财报的被动推送财经日历。系统通过确定性的
`fetch → normalize → diff → render` 流水线生成月度、周度和日度简报，并把改期、
取消和确认状态置于输出顶部。

本项目只回答“什么时候发生什么”，不预测结果、市场方向或资金流规模。

## 主要能力

- 聚合 FRED、BLS、美国财政部半年拍卖日程、TreasuryDirect、BEA、Census、ISM、ADP 等日程源。
- 拉取 Finnhub 财报日历，并用 yfinance 交叉验证。
- 生成 OPEX、三重魔咒、交易月末/季末和指数再平衡等机械事件。
- 保存快照并检测 `NEW`、`MOVED`、`STALE`、`CANCELLED`、`CONFIRMED`。
- 输出 ET 与北京时间双时区的长版邮件和不超过 15 行的 IM 短版。
- 数据源失败时沿用有效快照并显示显著警报，避免静默缺失。
- 短版经 IM 投递层推送到飞书+微信：按 `health.json` 门控、内容幂等、逐渠道独立记账与正文存档。

## 目录

```text
financial-calendar/
  config/       事件白名单、人工日历、watchlist、投递渠道、运行设置
  scripts/      抓取、归一化、diff、渲染、IM 投递分发与运行入口
  tests/        确定性与故障演练测试
  references/   数据源说明与已知限制
  data/         运行状态和快照（gitignored）
  logs/         生成的简报（gitignored）
deploy/         systemd units 与 Hermes 投递 wrapper
docs/           设计、实施计划和生产迁移说明
```

## 本地安装

需要 Python 3.13 和 [uv](https://docs.astral.sh/uv/)：

```bash
uv venv .venv
uv pip install --python .venv/bin/python -r requirements.lock
```

复制密钥模板并只在本机填写：

```bash
cp .env.example .env
```

`.env` 需要 `FRED_API_KEY` 和 `FINNHUB_API_KEY`，且已被 Git 忽略。随后在
`financial-calendar/config/watchlist.yaml` 填写 `core` 和 `monitor` 美股代码。

## 首次配置

```bash
.venv/bin/python financial-calendar/scripts/bootstrap_releases.py
.venv/bin/python financial-calendar/scripts/run.py --doctor
```

必须阅读 `financial-calendar/logs/bootstrap_report.md`，处理
`config/events_review.yaml`，并人工抽查至少 5 条 FRED release ID。不要凭记忆填写
release ID，也不要把没有 `source` 和 `source_checked_at` 的日期标为已核验。

## 运行

```bash
.venv/bin/python financial-calendar/scripts/run.py --tier=day
.venv/bin/python financial-calendar/scripts/run.py --tier=week
.venv/bin/python financial-calendar/scripts/run.py --tier=month
.venv/bin/python financial-calendar/scripts/run.py --tier=week --no-fetch
```

结果写入 `financial-calendar/logs/YYYY-MM-DD-<tier>.md` 和对应的
`-short.md`。缺少 FRED key 时，系统仍会抓取无需 key 的官方源，但简报会明确标记
FRED 数据不完整。

本机生产 shadow 使用独立的 `runtime/` 持久目录，并启用文件投递内容幂等：

```bash
FINCAL_DATA_DIR="$PWD/runtime/data" \
FINCAL_LOG_DIR="$PWD/runtime/logs" \
FINCAL_DELIVERY_DIR="$PWD/runtime/shadow-delivery" \
.venv/bin/python financial-calendar/scripts/run.py --tier=week
```

同一天、同一 tier、同一渲染内容只投递一次；只有 adapter 成功后才写入投递状态。

### IM 投递（飞书 + 微信）

`scripts/deliver_im.py` 读取 `health.json` 与最新短版，把简报投递到
`config/delivery.yaml` 里配置的渠道（试运行：飞书 + 微信），并逐渠道记账：

```bash
.venv/bin/python financial-calendar/scripts/deliver_im.py            # 投递未完成的渠道
.venv/bin/python financial-calendar/scripts/deliver_im.py --dry-run  # 只打印，不发送
```

- 门控：`unhealthy` 只发故障告警（飞书+telegram）、`degraded` 透传 render 内置降级横幅。
- 幂等：`runtime/data/im_delivery.json` 记「幂等键 × 渠道」，部分失败只重试失败渠道。
- 存档：每条成功消息正文写入 `runtime/im-delivery/{key}-{channel}.md`。

生产调度由 Hermes `no_agent` cron 每 30 分钟轮询（wrapper 见
`deploy/hermes/fincal_deliver.sh`）。

## 验证

```bash
.venv/bin/python -m pytest -q financial-calendar/tests
.venv/bin/python -m compileall -q financial-calendar/scripts
python3 /home/hong/.codex/skills/.system/skill-creator/scripts/quick_validate.py financial-calendar
```

当前本地回归包含 75 个测试，覆盖日期数学、DST、源失败、快照恢复、diff、防抖、
短版上限、重复运行幂等和 IM 投递门控/幂等/存档。

## 上线边界

本环境现作为目标生产主机，处于 14 天并行验收（约 2026-08-28 结束）。当前已启用：

- `deploy/systemd/`：纽约时区的 day/week/month user timers 跑确定性流水线，产出简报、
  `health.json` 与 shadow 文件投递；安装脚本会先执行 doctor，空 watchlist 或配置缺口会阻止启用。
- Hermes `no_agent` cron：每 30 分钟轮询，把短版投递到飞书 + 微信（试运行渠道）。

验收通过前不扩展到全量多渠道；若日后让 Hermes 接管调度，须先关闭现有 systemd timers。
持久状态、调度、投递和回退要求见
[生产迁移清单](docs/production-migration.md)。

详细系统决策见 [设计文档](docs/financial-calendar-design-v1.0.md)，贡献约定见
[AGENTS.md](AGENTS.md)。
