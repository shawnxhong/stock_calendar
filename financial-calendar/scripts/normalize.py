"""Normalize raw sources into the unified events.json schema.

Also the place where the BLS ICS is checked against the static release-time
table. A conflict is REPORTED (both values shown), never silently resolved —
if BLS moves a release time and we quietly pick one, the error is undetectable.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import mechanical_calendar  # noqa: E402
from common import (DATA, ET, et_to_utc, load_yaml, now_utc_iso, parse_utc,  # noqa: E402
                    read_json, settings, today_et, write_json)


def _base(ev_id, kind, label, date, tier, time_et, source, fetched_at):
    iso, tconf = et_to_utc(date, time_et)
    return {
        "id": ev_id, "kind": kind, "label": label, "date_utc": iso,
        "tier": tier, "time_confidence": tconf, "date_confidence": "confirmed",
        "source": source, "source_fetched_at": fetched_at,
        "prior_value": None, "consensus": None, "nowcast": None, "notes": [],
    }


def _bls_index(bls_rows):
    """Map normalized BLS summary -> exact ET time, for conflict checking."""
    idx = {}
    for row in bls_rows or []:
        if not row.get("has_time"):
            continue
        try:
            d = dt.datetime.fromisoformat(row["datetime"])
        except (ValueError, KeyError):
            continue
        if d.tzinfo is None:
            d = d.replace(tzinfo=ET)
        local = d.astimezone(ET)
        idx.setdefault(local.date(), []).append(
            {"summary": row.get("summary", ""), "time_et": f"{local:%H:%M}"})
    return idx


def normalize_macro(raw, whitelist, conflicts):
    if not raw:
        return []
    fetched = raw.get("fetched_at")
    by_key = {e["key"]: e for e in whitelist}
    bls_idx = _bls_index(raw.get("bls"))
    out = []

    for key, blob in (raw.get("fred") or {}).items():
        e = by_key.get(key)
        if not e:
            continue
        prior = (raw.get("priors") or {}).get(key)
        for ds in blob.get("dates", []):
            try:
                d = dt.date.fromisoformat(ds)
            except ValueError:
                continue
            time_et = e.get("time_et")

            # Conflict check against the BLS ICS.
            for cand in bls_idx.get(d, []):
                if any(tok in cand["summary"].lower()
                       for tok in _tokens(e)) and time_et and cand["time_et"] != time_et:
                    conflicts.append({
                        "key": key, "date": ds,
                        "static_table_et": time_et, "bls_ics_et": cand["time_et"],
                        "bls_summary": cand["summary"],
                    })

            ev = _base(f"fred:{blob['release_id']}:{ds}", "macro", e["label"], d,
                       e.get("tier", "C"), time_et,
                       f"FRED release_id={blob['release_id']}", fetched)
            if prior:
                ev["prior_value"] = f"{prior['value']}（{prior['date']}，{prior['series']}）"
            out.append(ev)

    for row in raw.get("treasury") or []:
        d = dt.date.fromisoformat(row["date"])
        e = by_key.get("treasury_auction_long", {})
        ev = _base(f"treasury:{row['term'].replace(' ', '')}:{row['date']}", "macro",
                   f"{row['term']} 国债拍卖", d, e.get("tier", "A"),
                   e.get("time_et"), "TreasuryDirect announced", fetched)
        out.append(ev)

    man = raw.get("manual") or {}
    for m in man.get("fomc") or []:
        for field, key, label in (("decision", "fomc_decision", "FOMC 利率决议"),
                                  ("minutes", "fomc_minutes", "FOMC 会议纪要")):
            if not m.get(field):
                continue
            d = dt.date.fromisoformat(str(m[field]))
            e = by_key.get(key, {})
            ev = _base(f"manual:{key}:{d.isoformat()}", "macro", label, d,
                       e.get("tier", "A"), e.get("time_et"),
                       "calendar.yaml (人工核实)", fetched)
            if field == "decision" and m.get("sep"):
                ev["label"] += "（含 SEP / 点阵图）"
                pres = by_key.get("fomc_presser", {})
                out.append(_base(f"manual:fomc_presser:{d.isoformat()}", "macro",
                                 "FOMC 主席新闻发布会", d, pres.get("tier", "A"),
                                 pres.get("time_et"), "calendar.yaml (人工核实)", fetched))
            if m.get("note"):
                ev["notes"].append(str(m["note"]))
            out.append(ev)

    for p in man.get("private") or []:
        e = by_key.get(p.get("key"))
        if not e:
            continue
        d = dt.date.fromisoformat(str(p["date"]))
        ev = _base(f"manual:{e['key']}:{d.isoformat()}", "macro", e["label"], d,
                   e.get("tier", "B"), e.get("time_et"),
                   "calendar.yaml (人工录入)", fetched)
        if p.get("note"):
            ev["notes"].append(str(p["note"]))
        out.append(ev)

    return out


def _tokens(entry):
    toks = []
    for m in entry.get("match") or []:
        toks.extend(w.lower() for w in m.split() if len(w) > 4)
    return toks or [entry["key"].lower()]


HOUR_LABEL = {"bmo": "盘前", "amc": "盘后", "dmh": "盘中"}


def normalize_earnings(raw):
    if not raw:
        return []
    fetched = raw.get("fetched_at")
    core = set(raw.get("core") or [])
    out = []
    for r in raw.get("records") or []:
        try:
            d = dt.date.fromisoformat(r["date"])
        except (ValueError, KeyError, TypeError):
            continue
        hour = (r.get("hour") or "").lower()
        suffix = HOUR_LABEL.get(hour)
        label = f"{r['ticker']} 财报" + (f"（{suffix}）" if suffix else "")
        # bmo -> before the open; amc -> after the close. Anything else: no time.
        time_et = {"bmo": "07:00", "amc": "16:30"}.get(hour)
        ev = _base(f"earnings:{r['ticker']}:{r['date']}", "earnings", label, d,
                   "B", time_et, "+".join(r.get("sources") or ["vendor"]), fetched)
        ev["date_confidence"] = r.get("date_confidence", "estimated")
        ev["watchlist"] = "core" if r["ticker"] in core else "monitor"
        if r.get("notes_hint"):
            ev["notes"].append(r["notes_hint"])
        if r.get("disagreement"):
            ev["notes"].append("两源日期不一致，已标注")
        if ev["date_confidence"] == "estimated":
            ev["notes"].append("日期未确认，勿据此安排头寸")
        out.append(ev)
    return out


def main() -> int:
    cfg = settings()
    wl = load_yaml("events.yaml").get("macro") or []
    conflicts: list[dict] = []

    macro_raw = read_json(DATA / "raw_macro.json")
    earn_raw = read_json(DATA / "raw_earnings.json")

    events = []
    events += normalize_macro(macro_raw, wl, conflicts)
    events += normalize_earnings(earn_raw)
    events += mechanical_calendar.generate(
        today_et(), int(cfg["fred"]["lookahead_days"]))

    # Deduplicate by id, keeping the first occurrence.
    seen, uniq = set(), []
    for ev in sorted(events, key=lambda e: (e["date_utc"], e["id"])):
        if ev["id"] in seen:
            continue
        seen.add(ev["id"])
        uniq.append(ev)

    failures = list((macro_raw or {}).get("failures") or [])
    failures += list((earn_raw or {}).get("failures") or [])

    # A missing raw file means the fetch never produced data. Rendering a brief
    # containing only mechanical events without saying so would be the worst
    # failure this system can have: it looks like a normal calendar with no
    # macro events scheduled.
    if not macro_raw:
        failures.append({"source": "macro", "severity": "critical",
                         "reason": "raw_macro.json 缺失 —— 本次未取得任何宏观事件"})
    if not earn_raw:
        failures.append({"source": "earnings", "severity": "critical",
                         "reason": "raw_earnings.json 缺失 —— 本次未取得任何财报日期"})

    doc = {
        "generated_at": now_utc_iso(),
        "source_fetched_at": {
            "macro": (macro_raw or {}).get("fetched_at"),
            "earnings": (earn_raw or {}).get("fetched_at"),
        },
        "events": uniq,
        "time_conflicts": conflicts,
        "failures": failures,
        "blackout_profile": mechanical_calendar.blackout_profile(),
    }
    write_json(DATA / "events.json", doc)
    print(f"[ok] events={len(uniq)} conflicts={len(conflicts)} failures={len(failures)}")
    for c in conflicts:
        print(f"  [conflict] {c['key']} {c['date']}: "
              f"静态表 {c['static_table_et']} vs BLS {c['bls_ics_et']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
