"""M3 — mechanical calendar. Pure date math. No network, no parameters, no error.

Computed here (zero maintenance):
  - monthly OPEX (third Friday)
  - quarterly triple witching (Mar/Jun/Sep/Dec OPEX)
  - S&P quarterly rebalance (effective on the triple-witching Friday)
  - month-end / quarter-end (last business day)
  - buyback blackout intensity (static seasonal curve from calendar.yaml)

From config (human-maintained): index reconstitution dates, manual events.

Scope guard: this module answers "what happens on this date". It never answers
"how big" or "which direction". Flow magnitude and direction estimation lives in
EEI, not here.
"""
from __future__ import annotations

import datetime as dt

from common import et_to_utc, load_yaml, today_et
from market_calendar import last_trading_day, previous_trading_day


# ── primitives ───────────────────────────────────────────────────────────────

def third_friday(year: int, month: int) -> dt.date:
    d = dt.date(year, month, 15)
    while d.weekday() != 4:
        d += dt.timedelta(days=1)
    return d


def last_bday(year: int, month: int) -> dt.date:
    """Backward-compatible alias: return the actual final NYSE trading day."""
    return last_trading_day(year, month)


def quarter_end_anchor(d: dt.date) -> dt.date:
    """Nearest quarter-end calendar day — x-axis origin for the blackout curve."""
    ends = []
    for y in (d.year - 1, d.year, d.year + 1):
        for m in (3, 6, 9, 12):
            ends.append(dt.date(y, 12, 31) if m == 12
                        else dt.date(y, m + 1, 1) - dt.timedelta(days=1))
    return min(ends, key=lambda q: abs((d - q).days))


def blackout_share(d: dt.date, curve: list) -> float:
    """Piecewise-linear interpolation of the static seasonal curve.

    This is an APPROXIMATION, not data. Render it in calendar language
    ("回购受限"), never as a flow estimate.
    """
    anchor = quarter_end_anchor(d)
    x = (d - anchor).days
    pts = sorted((float(p[0]), float(p[1])) for p in curve)
    if x <= pts[0][0]:
        return pts[0][1]
    if x >= pts[-1][0]:
        return pts[-1][1]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return y1
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return pts[-1][1]


def _months_between(start: dt.date, end: dt.date):
    y, m = start.year, start.month
    while dt.date(y, m, 1) <= end:
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


# ── event generation ─────────────────────────────────────────────────────────

def _mk(key: str, label: str, date: dt.date, tier: str,
        time_et: str | None, notes: list | None = None,
        kind: str = "mechanical") -> dict:
    iso, tconf = et_to_utc(date, time_et)
    return {
        "id": f"mech:{key}:{date.isoformat()}",
        "kind": kind,
        "label": label,
        "date_utc": iso,
        "tier": tier,
        "time_confidence": tconf,
        "date_confidence": "confirmed",
        "source": "mechanical_calendar (date math)",
        "source_fetched_at": None,
        "prior_value": None,
        "consensus": None,
        "nowcast": None,
        "notes": notes or [],
    }


def generate(start: dt.date | None = None, days: int = 400) -> list[dict]:
    start = start or today_et()
    end = start + dt.timedelta(days=days)
    cal = load_yaml("calendar.yaml")
    out: list[dict] = []

    for y, m in _months_between(start.replace(day=1), end):
        tf = previous_trading_day(third_friday(y, m))
        if start <= tf <= end:
            if m in (3, 6, 9, 12):
                out.append(_mk("witching", "三重魔咒到期（季度）", tf, "B", "16:00"))
                out.append(_mk("sp_rebalance", "标普指数季度再平衡生效", tf, "B", "16:00",
                               ["生效于三重魔咒收盘，自动生成"]))
            else:
                out.append(_mk("opex", "月度期权到期（OPEX）", tf, "C", "16:00"))

        lb = last_trading_day(y, m)
        if start <= lb <= end:
            if m in (3, 6, 9, 12):
                out.append(_mk("quarter_end", "季末最后一个交易日", lb, "B", "16:00"))
            else:
                out.append(_mk("month_end", "月末最后一个交易日", lb, "C", "16:00"))

    for rec in cal.get("reconstitutions") or []:
        d = dt.date.fromisoformat(str(rec["date"]))
        if not (start <= d <= end):
            continue
        verified = bool(rec.get("verified"))
        notes = [rec.get("note", "")] if rec.get("note") else []
        if not verified:
            notes.append("⚠ 日期未经核实，勿据此安排头寸")
        ev = _mk("recon", f"{rec.get('index', '指数')}成分股重构生效", d, "B", "16:00", notes)
        ev["date_confidence"] = "confirmed" if verified else "estimated"
        out.append(ev)

    for me in cal.get("manual_events") or []:
        d = dt.date.fromisoformat(str(me["date"]))
        if start <= d <= end:
            out.append(_mk("manual", str(me.get("label", "人工事件")), d,
                           str(me.get("tier", "B")), me.get("time_et"),
                           [me.get("note")] if me.get("note") else [],
                           kind="policy"))

    return out


def blackout_profile(start: dt.date | None = None, days: int = 45) -> dict:
    """Buyback blackout intensity over a window — a CONTEXT field, not an event.

    Returned as a profile the renderer states in calendar language. Deliberately
    not emitted as an event so it can never be mistaken for a flow estimate.
    """
    start = start or today_et()
    cal = load_yaml("calendar.yaml")
    curve = cal.get("buyback_blackout_curve") or []
    if not curve:
        return {}
    pts = [(start + dt.timedelta(days=i),
            blackout_share(start + dt.timedelta(days=i), curve))
           for i in range(days)]
    lo = min(pts, key=lambda p: p[1])
    hi = max(pts, key=lambda p: p[1])
    return {
        "today_share": round(pts[0][1], 3),
        "window_days": days,
        "peak": {"date": hi[0].isoformat(), "share": round(hi[1], 3)},
        "trough": {"date": lo[0].isoformat(), "share": round(lo[1], 3)},
        "disclaimer": "静态季节性近似，非数据；仅用于描述企业回购受限程度，不可作流量估计",
    }


if __name__ == "__main__":
    import json
    evs = generate()
    print(json.dumps(evs[:8], ensure_ascii=False, indent=2))
    print(f"... total {len(evs)} mechanical events")
    print(json.dumps(blackout_profile(), ensure_ascii=False, indent=2))
