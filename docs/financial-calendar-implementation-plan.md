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

### P1 — 关键正确性修复

- [ ] 用真实美股交易日历替代仅排除周末的月末/季末计算。
- [ ] 解耦 FOMC 决议、SEP、发布会和纪要建模。
- [ ] 将“两供应商一致”与“公司 IR confirmed”分开。
- [ ] 增加不会被 normalize 覆盖的 enrichment/override 层。
- [ ] 源整体失败时沿用上一份有效事件并显式标陈旧。
- [ ] 修正重复宏观发布与真正 `MOVED` 的身份配对。
- [ ] 将政策类 `manual_events` 规范化为 `policy`。
- [ ] 补齐 BEA/Census，或在代码和验收范围中显式关闭未实现覆盖。

验收：每个修复均有回归测试；失败场景不产生静默缺失。

### P2 — 本地密钥与真实联网验证

- [ ] 由 operator 在本机创建未入库 `.env`，设置 `FRED_API_KEY`、`FINNHUB_API_KEY`。
- [ ] 扩充 `--doctor`：认证、schema、未来日期、合理事件数、陈旧度检查。
- [ ] 单独验证 FRED、BLS ICS、TreasuryDirect、Finnhub、yfinance、BEA、Census。
- [ ] 完成一次真实全链路运行和断网降级运行。
- [ ] 保存脱敏的验证证据，不记录密钥。

验收：每个源有成功证据；空响应、认证失败、超时和无事件可区分。

### P3 — 人工权威配置与审计

- [ ] 运行 bootstrap，处理 `events_review.yaml`，抽查 5 条 release ID。
- [ ] 从 Federal Reserve 官方页面录入 FOMC 日程。
- [ ] 从官方来源录入 ISM、密歇根、ADP。
- [ ] 核实 Russell 2026 日期，移除或隔离占位符。
- [ ] 填写 `watchlist.yaml`。
- [ ] 建立财报 IR 确认和共识/nowcast 的审计字段。

验收：所有人工事实含来源、抓取时间和核实状态；未核实条目不会伪装成 confirmed。

### P4 — 故障演练与完整本地回归

- [ ] 覆盖 DST、交易所假日、连续宏观发布、改期/改时点、重复运行、状态恢复。
- [ ] 覆盖单源失败、全源失败、空响应、超时和财报分歧。
- [ ] 验证月/周/日幂等、`--no-fetch` 和短版 15 行上限。

验收：测试自动化；所有故障均有明确退出码、横幅和日志。

### P5 — 14 天 Shadow Run

- [ ] 每日/每周/月度按计划生成简报，与官方源及 TE 人工对照。
- [ ] 记录 A 类遗漏、错日期、错时点和误告警。
- [ ] 抽查 confirmed 财报与全部共识来源。
- [ ] 验证真实或构造的 `MOVED`/`CONFIRMED`。

验收：满足设计文档 §14 全部六项标准。

### P6 — 生产移植

- [ ] 抽象 `Scheduler`、`SecretProvider`、`StateStore`、`DeliveryAdapter`、`HealthReporter`。
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
