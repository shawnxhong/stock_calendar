本地可自主执行部分已完成并提交，工作区干净。

  主要结果：

  - docs/financial-calendar-implementation-plan.md 已持续更新。
  - 46/46 自动化测试通过，Python 编译与标准 Skill 校验通过。
  - 真实联网验证通过：TreasuryDirect 2 条、BEA 28 条、Census 65 条、ISM 8 条、ADP 4 条、yfinance schema。
  - BLS ICS 仍被官方 Akamai 返回 403，系统会明确告警并降级。
  - FOMC、密歇根大学日期和 Russell 6 月日期已按官方来源核验；Russell 11 月占位符保持 verified: false。
  - 无 FRED key 时仍能生成包含公开源与人工源的简报，FRED 缺失会置顶红色警报。
  - 月/周/日缓存运行、状态恢复、DST、短版 15 行、重复运行幂等均已验证；第二次周度运行新增事件为 0。
  - 已建立生产端口抽象和docs/production-migration.md，但没有在 Hermes 上启用生产调度或外部投递。

  本轮提交：

  - 61a633e 官方源与审计
  - d0d61e9 故障演练与幂等
  - 669bc28 生产可移植边界
  - 1c96ed3 剩余 operator 闸门

  现在需要你完成两个本地输入：

  1. 复制 .env.example 为 .env，在本机填写 FRED_API_KEY、FINNHUB_API_KEY，不要把 key 发给我。
  2. 在 financial-calendar/config/watchlist.yaml 填写 core / monitor。

  完成后即可继续执行：

  .venv/bin/python financial-calendar/scripts/bootstrap_releases.py
  .venv/bin/python financial-calendar/scripts/run.py --doctor
  .venv/bin/python financial-calendar/scripts/run.py --tier=week

  随后还必须人工审查 bootstrap 报告中的 5 条匹配，并执行不可压缩的 14 天 shadow run；这些是上线硬闸门。