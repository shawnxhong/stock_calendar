"""Orchestrator.

  python scripts/run.py --tier=day|week|month
  python scripts/run.py --tier=week --no-fetch     # re-render from cached data
  python scripts/run.py --doctor                   # connectivity + config check

Idempotency: state.json records (event id, tier) already pushed. An event is
not re-pushed in the same tier unless its diff status changed — a MOVED event
counts as new content and MUST be re-pushed.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import render as render_mod  # noqa: E402
from common import (CONFIG, DATA, LOGS, load_yaml, now_utc_iso, read_json,  # noqa: E402
                    today_et, write_json)


def _run(script: str) -> int:
    print(f"[run] {script}")
    return subprocess.call([sys.executable, str(HERE / script)])


def update_delivery_state(state: dict, doc: dict, changes: list[dict],
                          tier: str, pushed_at: str) -> int:
    """Bookkeep newly deliverable events without coupling to any transport."""
    pushed = state.setdefault("pushed", {})
    changed = {c["id"] for c in changes
               if c["type"] in ("MOVED", "CANCELLED", "CONFIRMED")}
    tier_map = pushed.setdefault(tier, {})
    fresh = 0
    for ev in doc["events"]:
        prev = tier_map.get(ev["id"])
        if prev is None or ev["id"] in changed:
            tier_map[ev["id"]] = pushed_at
            fresh += 1
    state["last_run"] = {"tier": tier, "at": pushed_at}
    return fresh


def doctor() -> int:
    """Check config and reachability. Run this first in a new environment."""
    import os
    ok = True
    print("── 配置检查 ──")
    for name, required in (("FRED_API_KEY", True), ("FINNHUB_API_KEY", True)):
        present = bool(os.environ.get(name))
        print(f"  {'✅' if present else '❌'} 环境变量 {name}")
        ok &= present or not required

    ids = load_yaml("release_ids.yaml")
    print(f"  {'✅' if ids else '❌'} release_ids.yaml：{len(ids)} 条已解析"
          + ("" if ids else "  → 先运行 bootstrap_releases.py"))
    ok &= bool(ids)

    review = (load_yaml("events_review.yaml") or {}).get("needs_review") or []
    if review:
        print(f"  ⚠ events_review.yaml 有 {len(review)} 条待人工处理")

    cal = load_yaml("calendar.yaml")
    fomc = cal.get("fomc_meetings") or []
    print(f"  {'✅' if fomc else '⚠'} FOMC 日程：{len(fomc)} 场"
          + ("" if fomc else "  → calendar.yaml 中为空，A 类事件将缺失"))
    ok &= bool(fomc)

    verified_manual = [
        *[("FOMC", r.get("decision"), r) for r in fomc if r.get("verified")],
        *[("recon", r.get("date"), r) for r in (cal.get("reconstitutions") or [])
          if r.get("verified")],
        *[("private", r.get("date"), r) for r in (cal.get("private_releases") or [])
          if r.get("verified")],
    ]
    unaudited = [(kind, date) for kind, date, row in verified_manual
                 if not row.get("source") or not row.get("source_checked_at")]
    print(f"  {'✅' if not unaudited else '❌'} 已核验人工事实审计字段："
          f"{len(verified_manual) - len(unaudited)}/{len(verified_manual)} 完整")
    ok &= not unaudited
    unverified = [r for r in (cal.get("reconstitutions") or [])
                  if not r.get("verified")]
    if unverified:
        print(f"  ⚠ {len(unverified)} 条 recon 日期未核实：" +
              ", ".join(f"{r['date']}({r.get('index')})" for r in unverified))

    wl = load_yaml("watchlist.yaml")
    ncore = len(wl.get("core") or [])
    nmon = len(wl.get("monitor") or [])
    print(f"  {'✅' if ncore + nmon else '⚠'} watchlist：core {ncore} / monitor {nmon}")
    ok &= bool(ncore + nmon)

    print("── 真实源解析 ──")
    import datetime as dt
    import fetch_macro

    start = today_et()
    end = start + dt.timedelta(days=400)

    def report(label, rows, *, required=True):
        nonlocal ok
        good = rows is not None and len(rows) > 0
        count = "n/a" if rows is None else str(len(rows))
        print(f"  {'✅' if good else '❌'} {label}：{count} 条")
        if required:
            ok &= good

    # BLS may return Akamai 403 on datacenter/residential IPs. This is still a
    # failed cross-check and must remain visible, but FRED is the date backbone.
    report("BLS ICS（时点交叉验证）", fetch_macro.bls_schedule(), required=False)
    report("TreasuryDirect", fetch_macro.treasury_long_auctions(start, end))
    report("BEA release_dates.json", fetch_macro.bea_schedule(start, end))
    report("Census official calendar", fetch_macro.census_schedule(start, end))
    report("ISM official report calendar", fetch_macro.ism_schedule(start, end))
    report("ADP official NER calendar", fetch_macro.adp_schedule(start, end))

    fred_key = os.environ.get("FRED_API_KEY")
    if fred_key:
        from common import http_get
        fred = http_get("https://api.stlouisfed.org/fred/releases", {
            "api_key": fred_key, "file_type": "json", "limit": 1,
        }, retries=1)
        good = isinstance(fred, dict) and bool(fred.get("releases"))
        print(f"  {'✅' if good else '❌'} FRED 认证与 schema")
        ok &= good
    else:
        print("  ⏭ FRED：缺少 FRED_API_KEY，未联网测试")

    finnhub_key = os.environ.get("FINNHUB_API_KEY")
    if finnhub_key:
        from fetch_earnings import finnhub_earnings
        rows = finnhub_earnings(finnhub_key, start, start + dt.timedelta(days=30), set())
        good = rows is not None
        print(f"  {'✅' if good else '❌'} Finnhub 认证与 schema")
        ok &= good
    else:
        print("  ⏭ Finnhub：缺少 FINNHUB_API_KEY，未联网测试")

    # Connectivity/schema probe only. AAPL is not added to the user's watchlist.
    from fetch_earnings import yfinance_earnings
    yf_rows = yfinance_earnings(["AAPL"])
    yf_good = bool(yf_rows.get("AAPL"))
    print(f"  {'✅' if yf_good else '❌'} yfinance schema（AAPL 诊断，不写入 watchlist）")
    ok &= yf_good
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=["day", "week", "month"])
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--doctor", action="store_true")
    args = ap.parse_args()

    if args.doctor:
        return doctor()
    if not args.tier:
        ap.error("--tier is required (or use --doctor)")

    if not args.no_fetch:
        for script in ("fetch_macro.py", "fetch_earnings.py"):
            if _run(script) != 0:
                print(f"[warn] {script} 失败 —— 继续，将使用上次快照渲染")
        if _run("normalize.py") != 0:
            print("[fatal] normalize 失败")
            return 3
        _run("diff_engine.py")

    doc = read_json(DATA / "events.json")
    if not doc:
        print("[fatal] events.json 缺失")
        return 3

    long_txt = render_mod.render(args.tier, short=False)
    short_txt = render_mod.render(args.tier, short=True)

    out = LOGS / f"{today_et().isoformat()}-{args.tier}.md"
    out.write_text(long_txt, encoding="utf-8")
    out_s = LOGS / f"{today_et().isoformat()}-{args.tier}-short.md"
    out_s.write_text(short_txt, encoding="utf-8")

    # Idempotency bookkeeping.
    state = read_json(DATA / "state.json", {}) or {}
    pushed_at = now_utc_iso()
    fresh = update_delivery_state(
        state, doc, (read_json(DATA / "changes.json") or {}).get("changes", []),
        args.tier, pushed_at)
    write_json(DATA / "state.json", state)

    print(f"[ok] {out}")
    print(f"[ok] {out_s}")
    print(f"[ok] 本次新增/更新事件 {fresh} 条")
    print("\n" + short_txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
