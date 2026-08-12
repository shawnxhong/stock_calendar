---
name: financial-calendar
description: Passive-push US financial calendar. Pulls official release schedules (FRED, BLS, TreasuryDirect), watchlist earnings dates with explicit confirmed/estimated confidence, and a pure-date-math mechanical calendar (OPEX, triple witching, month/quarter end, index reconstitution, buyback blackout), diffs them against the last snapshot to surface date changes, and renders monthly / weekly / daily briefs in both a short IM version and a long email version. Use this skill whenever the user or an agent asks to run the financial calendar, 财经日历, econ calendar, macro calendar, the monthly/weekly/daily brief, what's happening this week/month, upcoming FOMC or CPI or NFP dates, watchlist earnings dates, or to edit the calendar configs (events.yaml, watchlist.yaml, calendar.yaml, settings.yaml). Covers US macro and US-listed watchlist earnings only. It does NOT estimate flow magnitude or direction (CTA positioning, vol-control deleveraging, dealer gamma) — that lives in EEI.
---

# 财经日历 — heads-up 系统

## 这个系统回答什么（先读这段）

**只回答一个问题：未来一段时间会发生什么。** 某日会发生什么事、几点、有多重要、日期确不确定。

**不回答：规模多大、往哪个方向。** 资金流量级与方向估计属于 EEI，不属于这里。分工线是硬的：
本 skill 说"什么时候会发生什么"，EEI 说"发生了什么、有多大、怎么应对"。两者不共享代码、不互相 import。

设计的出发点是把主动查询（TE 网站）翻转为被动接收。这个翻转引入了一个新的头号风险：
**日历悄悄改了而你不知道**。因此 diff 引擎是本系统最重要的组件，变更永远置顶于所有新事件之上。

## 角色分离（护栏，不得违反）

**确定性脚本产出全部日期、时点、前值、分级、diff、机械日历。**

运行本 skill 的 agent 只做五件事：

1. **运行 pipeline** 并检查数据健康（`run.py --doctor` / `run.py --tier=...`）
2. **检索共识值**：仅对每周 Top 3–5 事件（`render.py` 会在长版列出"待补共识"清单）
3. **检索财报确认状态**：找到公司 IR 公告后，把 `estimated` 升级为 `confirmed`
4. **追加已核实日期**：仅在 operator 提供或要求核实时，写入 `config/calendar.yaml`
5. **写 ≤3 条描述性注记**：数据源异常、可疑变更。只描述，不判断

### 共识检索护栏（本系统唯一有幻觉风险的环节）

1. 必须带 `source` 与 `fetched_at`，**无来源的数字不得写入**
2. 查不到就写"无共识数据"，**禁止凭记忆填数**
3. nowcast（Cleveland Fed 通胀 nowcast、Atlanta Fed GDPNow）单列独立栏位，
   标签必须写明"模型 nowcast，非卖方共识"
4. **禁止把 nowcast 填入 `consensus` 字段**——模型值与共识值在预期差判断里不能混用
5. `prior_value` 由脚本从 FRED 观测拉取，agent 不得填写

### Agent 禁止

判断事件重要性 / 修改分级 / 预测数据结果或市场方向 / 给交易建议 / 编造任何日期或数字 /
估计任何资金流量级或方向 / 在源不可用时用记忆填补。

**缺失数据永远不等于零，也不等于"照旧"。**

## 一次性设置

```bash
pip install requests pyyaml icalendar yfinance finnhub-python

export FRED_API_KEY=...        # https://fred.stlouisfed.org/docs/api/api_key.html
export FINNHUB_API_KEY=...     # https://finnhub.io  免费层即可
# key 只放环境变量或 .env（已在 .gitignore），不要写进任何 config 文件

python scripts/bootstrap_releases.py     # 发现 FRED release_id，只需跑一次
```

然后**必须人工完成三件事**，否则 A 类事件会缺失：

1. 阅读 `logs/bootstrap_report.md`，抽查 5 条 AUTO-ACCEPT（验收标准第 6 条）
2. 处理 `config/events_review.yaml` 里的 ambiguous / not_found
3. 填写 `config/calendar.yaml`：
   - `fomc_meetings` —— **当前为空，不填则所有 FOMC 事件缺失**，按 federalreserve.gov 逐条核对
   - `reconstitutions` —— 现有条目 `verified: false`，核实后改为 true
   - `private_releases` —— ISM / 密歇根 / ADP，FRED 无覆盖，按季度补
4. 填写 `config/watchlist.yaml`（core / monitor，仅美国上市代码）

```bash
python scripts/run.py --doctor    # 检查配置与连通性，跑通后再上线
```

## 日常运行

```bash
python scripts/run.py --tier=day      # 盘前，T-1 提醒
python scripts/run.py --tier=week     # 周日
python scripts/run.py --tier=month    # 每月第一个自然日
python scripts/run.py --tier=week --no-fetch   # 用缓存数据重渲染
```

输出：`logs/YYYY-MM-DD-<tier>.md`（长版，邮件）与 `-short.md`（短版，IM，≤15 行）。

## 数据健康与失败处理

| 情况 | 系统行为 |
|---|---|
| FRED 不可用 | 用上次快照渲染，顶部标注"数据陈旧 N 天"，绝不静默 |
| 某源缺失单个事件 | 标 `STALE` 保留上次值；**连续 2 次缺失**才判 `CANCELLED` |
| 静态时点表与 BLS ICS 冲突 | 同时显示两个值并告警，不自动择一 |
| 财报两源不一致 | 取更晚日期（更保守），标注分歧 |
| 陈旧 > 3 天 | 降级为"仅历史快照"，A 类仍推送但打警示标 |

抗抖动不是可选项：假告警会很快训练你忽略这个通道，这比漏报更难修复。

## 关键约束备忘

- **FRED `release/dates` 必须带 `include_release_dates_with_no_data=true`**，
  否则只返回已有数据的历史日期——与前瞻日历的需求恰好相反
- **时区一律存 UTC，渲染时用 `zoneinfo` 转换**。禁止手写 UTC 偏移：
  夏令时切换会静默错 1 小时，且错的方向让你晚一小时看盘
- **release_id 绝不手写**。由 `bootstrap_releases.py` 从 FRED 发现并留下可审计报告。
  写错一个 id = 永久漏掉一个 A 类事件且不报错
- **`estimated` 财报不进"确定事件"块**。按幻觉日期布置头寸的代价远大于漏看一次财报
- **回购静默期曲线是静态季节性近似，不是数据**。只能用日历语气表述
  （"本月企业回购受限"），不可作流量估计

## 目录

```
config/     events.yaml(白名单/分级) release_ids.yaml(自动生成) events_review.yaml
            watchlist.yaml calendar.yaml(FOMC/recon/私营发布) settings.yaml
scripts/    bootstrap_releases fetch_macro fetch_earnings mechanical_calendar
            normalize diff_engine resonance render run
data/       events.json snapshots/ changes.json state.json
logs/       YYYY-MM-DD-<tier>[-short].md  bootstrap_report.md
references/ sources.md
```
