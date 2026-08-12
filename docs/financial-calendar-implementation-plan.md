# 财经日历 Skill 本地实施计划

状态：执行中  
开始日期：2026-08-12  
设计基线：[financial-calendar-design-v1.0.md](financial-calendar-design-v1.0.md)  
代码基线：`stock-calender.zip`（Claude 生成的 1771 行骨架，原件保留不改）

## 实施原则

- 本机 Hermes agent 仅用于开发、调试和人工触发，不作为最终生产环境。
- 核心程序保持为可移植的 `fetch → normalize → diff → render` 流水线。
- 密钥只从环境变量或未入库的 `.env` 读取，不进入配置、日志、快照或提交历史。
- 官方日期事实必须可追溯；缺失数据不得解释为零、照旧或没有事件。
- 离线测试、真实联网验证、14 天 shadow run 和生产部署是四道独立闸门。
- 未通过设计文档验收标准 1、4、6 中任一项，不上线。

## 已知验证边界

### Claude 已在其容器中实测

- 机械日历日期数学：第三个周五、周末回退、跨季度锚点、witching 与 S&P 再平衡、ID 去重。
- diff 序列：`MOVED`、`CONFIRMED`、`STALE → CANCELLED`、取消后静默。
- BLS 静态时点冲突检测。
- 月/周/日 × 长/短版渲染，短版不超过 15 行。
- `estimated` 财报不进入确定事件块。
- 缺少 API key 时的降级行为。

### 尚未验证

- FRED、BLS、Finnhub、TreasuryDirect、BEA、Census 的真实联网行为。
- 本机环境中的完整回归结果。
- 人工日历事实、watchlist 和财报 IR 确认流程。
- 14 天并行验收。
- 最终生产调度、密钥、状态存储和投递适配。

## 执行阶段

### P0 — 工程与测试基线（已完成）

- [x] 将 ZIP 整理为标准 skill 目录：`SKILL.md`、`agents/`、`config/`、`scripts/`、`references/`、`data/`、`logs/`、`tests/`。
- [x] 保留原始 ZIP；建立文件清单与 SHA-256 基线。
- [x] 添加 `agents/openai.yaml`。
- [x] 添加 `.gitignore`、`.env.example` 和依赖声明。
- [x] 将 Claude 已验证的行为固化为本机自动化测试。
- [x] 运行 Python 编译检查和 skill `quick_validate.py`。
- [ ] 初始化本地 Git 仓库并提交可追溯基线（计划在 P1 首次修复前完成）。

验收：离线、不带密钥即可完成编译、skill 校验和确定性测试。

### P1 — 关键正确性修复（已完成）

- [x] 用真实美股交易日历替代仅排除周末的月末/季末计算。
- [x] 解耦 FOMC 决议、SEP、发布会和纪要建模。
- [x] 将“两供应商一致”与“公司 IR confirmed”分开。
- [x] 增加不会被 normalize 覆盖的 enrichment/override 层。
- [x] 源整体失败时沿用上一份有效事件并显式标陈旧。
- [x] 修正重复宏观发布与真正 `MOVED` 的身份配对。
- [x] 将政策类 `manual_events` 规范化为 `policy`。
- [x] 补齐 BEA/Census 官方交叉验证与补缺层。

验收：每个修复均有回归测试；失败场景不产生静默缺失。

### P2 — 本地密钥与真实联网验证（执行中）

- [ ] 由 operator 在本机创建未入库 `.env`，设置 `FRED_API_KEY`、`FINNHUB_API_KEY`。
- [x] 扩充 `--doctor`：认证、schema、未来日期、合理事件数、陈旧度检查。
- [ ] 单独验证 FRED、BLS ICS、TreasuryDirect、Finnhub、yfinance、BEA、Census、ISM、ADP。
- [ ] 完成一次真实全链路运行和断网降级运行。
- [ ] 保存脱敏的验证证据，不记录密钥。

验收：每个源有成功证据；空响应、认证失败、超时和无事件可区分。

### P3 — 人工权威配置与审计（执行中）

- [ ] 运行 bootstrap，处理 `events_review.yaml`，抽查 5 条 release ID。
- [x] 从 Federal Reserve 官方页面录入 FOMC 日程。
- [x] 从官方来源录入 ISM、密歇根、ADP。
- [x] 核实 Russell 2026-06-26；将 2026-11-20 保持为显式未核实占位符。
- [ ] 填写 `watchlist.yaml`。
- [x] 建立财报 IR 确认和共识/nowcast 的审计字段。

验收：所有人工事实含来源、抓取时间和核实状态；未核实条目不会伪装成 confirmed。

### P4 — 故障演练与完整本地回归（已完成）

- [x] 覆盖 DST、交易所假日、连续宏观发布、改期/改时点、重复运行、状态恢复。
- [x] 覆盖单源失败、全源失败、空响应、超时和财报分歧。
- [x] 验证月/周/日幂等、`--no-fetch` 和短版 15 行上限。

验收：测试自动化；所有故障均有明确退出码、横幅和日志。

### P5 — 14 天 Shadow Run

- [ ] 每日/每周/月度按计划生成简报，与官方源及 TE 人工对照。
- [ ] 记录 A 类遗漏、错日期、错时点和误告警。
- [ ] 抽查 confirmed 财报与全部共识来源。
- [ ] 验证真实或构造的 `MOVED`/`CONFIRMED`。

验收：满足设计文档 §14 全部六项标准。

### P6 — 生产移植

- [x] 抽象 `Scheduler`、`SecretProvider`、`StateStore`、`DeliveryAdapter`、`HealthReporter`。
- [ ] 生产环境重新执行 doctor、dry-run 和影子运行。
- [ ] 配置真实投递和回退方案。

验收：核心代码不依赖 Hermes API、目录或消息格式；生产 secrets 不复用本地文件。

## 执行日志

### 2026-08-12

- 用户批准计划并授权开始实施。
- 确认当前工作区尚不是 Git 仓库。
- 开始 P0：标准目录、基线测试与 skill 校验。
- 原始 ZIP SHA-256：`7a1f8f95aa8e0ad2c4bb8ed58913236224465e3575a9f34f2861d5f08b01ea53`。
- 整理后的 16 个 Claude 业务文件与 ZIP 解包原件逐一 SHA-256 一致。
- Python 编译检查通过；skill `quick_validate.py` 通过。
- 本机离线基线测试 10/10 通过。首次运行有 1 条测试预期写错，核算后确认代码正确并修正测试：2026-08-12 最近季度末为 2026-06-30。
- 创建基线提交 `e533819`（`chore: establish financial calendar baseline`）。
- P1 新增标准库 NYSE 交易日历，处理常规整日休市和 Good Friday OPEX 回退；非常规休市走人工审计配置。
- FOMC 发布会不再与 SEP 条件绑定；政策人工事件归类为 `policy`。
- 两供应商一致只记 `vendor_corroboration: agreed`；公司 IR confirmed 必须经 `event_overrides.yaml` 保存来源和抓取时间。
- 显式失败的源从上一份快照结转未来事件，并保留旧抓取时间与陈旧注记。
- diff 不再把已发生的上期宏观数据与新一期错误配对为 `MOVED`。
- 接入 BEA 官方 `release_dates.json` 与 Census 官方 Economic Indicator Calendar；只允许精确 `official_match` 白名单补缺。
- P1 离线回归 21/21 通过；Python 编译与 skill 校验通过。
- 创建 P1 提交 `56ca2d7`（`fix: harden calendar correctness and source validation`）。
- 使用 `uv` 创建 repo 内 `.venv` 并安装锁定依赖；pandas/numpy 仅为 yfinance 传递依赖，核心代码不直接使用。
- 隔离环境回归 22/22 通过。
- 2026-08-12 真实公开源测试：TreasuryDirect 2 条、BEA 28 条、Census 65 条，schema 与未来窗口检查通过。
- BLS ICS 由官方 Akamai 返回 403，浏览器 UA 亦无效；按安全策略不绕过，保留失败告警并继续以 FRED + 静态时点运行。
- FRED/Finnhub 未测试：本机 `FRED_API_KEY`、`FINNHUB_API_KEY` 与 `.env` 均未配置。
- 补齐开发依赖中的 pytest；本机隔离环境回归现为 29/29 通过。
- 从 Fed 官方页面核验 2026 年 8 场 FOMC 决议；仅录入官方已公布的纪要日期，未来纪要不推算。
- 从密歇根大学官方 2026 PDF 录入 8 月 14 日至 12 月 18 日的初值/终值日期。
- 从 FTSE Russell 官方页面核验 2026-06-26 美股收盘后生效；11 月日期尚无官方公告，保留 `verified: false`。
- 新增 ISM 官方 HTML 日历解析（未来 8 条）与 ADP 官网 `ner_production.json` 解析（未来 4 条），并排除 weekly NER pulse。
- 所有 19 条 `verified: true` 人工事实已具备 `source` 与 `source_checked_at`，doctor 审计通过。
- `.env` 现在由程序自动从 repo 根目录加载，且不会覆盖生产环境已注入的同名变量。
- P4 回归扩展到 44/44：DST 春秋切换、北京跨日、时点变更、pending 状态恢复、
  FRED 失败不增 miss、ISM carry-forward、critical 横幅、>3 天降级、重复运行幂等均通过。
- yfinance 对 AAPL 的真实 schema 诊断通过（仅 doctor 探针，不写入用户 watchlist）。
- 无 FRED key 的真实部分降级全链路成功：FRED 标 critical，但仍获取 Treasury 2、BEA 28、
  Census 65、ISM 8、ADP 4 条，并保留人工 FOMC/密歇根事件。
- 修正实跑发现的两个边界：人工历史事件按 fetch window 过滤；短版截断不再留下孤立日期标题。
- 同一周度缓存任务连续运行，第二次报告 `本次新增/更新事件 0 条`；幂等实证通过。
- 新增生产端口协议与本地参考适配器；`FINCAL_DATA_DIR` / `FINCAL_LOG_DIR` 可指向生产持久卷。
- 新增 [production-migration.md](production-migration.md)，明确 secret、状态、调度、投递、健康告警与回退边界。
- P6 抽象层回归后总测试数 46/46；未在 Hermes 上启用任何生产调度或外部投递。
