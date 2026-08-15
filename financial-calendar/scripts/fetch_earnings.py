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
SAME_CYCLE_DAYS = 45


def parse_watchlist(wl: dict) -> tuple[list[str], list[str], dict[str, dict[str, str]]]:
    """Return canonical symbols and provider-specific aliases."""
    if not isinstance(wl, dict):
        raise TypeError("watchlist must be a mapping")
    core_rows = wl.get("core") or []
    monitor_rows = wl.get("monitor") or []
    if not isinstance(core_rows, list) or not isinstance(monitor_rows, list):
        raise TypeError("watchlist tiers must be lists")

    def canonical_ticker(row: dict) -> str:
        if not isinstance(row, dict):
            raise TypeError("watchlist rows must be mappings")
        symbol = str(row.get("ticker") or "").strip().upper()
        if not symbol:
            raise ValueError("watchlist ticker cannot be empty")
        return symbol

    core = [canonical_ticker(row) for row in core_rows]
    monitor = [canonical_ticker(row) for row in monitor_rows]
    canonical = core + monitor
    if len(canonical) != len(set(canonical)):
        raise ValueError("watchlist contains duplicate tickers")
    aliases = {}
    for row in core_rows + monitor_rows:
        symbol = canonical_ticker(row)
        aliases[symbol] = {
            provider: str(row.get(f"{provider}_ticker", symbol) or "").strip().upper()
            for provider in ("finnhub", "yfinance")
        }
        if not all(aliases[symbol].values()):
            raise ValueError(f"watchlist aliases cannot be empty: {symbol}")
    for provider in ("finnhub", "yfinance"):
        values = [aliases[symbol][provider] for symbol in canonical]
        if len(values) != len(set(values)):
            raise ValueError(f"watchlist contains duplicate {provider} aliases")
    return core, monitor, aliases


def finnhub_earnings(api_key: str, start: dt.date, end: dt.date,
                     tickers: set[str]) -> dict | None:
    data = http_get(FINNHUB_EARNINGS, {
        "from": start.isoformat(), "to": end.isoformat(), "token": api_key,
    })
    if data is None:
        return None
    rows = data.get("earningsCalendar") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return None
    out = {}
    for row in rows:
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


def yfinance_earnings(tickers: list[str]) -> tuple[dict, list[dict]]:
    """Cross-check source. Failures here are non-fatal — Finnhub is primary."""
    out: dict[str, list] = {}
    failures: list[dict] = []
    try:
        import yfinance as yf
    except ImportError:
        sys.stderr.write("[warn] yfinance not installed — skipping cross-check\n")
        failures.append({"source": "yfinance", "reason": "yfinance 未安装"})
        return out, failures

    for t in tickers:
        try:
            tk = yf.Ticker(t)
            cal = getattr(tk, "calendar", None)
            if not isinstance(cal, dict):
                raise ValueError("calendar response is not a mapping")
            dates = []
            raw = cal.get("Earnings Date")
            if raw is None:
                raise ValueError("calendar response is missing Earnings Date")
            if not isinstance(raw, list):
                raw = [raw]
            for d in raw:
                if hasattr(d, "isoformat"):
                    dates.append(d.isoformat()[:10])
            if not dates:
                raise ValueError("calendar response has no usable earnings date")
            out[t.upper()] = sorted(set(dates))
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[warn] yfinance {t}: {exc}\n")
            failures.append({
                "source": "yfinance", "key": t.upper(),
                "reason": f"{type(exc).__name__}: {exc}",
            })
    return out, failures


def reconcile(sym: str, fh: list[dict], yf_dates: list[str],
              policy: str) -> list[dict]:
    """Merge the two sources into records carrying explicit confidence.

    Vendor agreement is corroboration, not company confirmation. Every fetched
    record remains estimated until an audited company IR source upgrades it via
    config/event_overrides.yaml.
    """
    recs = []
    unmatched_yf = set(yf_dates or [])
    for row in fh or []:
        d = row.get("date")
        if not d:
            continue
        try:
            fh_day = dt.date.fromisoformat(d)
        except ValueError:
            continue
        candidates = []
        for other in unmatched_yf:
            try:
                distance = abs((dt.date.fromisoformat(other) - fh_day).days)
            except ValueError:
                continue
            if distance <= SAME_CYCLE_DAYS:
                candidates.append((distance, other))
        matched_yf = min(candidates)[1] if candidates else None
        agree = matched_yf == d
        rec = {
            "ticker": sym, "date": d, "hour": row.get("hour"),
            "quarter": row.get("quarter"), "year": row.get("year"),
            "date_confidence": "estimated",
            "vendor_corroboration": (
                "agreed" if agree else "disagreed" if matched_yf
                else "single_source"),
            "sources": ["finnhub"] + (["yfinance"] if matched_yf else []),
            "disagreement": None,
        }
        if matched_yf:
            unmatched_yf.remove(matched_yf)
        if matched_yf and not agree:
            rec["disagreement"] = {"finnhub": d, "yfinance": [matched_yf]}
            if policy == "conservative":
                latest = max(d, matched_yf)
                if latest != d:
                    rec["date"] = latest
                    rec["notes_hint"] = f"两源不一致，取更晚日期（Finnhub {d}）"
        recs.append(rec)

    # yfinance-only dates still surface, but never as confirmed.
    for d in sorted(unmatched_yf):
        recs.append({
            "ticker": sym, "date": d, "hour": None, "quarter": None, "year": None,
            "date_confidence": "estimated", "sources": ["yfinance"],
            "disagreement": None,
        })
    return recs


def main() -> int:
    cfg = settings()
    wl = load_yaml("watchlist.yaml")
    core, monitor, aliases = parse_watchlist(wl)
    tickers = core + monitor

    if not tickers:
        sys.stderr.write("[warn] watchlist is empty — no earnings to fetch.\n")
        write_json(DATA / "raw_earnings.json", {
            "fetched_at": now_utc_iso(), "records": [], "failures": [],
            "core": [], "monitor": []})
        return 0

    api_key = env_key("FINNHUB_API_KEY", required=False)
    if not api_key:
        write_json(DATA / "raw_earnings.json", {
            "fetched_at": now_utc_iso(), "records": [],
            "failures": [{
                "source": "finnhub", "severity": "critical",
                "reason": "FINNHUB_API_KEY 未配置；watchlist 财报日期缺失",
            }],
            "core": core, "monitor": monitor,
        })
        sys.stderr.write(
            "[warn] FINNHUB_API_KEY is missing — wrote an explicit failure snapshot.\n")
        return 0
    start = today_et()
    end = start + dt.timedelta(days=int(cfg["fred"]["lookahead_days"]))

    failures = []
    finnhub_symbols = {aliases[symbol]["finnhub"] for symbol in tickers}
    fh_raw = finnhub_earnings(api_key, start, end, finnhub_symbols)
    if fh_raw is None:
        failures.append({"source": "finnhub", "reason": "请求失败"})
        fh_raw = {}
    yfinance_symbols = [aliases[symbol]["yfinance"] for symbol in tickers]
    yfd_raw, yfinance_failures = yfinance_earnings(yfinance_symbols)
    alias_to_canonical = {
        aliases[symbol]["yfinance"]: symbol for symbol in tickers
    }
    for failure in yfinance_failures:
        provider_key = failure.get("key")
        failures.append({
            **failure,
            "key": alias_to_canonical.get(provider_key, provider_key),
        })

    policy = cfg["earnings"]["disagreement_policy"]
    records = []
    for sym in tickers:
        records.extend(reconcile(
            sym,
            fh_raw.get(aliases[sym]["finnhub"], []),
            yfd_raw.get(aliases[sym]["yfinance"], []),
            policy,
        ))

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
