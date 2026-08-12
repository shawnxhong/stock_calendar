# Financial Calendar

一个面向美国宏观事件与美股 watchlist 财报的被动推送财经日历。系统通过确定性的
`fetch → normalize → diff → render` 流水线生成月度、周度和日度简报，并把改期、
取消和确认状态置于输出顶部。

本项目只回答“什么时候发生什么”，不预测结果、市场方向或资金流规模。

## 主要能力

- 聚合 FRED、BLS、TreasuryDirect、BEA、Census、ISM、ADP 等日程源。
- 拉取 Finnhub 财报日历，并用 yfinance 交叉验证。
- 生成 OPEX、三重魔咒、交易月末/季末和指数再平衡等机械事件。
- 保存快照并检测 `NEW`、`MOVED`、`STALE`、`CANCELLED`、`CONFIRMED`。
- 输出 ET 与北京时间双时区的长版邮件和不超过 15 行的 IM 短版。
- 数据源失败时沿用有效快照并显示显著警报，避免静默缺失。

## 目录

```text
financial-calendar/
  config/       事件白名单、人工日历、watchlist、运行设置
  scripts/      抓取、归一化、diff、渲染与运行入口
  tests/        确定性与故障演练测试
  references/   数据源说明与已知限制
  data/         运行状态和快照（gitignored）
  logs/         生成的简报（gitignored）
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

## 验证

```bash
.venv/bin/python -m pytest -q financial-calendar/tests
.venv/bin/python -m compileall -q financial-calendar/scripts
python3 /home/hong/.codex/skills/.system/skill-creator/scripts/quick_validate.py financial-calendar
```

当前本地回归包含 46 个测试，覆盖日期数学、DST、源失败、快照恢复、diff、防抖、
短版上限和重复运行幂等。

## 上线边界

本机 Hermes 仅用于开发和 shadow run，不是真实生产环境。正式投递前必须完成 14 天
并行验收，并在生产环境重新运行 doctor、bootstrap 审计和 dry run。持久卷、secret
manager、调度、投递适配和回退要求见
[生产迁移清单](docs/production-migration.md)。

详细系统决策见 [设计文档](docs/financial-calendar-design-v1.0.md)，贡献约定见
[AGENTS.md](AGENTS.md)。
