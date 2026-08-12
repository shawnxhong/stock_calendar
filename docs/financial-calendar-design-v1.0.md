# 财经日历 skill — 设计文档 v1.0（定版，可实施）

> **定版说明**：本 skill 只回答"未来一段时间会发生什么"，不回答"规模多大、往哪个方向"。
> 相对 v0.2 删除：M4 机械资金流状态模块、价格拉取、CTA/vol-control/杠杆 ETF 引擎、
> 月末养老金方向估计、回测验证闸门、pandas/numpy 依赖。机械资金流能力已由 EEI 承接。
> 全部开放项已关闭，无待决事项。

## 0. 定位与非目标

**做什么**：把"美国宏观事件 + watchlist 财报 + 机械日历"的日程，从"你去 TE 主动查"翻转为"按月/周/日主动推给你"；并在日期发生变更时立即告警。

**做到什么程度就停**：输出是 **heads-up**——某日会发生什么、几点、有多重要、日期确不确定。**不估计事件的量级，不判断方向。**

**明确排除**：
- 非美宏观（欧日中数据与政策会议）
- 非美上市财报。watchlist 仅美国上市代码；中概 ADR 与普通美股同源同处理
- 事件解读、方向判断、交易建议
- 完整卖方共识数据库（只对每周 Top 3–5 事件由 agent 检索补充）
- 爬虫。任何依赖 Investing.com / TE / ForexFactory 页面结构的实现一律拒绝
- **一切资金流量级与方向估计**：CTA 持仓与 flip levels、vol-control 去杠杆规模、杠杆 ETF 收盘再平衡流量、养老金月末再平衡方向、dealer gamma/GEX、个股资金流归因

**核心原则**：日期是事实，必须来自官方源且可追溯；置信度必须显式；日历变更优先于日历内容。

---

## 1. 关于删除 M4

同意删除，理由比"太重"更具体：M4 与本 skill 的**失败模式不兼容**。

日历系统的失败是"漏了一个日期"——可检测、可对照、可修复。流量估计的失败是"数字看着合理但错了 50%"——不可检测，而且错误会被简报的权威版面放大。把两者放进同一个推送通道，等于让一个需要签署验证书才能上线的模块，搭一个只需人工对照两周就能上线的模块的便车。

删除后本 skill 的依赖从 `requests pyyaml icalendar yfinance finnhub-python pandas numpy` 缩到 `requests pyyaml icalendar yfinance finnhub-python`，日常失败点少一个（价格拉取），且没有任何模块需要历史验证才能上线。

**flows 代码的去向已确认**：CTA/vol-control 复制、flip level 求解、回测验证协议（V1: 对 CFTC 资产管理人净头寸 ρ ≥ 0.40；V2: flip 突破日前瞻 3 日波动，n ≥ 15、bootstrap p < 0.10）已由 EEI 吸收，不随本次删除而丢失。本 skill 与 EEI 因此形成清晰分工：**本 skill 说"什么时候会发生什么"，EEI 说"发生了什么、有多大、怎么应对"。** 两者不共享代码、不互相 import；若日后需要联动，走 EEI 既有的 JSON state contract 模式，而非跨 skill 调用。

### 一条边界线的移动

原 M3 里的**月末养老金再平衡方向估计**按你的标准属于 M4（它回答"往什么方向"，且需要价格数据），**一并删除**。

删除后 M3 成为纯粹的日期数学：零网络依赖、零参数、零误差。这是个干净的边界——M3 里剩下的每一项都能用"这天会发生 X"一句话说完。

**回购静默期强度曲线：保留**（已确认）。它是相对季末的静态季节插值，无价格数据、无 AUM 先验，输出为强度百分比。它是 M3 中唯一非"事件"形态的输出，因此在渲染上必须写成日历语气（"本月上半月企业回购受限，约 X% 市值处于静默期"），而不是流量语气。曲线本身是静态近似而非数据，`calendar.yaml` 中的注释须原样保留这一声明。

---

## 2. 架构

```
拉取层
  ├── 骨：官方源（零脆弱）
  │     FRED releases/dates · BLS bls.ics · BEA/Census 日程
  │     FOMC 页 · TreasuryDirect 拍卖
  ├── 肉：财报（半可靠，带置信标记）
  │     Finnhub earnings calendar / yfinance 交叉校验
  └── 补：人工 YAML
        ISM/密歇根/ADP · 政策事件 · Russell recon
                    ↓
规范化层   events.json（统一 schema，UTC 存储）
                    ↓
        ┌───────────┼───────────┬───────────┐
    diff 引擎    M3 机械日历   分级打标    共振检测
   （快照对比）   （日期数学）   A/B/C     （同日叠加）
        └───────────┴───────────┴───────────┘
                    ↓
渲染层   月度全景 / 周度清单 / 日度 T-1
         每档双渲染：短版（IM ≤15 行）+ 长版（邮件/文件）
```

三个模块，全部自包含：

| 模块 | 内容 | 性质 | 依赖 |
|---|---|---|---|
| **M1 宏观日历** | FRED/BLS/BEA/Census/FOMC/Treasury 事件与时点 | 官方事实 | 网络 + FRED key |
| **M2 财报日历** | watchlist 财报日期 + 置信度 | 半可靠事实 | Finnhub / yfinance |
| **M3 机械日历** | OPEX、三重魔咒、月末季末、recon、回购静默期 | 纯确定性日期数学 | 无（仅 config） |

### 统一事件 schema

```yaml
- id: "fred:10:2026-08-12"        # 稳定主键，用于 diff 与幂等
  kind: macro | earnings | mechanical | policy
  label: "CPI（7月）"
  date_utc: "2026-08-12T12:30:00Z"
  tier: A | B | C
  time_confidence: exact | date_only
  date_confidence: confirmed | estimated
  source: "FRED release_id=10"
  source_fetched_at: "2026-07-30T09:00:00Z"
  prior_value: "0.3% m/m"
  consensus: null                  # 仅 Top 事件填充，必带 source
  nowcast: null                    # 模型值，单列
  notes: []
```

`date_utc` 一律存 UTC，渲染时用 `zoneinfo` 转 ET 与北京时间**双列并排**。禁止手写时区偏移——美国夏令时切换会静默错 1 小时，且错的方向恰好让你晚一小时看盘。

---

## 3. M1 宏观日历

### 数据源

| 源 | 覆盖 | 坑 |
|---|---|---|
| FRED `fred/releases/dates` | 几乎所有官方系列的发布日程 | 必须 `include_release_dates_with_no_data=true`，否则只返回历史日期 |
| BLS `bls.gov/schedule/news_release/bls.ics` | 劳工部全部发布，带精确时点 | ICS 解析，`icalendar` |
| BEA / Census 日程页 | GDP/PCE/贸易/耐用品 | HTML，与 FRED 交叉验证 |
| federalreserve.gov FOMC calendar | 会议、纪要、SEP、发布会 | 一年前公布，年度人工确认 |
| TreasuryDirect `TA_WS/securities/announced` | 国债拍卖 | JSON，无需 key |

### 分级白名单（已确认）

**A 类（可能改写 reaction function）**：FOMC 决议 + 点阵图/SEP + 发布会、FOMC 纪要、CPI、Nonfarm Payrolls、Core PCE、10y/30y 拍卖、Jackson Hole、主席国会作证

**B 类（稳态期定价输入）**：PPI、零售销售、ISM 制造/服务、JOLTS、初请失业金、GDP、密歇根信心、工业产出、耐用品、Beige Book、watchlist 财报

**C 类（噪音，仅长版）**：二级数据、修正值、区域联储调查、消费者信贷、贸易数据

> 初请失业金定为 B：稳态期是噪音，反应函数重写期是最高频劳动力读数。随 regime 动态调级需引入 regime 判定，本期不做。

### release_id 的取得（agent 可自动接受高置信匹配）

```
scripts/bootstrap_releases.py
  → GET fred/releases（全量约 300 条）
  → 与白名单名称匹配并计算置信度
  → 高置信（精确 / 唯一强匹配）自动写入 config/events.yaml
  → 模糊或缺失写入 config/events_review.yaml（ambiguous / not_found）
  → 输出 logs/bootstrap_report.md，列出每条自动决策供事后抽查
```

自动接受的前提是**决策可审计**：每条匹配须留下"白名单名称 → FRED 名称 → release_id → 匹配依据"。`not_found`（ISM、密歇根、ADP 等私营发布）落入人工 YAML 层。

### 发布时点静态表（ET）

FRED 只返回日期不返回时点，时点来自静态表：

| 时点 | 发布 |
|---|---|
| 08:30 | CPI, PPI, NFP, PCE, GDP, 零售, 初请, 耐用品 |
| 09:15 | 工业产出 |
| 10:00 | ISM, JOLTS, 密歇根, 成屋销售 |
| 14:00 | FOMC 决议, 纪要 |
| 14:30 | FOMC 发布会 |

时点与日期分离的副产品：若 BLS 改了时点，静态表与 ICS 冲突，**冲突即告警而非静默择一**。

---

## 4. M2 财报日历

Finnhub `/calendar/earnings`（免费层，按 watchlist 过滤）为主，yfinance 逐票交叉校验。

**置信度硬约束**：
- `confirmed`：公司正式公告（供应商标记确认，或 agent 检索到 IR 新闻稿）
- `estimated`：供应商模型推算

`estimated` **不进"本周确定事件"块**，只进"可能落在本周（未确认）"。按幻觉日期布置头寸的代价远大于漏看一次财报。

两源不一致时取更保守者（更晚日期）并标注分歧。

**watchlist**：`config/watchlist.yaml`，两层——`core`（持仓，全推）与 `monitor`（观察，仅 A/B 摘要）。

---

## 5. M3 机械日历（纯日期数学）

零网络依赖、零维护，由 `mechanical_calendar.py` 计算：

- 月度 OPEX：第三个周五
- 三重魔咒：3/6/9/12 月 OPEX
- S&P 季度再平衡：生效于三重魔咒周五（自动生成）
- 月末 / 季末：最后一个交易日
- 回购静默期强度：相对季末的静态季节曲线（分段线性插值，配置于 `calendar.yaml`）

人工维护部分（`config/calendar.yaml`）：
- Russell recon 生效日。**注意：FTSE Russell 自 2026 起改为半年度（6 月 + 11 月），11 月日期需在官方公布后核实，当前为占位符——实施时作为待核实项挂着**
- MSCI 季度审核等 `manual_events`

Agent 可在你提供或要求核实时**追加**已核实日期，**绝不编造**。

---

## 6. diff 引擎（本系统最重要的功能）

从主动查询转被动接收，头号新增风险是"日历悄悄变了而你不知道"。2025 年政府停摆导致 BLS 一批发布延期即为先例。

```
data/snapshots/YYYY-MM-DD.json   ← 全量快照
data/changes.json                ← 与上一份快照的 diff
```

四类变更，**在任何一档简报中都置顶，优先级高于所有新事件**：
- `NEW` 新增
- `MOVED` 日期/时点变更（旧值 → 新值，两者都显示）
- `CANCELLED` 取消或从源中消失
- `CONFIRMED` 由 estimated 升级为 confirmed（财报常见）

**抗抖动**：源临时返回不完整数据不得判为取消。连续 2 次拉取缺失才标 `CANCELLED`；单次缺失标 `STALE` 并保留上次值。假告警会很快训练你忽略这个通道，这比漏报更难修复。

---

## 7. 共振检测

同一交易日出现 A 类宏观事件 + M3 机械事件时，该日合并为一个高亮块。

例：`8/21 周五 — Jackson Hole 主席讲话 + 8 月 OPEX 到期`

共振是渲染逻辑而非新数据，也是把机械日历放进同一系统的主要理由：两份互不知情的日历产生不了这一行。

---

## 8. 三档输出（本 skill 的全部产出）

同一份 `events.json` 渲染，**双版本**：短版（IM，硬上限 15 行）+ 长版（邮件/文件，完整）。

### 月度全景（每月第一个自然日）
- 本月 A 类事件时间轴
- 财报季形状：watchlist 财报按周分布 + 密集周标注
- 本月 M3 机械事件（OPEX、季末、recon、回购静默期演变）
- 上月变更回顾
- 短版：A 类日期 + 财报最密集周 + 季末/三重魔咒

> 第一个自然日若为周末或假日，简报仍在当日发出，内容按交易日标注。

### 周度清单（周日）
- **置顶：本周变更**（MOVED / CANCELLED / CONFIRMED）
- 按交易日排列，ET 与北京时间双列，A/B 分级，C 类折叠
- 共振日高亮
- Top 3–5 事件的 前值 / 共识 / nowcast 三栏
- 未确认财报单列"可能落在本周"
- 短版：共振日 + A 类 + core 持仓财报

### 日度 T-1（盘前）
- 今日事件（精确时点，双时区）
- 明日预告
- 昨日新增变更
- 短版即全部内容

---

## 9. 幂等与状态

`data/state.json` 记录 `(事件 id, 档位, 推送时间)`。同一事件在同一档位不重复推送，除非 diff 状态变化（`MOVED` 视为新内容，必须重推）。hermes 重试或补跑不产生重复消息。

---

## 10. Agent 角色分离

**确定性脚本产出全部日期、时点、前值、分级、diff、M3 全部输出。**

Agent 只做五件事：
1. 运行 pipeline 并检查数据健康
2. 对 Top 3–5 事件检索共识值（护栏见下）
3. 检索财报确认状态（IR 新闻稿），把 `estimated` 升级为 `confirmed`
4. 在你提供或要求核实时，向 `calendar.yaml` 追加已核实日期
5. 写 ≤3 条描述性注记（数据源异常、可疑变更）

**共识检索护栏**（本系统唯一有幻觉风险的环节）：
1. 必须带 `source` 与 `source_fetched_at`，无来源的数字不得写入
2. 查不到写"无共识数据"，**禁止凭记忆填数**
3. nowcast（Cleveland Fed 通胀 nowcast、Atlanta Fed GDPNow）单列独立栏位，标签写明"模型 nowcast，非卖方共识"
4. 禁止把 nowcast 填入 `consensus` 字段
5. `prior_value` 来自 FRED 最新观测（脚本产出，非 agent 填写）

**Agent 禁止**：判断事件重要性 / 修改分级 / 预测数据结果或市场方向 / 给交易建议 / 编造任何日期或数字 / 估计任何资金流量级或方向 / 在源不可用时用记忆填补。

**缺失数据永远不等于零，也不等于"照旧"。**

---

## 11. 目录结构与依赖

```
financial-calendar/
  SKILL.md
  config/
    events.yaml          # A/B/C 白名单 + release_id + 发布时点
    events_review.yaml   # bootstrap 的 ambiguous / not_found
    watchlist.yaml       # core / monitor 两层
    calendar.yaml        # recon、manual_events、回购静默期曲线
    settings.yaml        # 时区、档位开关、短版行数、Top-N
  scripts/
    bootstrap_releases.py    # 一次性：发现 FRED release_id
    fetch_macro.py           # FRED + BLS ICS + BEA/Census + Treasury
    fetch_earnings.py        # Finnhub + yfinance 交叉校验
    mechanical_calendar.py   # M3 纯日期数学
    normalize.py             # → events.json
    diff_engine.py
    resonance.py
    render.py                # 三档 × 短/长版
    run.py --tier=month|week|day
  data/
    events.json, snapshots/, changes.json, state.json
  logs/
    YYYY-MM-DD-<tier>.md, bootstrap_report.md
  references/
    sources.md       # 每个源的 endpoint、坑、失效判断方式
```

**依赖**：`requests pyyaml icalendar yfinance finnhub-python`（`zoneinfo` 为标准库）
**密钥**：FRED API key（必需，免费）、Finnhub key（免费层）

---

## 12. 已定版的决策

| 项 | 决定 |
|---|---|
| 覆盖范围 | 美国宏观 + 美股 watchlist 财报 |
| 共识预期 | 仅每周 Top 3–5 事件由 agent 检索 |
| 推送节奏 | 月 + 周 + 日 三档全要 |
| 财报标的 | 仅美国上市 |
| 推送形态 | IM 短版 + 邮件长版，双版本渲染 |
| 时区 | ET 与北京时间双列并排 |
| A/B/C 分级 | 采用 §3 白名单 |
| bootstrap 匹配 | agent 自动接受高置信匹配 + 可审计报告 |
| 月度推送时点 | 每月第一个自然日 |
| 短版行数 | 15 行上限 |
| 资金流估计 | **不做**，独立立项 |

---

## 13. 数据健康与失败处理

| 情况 | 处理 |
|---|---|
| FRED 不可用 | 用上次快照渲染，顶部标注"数据陈旧 N 天"，不静默 |
| 某源缺失单个事件 | 标 `STALE` 保留上次值，连续 2 次才判 `CANCELLED` |
| 静态时点表与 BLS ICS 冲突 | 告警并同时显示两值，不自动择一 |
| 财报两源不一致 | 取更保守者（更晚日期），标注分歧 |
| 陈旧 > 3 天 | 降级为"仅历史快照"，A 类事件仍推送但打警示标 |

---

## 14. 验收标准

上线前两周并行验证：

1. **零遗漏**：A 类事件与 TE 页面人工对照，14 天内 0 遗漏、0 错日期、0 错时点
2. **变更捕获**：至少捕获 1 次真实 `MOVED` 或 `CONFIRMED`；若两周零变更，人工构造快照测试 diff 引擎
3. **置信度正确**：所有 `confirmed` 财报可追溯至公司公告，抽查 3 个
4. **无幻觉**：抽查全部共识数字，来源链接可打开且数字一致
5. **短版可读**：IM 短版手机端不折叠、不超 15 行
6. **bootstrap 可审计**：抽查 5 条自动匹配的 release_id，实际拉取的发布日期与官方一致

未通过 1、4、6 任一 → 不上线。
第 4 条失败的处置是**关闭共识检索功能**，保留纯日历——纯日历本身已完成 80% 的价值。

---

## 15. 明确划归未来独立项目（不在本 skill）

- **机械资金流系统**：CTA 复制与 flip levels、vol-control 情景网格、杠杆 ETF 收盘再平衡、月末养老金方向 —— **已归入 EEI**，不在本 skill 范围内
- dealer gamma/GEX、个股资金流归因（原 flows skill 的 Phase 2/3，去向由 EEI 决定）
- 非美宏观日历（欧央行、日银、中国数据与政策会议）
- 事件实际值回填 + "预期差"记录，积累自有的事件反应统计
- 财报隐含波动 / 预期波动幅度（需期权数据）
