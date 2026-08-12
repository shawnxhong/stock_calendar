"""M2 — watchlist earnings dates, with explicit confidence.

The hard constraint: every earnings record carries confirmed | estimated.
Vendors return model-projected dates that look identical to company-announced
ones. Placing a position against a projected date is worse than missing the
print, so `estimated` never enters the "this week, confirmed" block.

Sources: Finnhub /calendar/earnings (primary) + yfinance (cross-check).
Disagreement policy (settings.yaml): conservative = take the LATER date and
flag the disagreement. Later is safer — you prepare early rather than late.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (DATA, env_key, http_get, load_yaml, now_utc_iso,  # noqa: E402
                    settings, today_et, write_json)

FINNHUB_EARNINGS = "https://finnhub.io/api/v1/calendar/earnings"


def finnhub_earnings(api_key: str, start: dt.date, end: dt.date,
                     tickers: set[str]) -> dict | None:
    data = http_get(FINNHUB_EARNINGS, {
        "from": start.isoformat(), "to": end.isoformat(), "token": api_key,
    })
    if data is None:
        return None
    out = {}
    for row in data.get("earningsCalendar", []):
        sym = (row.get("symbol") or "").upper()
        if sym not in tickers:
            continue
        # Finnhub's `date` is the report date; `hour` is bmo/amc/dmh.
        out.setdefault(sym, []).append({
            "date": row.get("date"),
            "hour": row.get("hour"),
            "quarter": row.get("quarter"),
            "year": row.get("year"),
        })
    return out


def yfinance_earnings(tickers: list[str]) -> dict:
    """Cross-check source. Failures here are non-fatal — Finnhub is primary."""
    out: dict[str, list] = {}
    try:
        import yfinance as yf
    except ImportError:
        sys.stderr.write("[warn] yfinance not installed — skipping cross-check\n")
        return out

    for t in tickers:
        try:
            tk = yf.Ticker(t)
            cal = getattr(tk, "calendar", None)
            dates = []
            if isinstance(cal, dict):
                raw = cal.get("Earnings Date") or []
                if not isinstance(raw, list):
                    raw = [raw]
                for d in raw:
                    if hasattr(d, "isoformat"):
                        dates.append(d.isoformat()[:10])
            if dates:
                out[t.upper()] = sorted(set(dates))
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[warn] yfinance {t}: {exc}\n")
    return out


def reconcile(sym: str, fh: list[dict], yf_dates: list[str],
              policy: str) -> list[dict]:
    """Merge the two sources into records carrying explicit confidence.

    Vendor agreement is corroboration, not company confirmation. Every fetched
    record remains estimated until an audited company IR source upgrades it via
    config/event_overrides.yaml.
    """
    recs = []
    yset = set(yf_dates or [])
    for row in fh or []:
        d = row.get("date")
        if not d:
            continue
        agree = d in yset
        rec = {
            "ticker": sym, "date": d, "hour": row.get("hour"),
            "quarter": row.get("quarter"), "year": row.get("year"),
            "date_confidence": "estimated",
            "vendor_corroboration": "agreed" if agree else "single_source",
            "sources": ["finnhub"] + (["yfinance"] if agree else []),
            "disagreement": None,
        }
        if yset and not agree:
            other = sorted(yset)
            rec["disagreement"] = {"finnhub": d, "yfinance": other}
            if policy == "conservative":
                latest = max([d] + other)
                if latest != d:
                    rec["date"] = latest
                    rec["notes_hint"] = f"两源不一致，取更晚日期（Finnhub {d}）"
        recs.append(rec)

    # yfinance-only dates still surface, but never as confirmed.
    known = {r["date"] for r in recs}
    for d in sorted(yset - known):
        recs.append({
            "ticker": sym, "date": d, "hour": None, "quarter": None, "year": None,
            "date_confidence": "estimated", "sources": ["yfinance"],
            "disagreement": None,
        })
    return recs


def main() -> int:
    cfg = settings()
    wl = load_yaml("watchlist.yaml")
    core = [str(x["ticker"]).upper() for x in (wl.get("core") or [])]
    monitor = [str(x["ticker"]).upper() for x in (wl.get("monitor") or [])]
    tickers = core + monitor

    if not tickers:
        sys.stderr.write("[warn] watchlist is empty — no earnings to fetch.\n")
        write_json(DATA / "raw_earnings.json", {
            "fetched_at": now_utc_iso(), "records": [], "failures": [],
            "core": [], "monitor": []})
        return 0

    api_key = env_key("FINNHUB_API_KEY")
    start = today_et()
    end = start + dt.timedelta(days=int(cfg["fred"]["lookahead_days"]))

    failures = []
    fh = finnhub_earnings(api_key, start, end, set(tickers))
    if fh is None:
        failures.append({"source": "finnhub", "reason": "请求失败"})
        fh = {}
    yfd = yfinance_earnings(tickers)

    policy = cfg["earnings"]["disagreement_policy"]
    records = []
    for sym in tickers:
        records.extend(reconcile(sym, fh.get(sym, []), yfd.get(sym, []), policy))

    write_json(DATA / "raw_earnings.json", {
        "fetched_at": now_utc_iso(),
        "core": core, "monitor": monitor,
        "records": records, "failures": failures,
    })
    nagree = sum(1 for r in records if r.get("vendor_corroboration") == "agreed")
    print(f"[ok] earnings records={len(records)} vendor_agreed={nagree} "
          f"estimated={len(records)} failures={len(failures)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
