"""M1 — macro calendar fetch: FRED + BLS ICS + TreasuryDirect + manual config.

Writes data/raw_macro.json. Source failures are recorded as failures, never as
"nothing scheduled" — normalize.py needs to tell the two apart.

Two rules enforced here:
  1. FRED release dates REQUIRE include_release_dates_with_no_data=true.
     Without it the endpoint returns only dates that already have data, i.e.
     the past — the exact opposite of what a forward calendar needs.
  2. FRED returns dates, not times. Times come from the static table in
     events.yaml. When the BLS ICS disagrees with that table, the conflict is
     RECORDED, not silently resolved.
"""
from __future__ import annotations

import datetime as dt
from html.parser import HTMLParser
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (DATA, env_key, http_get, load_yaml, norm, now_utc_iso,  # noqa: E402
                    settings, today_et, write_json)

FRED_RELEASE_DATES = "https://api.stlouisfed.org/fred/release/dates"
FRED_OBSERVATIONS = "https://api.stlouisfed.org/fred/series/observations"
BLS_ICS = "https://www.bls.gov/schedule/news_release/bls.ics"
TREASURY_ANNOUNCED = "https://www.treasurydirect.gov/TA_WS/securities/announced"
BEA_RELEASE_DATES = "https://apps.bea.gov/API/signup/release_dates.json"
CENSUS_CALENDAR = "https://www.census.gov/economic-indicators/calendar-listview.html"


# ── FRED ─────────────────────────────────────────────────────────────────────

def fred_release_dates(api_key: str, release_id: int,
                       start: dt.date, end: dt.date) -> list[str] | None:
    data = http_get(FRED_RELEASE_DATES, {
        "api_key": api_key, "file_type": "json",
        "release_id": release_id,
        "realtime_start": start.isoformat(),
        "realtime_end": end.isoformat(),
        # Non-negotiable: without this the endpoint returns only the past.
        "include_release_dates_with_no_data": "true",
        "sort_order": "asc", "limit": 1000,
    })
    if data is None:
        return None
    return [d["date"] for d in data.get("release_dates", [])]


def fred_prior_value(api_key: str, series_id: str, limit: int) -> dict | None:
    data = http_get(FRED_OBSERVATIONS, {
        "api_key": api_key, "file_type": "json", "series_id": series_id,
        "sort_order": "desc", "limit": limit,
    })
    if not data:
        return None
    for obs in data.get("observations", []):
        if obs.get("value") not in (None, ".", ""):
            return {"date": obs["date"], "value": obs["value"], "series": series_id}
    return None


# ── BLS ICS ──────────────────────────────────────────────────────────────────

def bls_schedule() -> list[dict] | None:
    """BLS publishes an ICS with exact release datetimes — the time authority."""
    raw = http_get(BLS_ICS, as_json=False)
    if raw is None:
        return None
    try:
        from icalendar import Calendar
        cal = Calendar.from_ical(raw)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"[warn] BLS ICS parse failed: {exc}\n")
        return None

    out = []
    for comp in cal.walk("VEVENT"):
        start = comp.get("DTSTART")
        if start is None:
            continue
        val = start.dt
        summary = str(comp.get("SUMMARY", "")).strip()
        if isinstance(val, dt.datetime):
            out.append({"summary": summary, "datetime": val.isoformat(),
                        "has_time": True})
        else:
            out.append({"summary": summary, "date": val.isoformat(),
                        "has_time": False})
    return out


# ── Treasury ─────────────────────────────────────────────────────────────────

def treasury_long_auctions(start: dt.date, end: dt.date) -> list[dict] | None:
    """10y/30y auctions. Short bills are deliberately excluded — they are noise
    for this purpose; long-end tails are the A-tier event."""
    data = http_get(TREASURY_ANNOUNCED, {"format": "json", "type": "Note"})
    bonds = http_get(TREASURY_ANNOUNCED, {"format": "json", "type": "Bond"})
    if data is None and bonds is None:
        return None
    rows = (data or []) + (bonds or [])
    out = []
    for r in rows:
        auc = (r.get("auctionDate") or "")[:10]
        if not auc:
            continue
        try:
            d = dt.date.fromisoformat(auc)
        except ValueError:
            continue
        if not (start <= d <= end):
            continue
        term = (r.get("securityTerm") or "").strip()
        if not any(t in term for t in ("10-Year", "20-Year", "30-Year")):
            continue
        out.append({"date": auc, "term": term,
                    "security_type": r.get("securityType"),
                    "cusip": r.get("cusip")})
    # De-duplicate: reopenings can announce the same auction more than once.
    seen, uniq = set(), []
    for r in sorted(out, key=lambda x: (x["date"], x["term"])):
        k = (r["date"], r["term"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    return uniq


# ── BEA / Census official cross-checks ──────────────────────────────────────

def bea_schedule(start: dt.date, end: dt.date) -> list[dict] | None:
    data = http_get(BEA_RELEASE_DATES)
    if not isinstance(data, dict):
        return None
    out = []
    for title, blob in data.items():
        if title == "file_last_updated" or not isinstance(blob, dict):
            continue
        for raw in blob.get("release_dates") or []:
            try:
                instant = dt.datetime.fromisoformat(raw)
            except (TypeError, ValueError):
                continue
            if start <= instant.date() <= end:
                out.append({"title": title, "datetime": instant.isoformat()})
    return out


class _CensusCalendarParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_calendar = False
        self.in_row = False
        self.in_cell = False
        self.cells: list[str] = []
        self.rows: list[list[str]] = []
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "table" and attrs.get("id") == "calendar":
            self.in_calendar = True
        elif self.in_calendar and tag == "tr":
            self.in_row, self.cells = True, []
        elif self.in_row and tag in ("td", "th"):
            self.in_cell, self._text = True, []

    def handle_data(self, data):
        if self.in_cell:
            self._text.append(data)

    def handle_endtag(self, tag):
        if self.in_cell and tag in ("td", "th"):
            self.cells.append(" ".join("".join(self._text).split()))
            self.in_cell = False
        elif self.in_row and tag == "tr":
            if self.cells:
                self.rows.append(self.cells)
            self.in_row = False
        elif self.in_calendar and tag == "table":
            self.in_calendar = False


def parse_census_calendar(raw: bytes | str, start: dt.date, end: dt.date) -> list[dict]:
    parser = _CensusCalendarParser()
    parser.feed(raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw)
    out = []
    for cells in parser.rows:
        if len(cells) < 4 or cells[0] == "Indicator":
            continue
        try:
            day = dt.datetime.strptime(cells[1], "%B %d, %Y").date()
            tm = dt.datetime.strptime(cells[2], "%I:%M %p").strftime("%H:%M")
        except ValueError:
            continue
        if start <= day <= end:
            out.append({"title": cells[0], "date": day.isoformat(),
                        "time_et": tm, "period": cells[3]})
    return out


def census_schedule(start: dt.date, end: dt.date) -> list[dict] | None:
    raw = http_get(CENSUS_CALENDAR, as_json=False)
    if raw is None:
        return None
    rows = parse_census_calendar(raw, start, end)
    return rows or None


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    api_key = env_key("FRED_API_KEY")
    cfg = settings()
    wl = load_yaml("events.yaml").get("macro") or []
    ids = load_yaml("release_ids.yaml") or {}
    cal = load_yaml("calendar.yaml")

    if not ids:
        sys.stderr.write(
            "[fatal] config/release_ids.yaml is empty — run bootstrap_releases.py first.\n")
        return 3

    start = today_et()
    end = start + dt.timedelta(days=int(cfg["fred"]["lookahead_days"]))

    result = {
        "fetched_at": now_utc_iso(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "fred": {}, "priors": {}, "bls": None, "treasury": None,
        "bea": None, "census": None,
        "manual": {"fomc": cal.get("fomc_meetings") or [],
                   "private": cal.get("private_releases") or []},
        "failures": [],
    }

    for e in wl:
        if e.get("source") != "fred":
            continue
        key = e["key"]
        meta = ids.get(key)
        if not meta:
            result["failures"].append(
                {"source": "fred", "key": key, "reason": "release_id 未解析（见 events_review.yaml）"})
            continue
        dates = fred_release_dates(api_key, int(meta["release_id"]), start, end)
        if dates is None:
            result["failures"].append({"source": "fred", "key": key,
                                       "reason": "release/dates 请求失败"})
            continue
        result["fred"][key] = {"release_id": meta["release_id"],
                               "fred_name": meta.get("fred_name"),
                               "dates": dates}
        if e.get("fred_series"):
            pv = fred_prior_value(api_key, e["fred_series"],
                                  int(cfg["fred"]["observation_limit"]))
            if pv:
                result["priors"][key] = pv

    bls = bls_schedule()
    if bls is None:
        result["failures"].append({"source": "bls_ics", "reason": "获取或解析失败"})
    else:
        result["bls"] = bls

    tre = treasury_long_auctions(start, end)
    if tre is None:
        result["failures"].append({"source": "treasury", "reason": "请求失败"})
    else:
        result["treasury"] = tre

    bea = bea_schedule(start, end)
    if bea is None:
        result["failures"].append({"source": "bea", "reason": "机器日程获取或解析失败"})
    else:
        result["bea"] = bea

    census = census_schedule(start, end)
    if census is None:
        result["failures"].append({"source": "census", "reason": "官方日历获取或解析失败"})
    else:
        result["census"] = census

    write_json(DATA / "raw_macro.json", result)
    print(f"[ok] fred keys={len(result['fred'])} priors={len(result['priors'])} "
          f"bls={'n/a' if bls is None else len(bls)} "
          f"treasury={'n/a' if tre is None else len(tre)} "
          f"bea={'n/a' if bea is None else len(bea)} "
          f"census={'n/a' if census is None else len(census)} "
          f"failures={len(result['failures'])}")
    for fl in result["failures"]:
        print(f"  [failure] {fl}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
