# 数据源参考

每个源记录三件事：**怎么用、有什么坑、怎么判断它坏了**。
第三项最重要——日历系统最恶劣的失败是源静默返回空数据而系统当作"没有事件"。

---

## FRED — 官方发布日程主源

- 发现 release：`GET https://api.stlouisfed.org/fred/releases`（约 300 条，分页 limit=1000）
- 发布日期：`GET https://api.stlouisfed.org/fred/release/dates`
- 前值观测：`GET https://api.stlouisfed.org/fred/series/observations`（`sort_order=desc&limit=2`）

### 坑

1. **`include_release_dates_with_no_data=true` 必须显式传**。不传则只返回"已有数据"的
   日期，也就是过去——与前瞻日历需求恰好相反。这是本系统最容易踩且最难察觉的坑。
2. **FRED 只给日期，不给时点**。时点来自 `config/events.yaml` 的静态表。
3. `realtime_start/realtime_end` 控制的是 vintage 窗口，不是"日程窗口"，但配合
   `include_release_dates_with_no_data` 可用于取未来日期。
4. release 名称会随机构改名而变（如 Census 的报告名）。这就是 release_id 必须由
   bootstrap 发现、并留下可审计报告的原因。

### 判断它坏了

- HTTP 非 200、或 `release_dates` 为空数组 → `fetch_macro.py` 记入 `failures`
- **返回的日期全部早于今天** → 几乎可以确定是漏传了 `include_release_dates_with_no_data`
- 某个 A 类 key 连续两次没有未来日期 → 检查 release_id 是否仍然有效

---

## BLS ICS — 劳工部发布日程（时点权威）

- `https://www.bls.gov/schedule/news_release/bls.ics`，用 `icalendar` 解析 VEVENT

### 用途

BLS 的 ICS 带**精确发布时点**，是静态时点表的交叉校验源。
`normalize.py` 发现冲突时**同时显示两个值并告警**，不自动择一——静默择一会让
"BLS 改了发布时点"这类事件永远无法被发现。

### 坑

- 摘要文本格式不稳定，匹配用关键词而非精确名称
- 时区：DTSTART 可能带或不带 tzinfo，代码里统一按 ET 处理
- 只覆盖 BLS（CPI、NFP、PPI、JOLTS、初请），不覆盖 BEA/Census/Fed

### 判断它坏了

- 解析异常或 VEVENT 数量为 0 → 记入 failures
- 冲突数量突然大增 → 可能是 BLS 改版或时点表过期，需人工核对

---

## TreasuryDirect — 国债拍卖

- `https://www.treasurydirect.gov/TA_WS/securities/announced?format=json&type=Note|Bond`
- 无需 API key

### 坑

- 只取 10y / 20y / 30y。短端票据是噪音，长端拍卖的 tail 才是 A 类事件
- 增发（reopening）会重复公告同一场拍卖，需按 (date, term) 去重
- 拍卖时点通常 13:00 ET，但偶有例外；本系统按静态表处理

---

## Finnhub — 财报日历主源

- `https://finnhub.io/api/v1/calendar/earnings?from=&to=&token=`
- 免费层有速率限制，按 watchlist 过滤而非逐票查询

### 坑（最重要的一条）

**返回的日期可能是模型预估，也可能是公司公告，接口不明确区分。**
因此本系统只把两源一致记为 `vendor_corroboration: agreed`，日期仍为
`estimated`。只有 agent 找到公司 IR 公告，并在 `event_overrides.yaml` 保存
来源和抓取时间后，才能升级为 `confirmed`。

`hour` 字段：`bmo` 盘前 / `amc` 盘后 / `dmh` 盘中。缺失时不渲染时点。

---

## yfinance — 财报日期交叉校验

- `Ticker(t).calendar` 的 `Earnings Date`
- 非官方接口，随时可能变。失败不阻塞——Finnhub 是主源
- 两源不一致时取更晚日期（`settings.yaml: disagreement_policy: conservative`）：
  提前准备的代价远小于错过

---

## BEA — 官方机器可读发布日程

- `https://apps.bea.gov/API/signup/release_dates.json`
- JSON 直接提供发布名称与带时区的 UTC 时点，无需 API key。
- 用于 GDP、Personal Income and Outlays（PCE）和贸易数据的交叉验证与补缺。
- 非 dict、目标系列为空或日期解析失败时记入 `failures`，不得视为无事件。

## Census — 官方 Economic Indicator Calendar

- `https://www.census.gov/economic-indicators/calendar-listview.html`
- 官方年历表提供 Indicator、Release Date、Time 和 Period Covered。
- `indicator.xml` 是已发布数据的 RSS，不是未来日程，不能代替 calendar list view。
- HTML 解析只依赖 `table#calendar` 的语义字段；解析出零行即源失败并告警。

## 无 API 的部分（人工 YAML）

以下没有可靠的机器可读源，全部走 `config/calendar.yaml`：

| 事件 | 说明 |
|---|---|
| FOMC 会议 / 纪要 / SEP | 美联储提前约一年公布，每年核实一次。**不填则全部缺失** |
| ISM 制造/服务 PMI | 私营机构，FRED 无日程 |
| 密歇根消费者信心 | 同上（初值 + 终值两次） |
| ADP 就业 | 同上 |
| Russell / MSCI 重构 | 提前公布但需人工核实。⚠ FTSE Russell 自 2026 年起改半年度 |
| 政策事件 | Jackson Hole、国会作证、立法截止日等 |

**Agent 只能在 operator 提供或要求核实时追加，绝不编造。**

---

## 共识预期：一个结构性缺口

免费官方源里**没有卖方共识**。这不是"再找找就有"，而是结构性的——共识是数据商的商品。

本系统的处置：
- 只对每周 Top 3–5 事件由 agent 检索，必须带来源与抓取时间
- 查不到写"无共识数据"，不猜
- Cleveland Fed 通胀 nowcast 与 Atlanta Fed GDPNow 是**模型值不是共识值**，
  单列一栏并明确标签，禁止填入 `consensus` 字段

如果验收时发现共识环节出现幻觉，正确的处置是**关闭这个功能**，
而不是修 prompt——纯日历本身已完成 80% 的价值。

---

## 明确不使用的源

- **Investing.com / TradingEconomics / ForexFactory 爬虫**：页面结构随时变，
  且有反爬。`investpy` 被 Investing.com 封禁后废弃即为先例。
  日历静默失效的失败模式最恶劣——你以为被动收到了提醒，实际上没有。
- **任何需要付费才能取得完整日程的聚合器**：本系统的骨架完全来自官方源，
  聚合器提供的增量只有共识值，见上。
