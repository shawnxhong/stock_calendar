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
    unverified = [r for r in (cal.get("reconstitutions") or [])
                  if not r.get("verified")]
    if unverified:
        print(f"  ⚠ {len(unverified)} 条 recon 日期未核实：" +
              ", ".join(f"{r['date']}({r.get('index')})" for r in unverified))

    wl = load_yaml("watchlist.yaml")
    ncore = len(wl.get("core") or [])
    nmon = len(wl.get("monitor") or [])
    print(f"  {'✅' if ncore + nmon else '⚠'} watchlist：core {ncore} / monitor {nmon}")

    print("── 连通性 ──")
    from common import http_get
    probes = [
        ("FRED", "https://api.stlouisfed.org/fred/releases",
         {"api_key": os.environ.get("FRED_API_KEY", ""), "file_type": "json", "limit": 1}),
        ("BLS ICS", "https://www.bls.gov/schedule/news_release/bls.ics", None),
        ("TreasuryDirect", "https://www.treasurydirect.gov/TA_WS/securities/announced",
         {"format": "json", "type": "Bond"}),
    ]
    for label, url, params in probes:
        as_json = label != "BLS ICS"
        r = http_get(url, params, retries=1, as_json=as_json)
        print(f"  {'✅' if r is not None else '❌'} {label}")
        ok &= r is not None
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
    pushed = state.setdefault("pushed", {})
    changed = {c["id"] for c in (read_json(DATA / "changes.json") or {}).get("changes", [])
               if c["type"] in ("MOVED", "CANCELLED", "CONFIRMED")}
    tier_map = pushed.setdefault(args.tier, {})
    fresh = 0
    for ev in doc["events"]:
        prev = tier_map.get(ev["id"])
        if prev is None or ev["id"] in changed:
            tier_map[ev["id"]] = now_utc_iso()
            fresh += 1
    state["last_run"] = {"tier": args.tier, "at": now_utc_iso()}
    write_json(DATA / "state.json", state)

    print(f"[ok] {out}")
    print(f"[ok] {out_s}")
    print(f"[ok] 本次新增/更新事件 {fresh} 条")
    print("\n" + short_txt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
