# 财经日历 Skill 本地实施计划

状态：执行中  
开始日期：2026-08-12  
设计基线：[financial-calendar-design-v1.0.md](financial-calendar-design-v1.0.md)  
代码基线：`stock-calender.zip`（Claude 生成的 1771 行骨架，原件保留不改）

## 实施原则

- 当前主机已改为目标生产环境；14 天验收前仅运行 shadow 文件投递。
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
- [x] 初始化本地 Git 仓库并提交可追溯基线。

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

- [x] operator 已在本机创建未入库 `.env` 并设置 `FRED_API_KEY`、`FINNHUB_API_KEY`；权限已收紧为 `0600`。
- [x] 扩充 `--doctor`：认证、schema、未来日期、合理事件数、陈旧度检查。
- [x] 单独验证 FRED、Treasury、Finnhub、yfinance、BEA、Census、ISM、ADP；BLS ICS 官方 403 显式降级。
- [x] 完成真实全链路运行；断网降级已有自动化回归。
- [x] 保存脱敏的验证证据，不记录密钥。

验收：每个源有成功证据；空响应、认证失败、超时和无事件可区分。

### P3 — 人工权威配置与审计（执行中）

- [x] 运行 bootstrap，处理 `events_review.yaml`，抽查 release ID；15 条自动接受、review 归零。
- [x] 从 Federal Reserve 官方页面录入 FOMC 日程。
- [x] 从官方来源录入 ISM、密歇根、ADP。
- [x] 核实 Russell 2026-06-26 与 LSEG 更新后的 2026-12-11。
- [x] 填写 `watchlist.yaml`（23 个 core、0 个 monitor；供应商别名保持规范 ticker）。
- [x] 建立财报 IR 确认和共识/nowcast 的审计字段。

验收：所有人工事实含来源、抓取时间和核实状态；未核实条目不会伪装成 confirmed。

### P4 — 故障演练与完整本地回归（已完成）

- [x] 覆盖 DST、交易所假日、连续宏观发布、改期/改时点、重复运行、状态恢复。
- [x] 覆盖单源失败、全源失败、空响应、超时和财报分歧。
- [x] 验证月/周/日幂等、`--no-fetch` 和短版 15 行上限。

验收：测试自动化；所有故障均有明确退出码、横幅和日志。

### P5 — 14 天 Shadow Run

- [x] 2026-08-14 在目标主机启用 day/week/month user timers，开始 shadow 验收窗口。
- [ ] 每日/每周/月度按计划生成简报，与官方源及 TE 人工对照。
- [ ] 记录 A 类遗漏、错日期、错时点和误告警。
- [ ] 抽查 confirmed 财报与全部共识来源。
- [ ] 验证真实或构造的 `MOVED`/`CONFIRMED`。

验收：满足设计文档 §14 全部六项标准。

### P6 — 生产移植

- [x] 抽象 `Scheduler`、`SecretProvider`、`StateStore`、`DeliveryAdapter`、`HealthReporter`。
- [x] 生产环境 doctor 与真实 watchlist 的 month/week/day shadow dry-run 已完成。
- [x] 增加内容幂等、成功后落状态、并发锁、原子写入、health.json 与 systemd user timers。
- [x] 试运行投递已配置（飞书+微信短版 + 幂等账本 + health 门控，2026-08-15）；正式全量多渠道待 14 天验收通过。

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

### 2026-08-14（Codex 接管）

- 在目标主机创建 Python 3.13.12 `.venv`，按锁文件安装依赖；`.env` 权限收紧为 `0600`。
- 首次沙箱联网失败暴露 `requests` 异常可能记录查询密钥；已脱敏并增加回归测试，建议 operator 轮换 FRED key。
- bootstrap 获取 331 条 FRED release；调整源归属后 15 条自动接受、review 归零。
- 修正 20 年期拍卖误纳；接入财政部未来六个月 tentative XML，正式 announced 记录覆盖 tentative。
- 耐用品改用 Census 官方年历；褐皮书补入 Fed 官方 2026 日期；Russell 按 LSEG 最新说明改为 2026-12-11。
- 实现原子写入、并发锁、fetch/diff 失败传播、critical 非零退出、`health.json`、内容哈希幂等和成功后投递记账。
- 真实源验证：FRED 15、Treasury 10、BEA 28、Census 64、ISM 8、ADP 4；Finnhub/yfinance schema 通过；BLS 仍为官方 403。
- 隔离 `runtime/` 中完成 day/week/month shadow；相同周报二次运行跳过重复投递。
- systemd units 与三档纽约时区 timer 已通过 `systemd-analyze verify` 并安装启用；手动触发的真实 service 以 `status=0/SUCCESS` 完成。
- 用户提供的 23 个标的全部配置为 core；Google 规范化为 `GOOGL`，`BRK.B` 通过 yfinance 别名 `BRK-B` 取数。
- 真实 watchlist 运行获得 29 条财报记录，23/23 标的有覆盖；day/week/month 短版分别为 15/12/15 行，重复周报正确跳过投递。
- 独立前向审查后修复跨季度财报误配、yfinance 单票静默失败、旧幂等键迁移、
  `.env` shell 执行风险与重叠 timer 争锁退出；doctor 现验证真实 watchlist 联合覆盖。
- 修复后真实运行仍为 29 条财报、23/23 标的覆盖，跨季度 disagreement 由误报降为 0；
  systemd service 再次以 `Result=success` 完成，同内容日报跳过重复投递。
- 回归测试增至 65/65；编译和 Skill 校验通过。

### 2026-08-15（Hermes 接管试运行投递）

- 建立可回滚基线 commit `bef96cb`（`feat: reach shadow-run production baseline`，26 文件 +953/−182）。
- 解决 skill 命名冲突：金十 MCP 财经日历摘要改名 `jin10-financial-calendar`
  （目录 + frontmatter + 周日 cron `6ac579609040` 引用），本 skill 改名
  `us-stock-financial-calendar`（仅 frontmatter，目录保持 `financial-calendar/`）。
- 新增确定性 IM 投递适配层：`config/delivery.yaml`（飞书+微信渠道、告警通道）、
  `scripts/deliver_im.py`（读 health.json → 门控 → 逐渠道 `hermes send` → 记账）、
  `tests/test_delivery.py`（9 用例）。详见
  `.hermes/plans/2026-08-15_222604-im-delivery-adapter.md`。
- 实现「幂等键 × 渠道」独立记账 `runtime/data/im_delivery.json`；unhealthy 只发告警
  到飞书+telegram、degraded 透传 render 内置降级横幅、部分失败只重试失败渠道。
- 部署 `no_agent` cron `0fb704e6fad7`（`*/30 * * * *`，`deliver=local`，wrapper
  `deploy/hermes/fincal_deliver.sh` + `~/.hermes/scripts/fincal_deliver.sh` shim）。
- 冒烟测试：飞书+微信均真实收到；重复运行静默幂等；cron 已 resume，待周一
  systemd 产出后自动投递。
- 渲染优化：tier 标签改中文（🔴 重要 / 🟡 中等 / ⚪ 次要），月度标题同步。
- 回归 74/74；编译与 Skill 校验通过。BLS ICS 403 维持 degraded 降级，按现状不处理。
