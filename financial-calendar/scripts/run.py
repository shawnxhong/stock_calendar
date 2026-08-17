"""Orchestrator.

  python scripts/run.py --tier=day|week|month
  python scripts/run.py --tier=week --no-fetch     # re-render from cached data
  python scripts/run.py --doctor                   # connectivity + config check

Delivery idempotency is content-based: date + tier + rendered-content hash.
State is recorded only after the adapter succeeds.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import render as render_mod  # noqa: E402
from adapters import DirectoryDelivery  # noqa: E402
from common import (CONFIG, DATA, LOGS, atomic_write_text, load_yaml,  # noqa: E402
                    is_advisory_failure, now_utc_iso, read_json,
                    today_et, write_json)


def _run(script: str) -> int:
    print(f"[run] {script}")
    return subprocess.call([sys.executable, str(HERE / script)])


@contextlib.contextmanager
def run_lock():
    """Serialize overlapping scheduler/manual runs to protect shared state."""
    path = DATA / ".run.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def content_version(short: str, long: str) -> str:
    payload = short.encode("utf-8") + b"\0" + long.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _advisory_fingerprint(failures: list[dict]) -> list[list[str]]:
    """Stable fingerprint of advisory failures for cross-run comparison.

    Lists (not tuples) so the JSON round-trip through state.json preserves
    equality across runs.
    """
    return sorted(
        [str(f.get("source") or ""), str(f.get("reason") or "")]
        for f in failures if is_advisory_failure(f)
    )


def _apply_advisory_notify(doc: dict, state: dict) -> dict:
    """Mark advisory failures to surface in the brief only on first sight/change.

    Persistent low-impact advisories (e.g. the known BLS cross-check 403) are
    recorded in health.json every run but only surfaced in the rendered brief
    the first time they appear or when the set/reason changes — repeating the
    same warning every day trains the reader to ignore the channel.
    """
    failures = doc.get("failures") or []
    current = _advisory_fingerprint(failures)
    notify = bool(current) and current != state.get("advisory_state")
    for f in failures:
        if not is_advisory_failure(f):
            continue
        if notify:
            f["notify"] = True
        else:
            f.pop("notify", None)  # 清除上次运行残留的标志
    state["advisory_state"] = current
    return doc


def deliver_once(state: dict, adapter, *, tier: str, short: str, long: str,
                 day: str, delivered_at: str) -> tuple[bool, str]:
    """Deliver a content version once and mark state only after success."""
    version = content_version(short, long)
    provider_key = f"{day}-{tier}-{version}"
    state_key = provider_key
    delivered = state.setdefault("delivered_content", {})
    legacy_key = f"{tier}:{day}-{version}"
    if state_key in delivered or legacy_key in delivered:
        if state_key not in delivered:
            delivered[state_key] = {
                **delivered[legacy_key], "migrated_from": legacy_key,
            }
            state["last_delivery"] = {
                "key": state_key, "at": delivered[state_key].get("at"),
            }
        return False, provider_key
    adapter.deliver(tier=tier, short=short, long=long,
                    idempotency_key=provider_key)
    delivered[state_key] = {
        "tier": tier, "day": day, "version": version, "at": delivered_at,
    }
    state["last_delivery"] = {"key": state_key, "at": delivered_at}
    return True, provider_key


def _write_health(*, healthy: bool, tier: str, failures: list[dict],
                  delivery: dict, outputs: list[str]) -> None:
    degrading = [f for f in failures if not is_advisory_failure(f)]
    status = "healthy" if healthy and not degrading else (
        "degraded" if healthy else "unhealthy")
    write_json(DATA / "health.json", {
        "checked_at": now_utc_iso(), "healthy": healthy, "status": status,
        "tier": tier,
        "failure_count": len(failures), "failures": failures,
        "advisory_failure_count": len(failures) - len(degrading),
        "delivery": delivery, "outputs": outputs,
    })


def _pipeline_failure(tier: str, source: str, reason: str, code: int = 3) -> int:
    failure = {"source": source, "severity": "critical", "reason": reason}
    _write_health(
        healthy=False, tier=tier, failures=[failure],
        delivery={"configured": False, "delivered": False}, outputs=[])
    print(f"[fatal] {reason}")
    return code


def fred_config_gaps(ids: dict, macro_entries: list[dict]) -> set[str]:
    expected = {row["key"] for row in macro_entries
                if row.get("source") == "fred"}
    return expected - set(ids)


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
    macro_entries = load_yaml("events.yaml").get("macro") or []
    fred_gaps = fred_config_gaps(ids, macro_entries)
    ids_ok = bool(ids) and not fred_gaps
    print(f"  {'✅' if ids_ok else '❌'} release_ids.yaml：{len(ids)} 条已解析"
          + ("" if ids else "  → 先运行 bootstrap_releases.py"))
    if fred_gaps:
        print("  ❌ 缺少 FRED release_id：" + "、".join(sorted(fred_gaps)))
    ok &= ids_ok

    review = (load_yaml("events_review.yaml") or {}).get("needs_review") or []
    if review:
        print(f"  ⚠ events_review.yaml 有 {len(review)} 条待人工处理")
        ok = False

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
    from fetch_earnings import parse_watchlist
    try:
        core, monitor, aliases = parse_watchlist(wl)
    except (KeyError, TypeError, ValueError) as exc:
        core, monitor, aliases = [], [], {}
        print(f"  ❌ watchlist 配置无效：{exc}")
        ok = False
    else:
        print(f"  {'✅' if core or monitor else '⚠'} watchlist："
              f"core {len(core)} / monitor {len(monitor)}")
        ok &= bool(core or monitor)

    print("── 真实源解析 ──")
    import datetime as dt
    import fetch_macro

    start = today_et()
    end = start + dt.timedelta(days=400)

    def report(label, rows, *, required=True):
        nonlocal ok
        good = rows is not None and len(rows) > 0
        count = "n/a" if rows is None else str(len(rows))
        icon = "✅" if good else ("❌" if required else "ℹ")
        note = "" if good or required else "（可选交叉验证；主数据不受影响）"
        print(f"  {icon} {label}：{count} 条{note}")
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
        fh_symbols = {aliases[symbol]["finnhub"] for symbol in core + monitor}
        fh_rows = finnhub_earnings(finnhub_key, start, end, fh_symbols)
        good = fh_rows is not None
        print(f"  {'✅' if good else '❌'} Finnhub 认证与 schema")
        ok &= good
    else:
        fh_rows = {}
        print("  ⏭ Finnhub：缺少 FINNHUB_API_KEY，未联网测试")

    from fetch_earnings import yfinance_earnings
    yf_symbols = [aliases[symbol]["yfinance"] for symbol in core + monitor]
    yf_rows, yf_failures = yfinance_earnings(yf_symbols)
    covered = {
        symbol for symbol in core + monitor
        if ((fh_rows or {}).get(aliases[symbol]["finnhub"])
            or yf_rows.get(aliases[symbol]["yfinance"]))
    }
    missing = sorted(set(core + monitor) - covered)
    yf_good = not yf_failures
    print(f"  {'✅' if yf_good else '❌'} yfinance watchlist schema："
          f"{len(yf_rows)} 个 ticker")
    print(f"  {'✅' if not missing else '❌'} 财报源联合覆盖："
          f"{len(covered)}/{len(core) + len(monitor)}")
    if missing:
        print("  ❌ 无财报日期：" + "、".join(missing))
    ok &= yf_good and not missing
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier", choices=["day", "week", "month"])
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--doctor", action="store_true")
    ap.add_argument(
        "--delivery-dir",
        help="shadow/file delivery directory; omit to render without delivery")
    args = ap.parse_args()

    if args.doctor:
        return doctor()
    if not args.tier:
        ap.error("--tier is required (or use --doctor)")

    try:
        with run_lock():
            return _run_tier(args.tier, args.no_fetch, args.delivery_dir)
    except Exception as exc:  # noqa: BLE001
        return _pipeline_failure(
            args.tier, "pipeline",
            f"未处理异常 {type(exc).__name__}: {exc}")


def _run_tier(tier: str, no_fetch: bool, delivery_dir: str | None) -> int:
    started_at = now_utc_iso()

    if not no_fetch:
        fetch_failures = []
        for script, source in (("fetch_macro.py", "macro_fetch"),
                               ("fetch_earnings.py", "earnings_fetch")):
            code = _run(script)
            if code != 0:
                print(f"[warn] {script} 失败 —— 继续，将使用上次快照渲染")
                fetch_failures.append({
                    "source": source, "severity": "critical",
                    "reason": f"{script} 异常退出（code={code}），沿用旧数据",
                })
        write_json(DATA / "fetch_status.json", {
            "started_at": started_at, "failures": fetch_failures,
        })
        if _run("normalize.py") != 0:
            return _pipeline_failure(tier, "normalize", "normalize 失败")
        if _run("diff_engine.py") != 0:
            return _pipeline_failure(
                tier, "diff_engine", "diff_engine 失败；拒绝沿用旧 changes.json")

    doc = read_json(DATA / "events.json")
    if not doc:
        return _pipeline_failure(tier, "events", "events.json 缺失")

    state = read_json(DATA / "state.json", {}) or {}
    # advisory 提醒去重：简报只在首次出现/状态变化时提醒，health.json 照常记录
    write_json(DATA / "events.json", _apply_advisory_notify(doc, state))

    long_txt = render_mod.render(tier, short=False)
    short_txt = render_mod.render(tier, short=True)

    out = LOGS / f"{today_et().isoformat()}-{tier}.md"
    atomic_write_text(out, long_txt)
    out_s = LOGS / f"{today_et().isoformat()}-{tier}-short.md"
    atomic_write_text(out_s, short_txt)

    finished_at = now_utc_iso()
    state["last_run"] = {"tier": tier, "at": finished_at}
    configured_delivery = delivery_dir or os.environ.get("FINCAL_DELIVERY_DIR")
    delivery = {"configured": bool(configured_delivery), "delivered": False}
    if configured_delivery:
        adapter = DirectoryDelivery(Path(configured_delivery).expanduser().resolve())
        try:
            did_deliver, provider_key = deliver_once(
                state, adapter, tier=tier, short=short_txt, long=long_txt,
                day=today_et().isoformat(), delivered_at=finished_at)
        except Exception as exc:  # noqa: BLE001
            delivery.update({"error": f"{type(exc).__name__}: {exc}"})
            _write_health(
                healthy=False, tier=tier,
                failures=(doc.get("failures") or []) + [{
                    "source": "delivery", "severity": "critical",
                    "reason": delivery["error"],
                }],
                delivery=delivery, outputs=[str(out), str(out_s)])
            print(f"[fatal] delivery failed: {delivery['error']}")
            return 5
        delivery.update({"delivered": did_deliver,
                         "idempotency_key": provider_key})
    write_json(DATA / "state.json", state)

    print(f"[ok] {out}")
    print(f"[ok] {out_s}")
    if configured_delivery:
        action = "已写入 shadow 投递目录" if delivery["delivered"] else "内容未变化，跳过重复投递"
        print(f"[ok] {action}（{delivery['idempotency_key']}）")
    else:
        print("[info] 渲染模式（未配置 delivery）—— 仅生成报告，不写 health.json")
    failures = doc.get("failures") or []
    critical = [f for f in failures if f.get("severity") == "critical"]
    if configured_delivery:
        # Only the delivery (production) path writes health.json. The IM
        # dispatcher consumes health.json and its idempotency key; a
        # render-only run has neither, so writing one would clobber production
        # delivery state and make the dispatcher fall back to a filename key.
        _write_health(
            healthy=not critical, tier=tier, failures=failures,
            delivery=delivery, outputs=[str(out), str(out_s)])
    print("\n" + short_txt)
    return 2 if critical else 0


if __name__ == "__main__":
    raise SystemExit(main())
