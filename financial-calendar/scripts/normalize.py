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
from common import (DATA, ET, SNAPSHOTS, et_date, et_to_utc, load_yaml,  # noqa: E402
                    now_utc_iso, parse_utc, read_json, settings, today_et, write_json)


def _base(ev_id, kind, label, date, tier, time_et, source, fetched_at):
    iso, tconf = et_to_utc(date, time_et)
    return {
        "id": ev_id, "kind": kind, "label": label, "date_utc": iso,
        "tier": tier, "time_confidence": tconf, "date_confidence": "confirmed",
        "source": source, "source_fetched_at": fetched_at,
        "source_key": None,
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

    official_by_key = _official_schedule_index(raw, whitelist)

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

            official = official_by_key.get(key, {}).get(d)
            if official and time_et and official.get("time_et") != time_et:
                conflicts.append({
                    "key": key, "date": ds, "static_table_et": time_et,
                    "official_et": official.get("time_et"),
                    "official_source": official.get("source"),
                    "official_title": official.get("title"),
                })

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
            ev["source_key"] = key
            if prior:
                ev["prior_value"] = f"{prior['value']}（{prior['date']}，{prior['series']}）"
            out.append(ev)

    # Official BEA/Census rows fill a missing FRED occurrence for known keys.
    existing = {(ev.get("source_key"), et_date(ev["date_utc"])) for ev in out}
    for key, by_day in official_by_key.items():
        if key not in by_key:
            continue
        e = by_key[key]
        for d, row in by_day.items():
            if (key, d) in existing:
                continue
            ev = _base(f"official:{row['source']}:{key}:{d.isoformat()}", "macro",
                       e["label"], d, e.get("tier", "C"), row.get("time_et"),
                       row["source"], fetched)
            ev["source_key"] = key
            ev["notes"].append("官方交叉验证源补缺；FRED 本次未提供该日期")
            out.append(ev)

    for row in raw.get("treasury") or []:
        d = dt.date.fromisoformat(row["date"])
        e = by_key.get("treasury_auction_long", {})
        ev = _base(f"treasury:{row['term'].replace(' ', '')}:{row['date']}", "macro",
                   f"{row['term']} 国债拍卖", d, e.get("tier", "A"),
                   e.get("time_et"), "TreasuryDirect announced", fetched)
        ev["source_key"] = "treasury"
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
            ev["source_key"] = key
            if field == "decision" and m.get("sep"):
                ev["label"] += "（含 SEP / 点阵图）"
            if field == "decision" and m.get("presser", True):
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
        ev["source_key"] = e["key"]
        if p.get("note"):
            ev["notes"].append(str(p["note"]))
        out.append(ev)

    return out


def _tokens(entry):
    toks = []
    for m in entry.get("match") or []:
        toks.extend(w.lower() for w in m.split() if len(w) > 4)
    return toks or [entry["key"].lower()]


def _official_schedule_index(raw, whitelist):
    """Map official BEA/Census rows onto whitelist keys using configured names."""
    from common import norm
    candidates = []
    for row in raw.get("bea") or []:
        instant = parse_utc(row["datetime"])
        candidates.append({"title": row["title"], "date": instant.date(),
                           "time_et": instant.astimezone(ET).strftime("%H:%M"),
                           "source": "BEA release_dates.json"})
    for row in raw.get("census") or []:
        candidates.append({"title": row["title"],
                           "date": dt.date.fromisoformat(row["date"]),
                           "time_et": row.get("time_et"),
                           "source": "Census official calendar"})
    out = {}
    for e in whitelist:
        patterns = [norm(x) for x in (e.get("official_match") or [])]
        if not patterns:
            continue
        for row in candidates:
            title = norm(row["title"])
            if title in patterns:
                out.setdefault(e["key"], {}).setdefault(row["date"], row)
    return out


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
        ev["vendor_corroboration"] = r.get("vendor_corroboration", "single_source")
        ev["watchlist"] = "core" if r["ticker"] in core else "monitor"
        if r.get("notes_hint"):
            ev["notes"].append(r["notes_hint"])
        if r.get("disagreement"):
            ev["notes"].append("两源日期不一致，已标注")
        if ev["date_confidence"] == "estimated":
            ev["notes"].append("日期未确认，勿据此安排头寸")
        out.append(ev)
    return out


def apply_overrides(events, overrides):
    """Apply durable, auditable human enrichment without mutating raw facts."""
    rows = (overrides or {}).get("events") or {}
    for ev in events:
        override = rows.get(ev["id"])
        if not override:
            continue
        confirmation = override.get("confirmation") or {}
        if override.get("date_confidence") == "confirmed":
            if ev["kind"] != "earnings":
                raise ValueError(f"confirmed override only supported for earnings: {ev['id']}")
            if not confirmation.get("source") or not confirmation.get("fetched_at"):
                raise ValueError(f"confirmation requires source and fetched_at: {ev['id']}")
            ev["date_confidence"] = "confirmed"
            ev["confirmation"] = confirmation
            ev["notes"] = [n for n in ev.get("notes", []) if "日期未确认" not in n]
        for field in ("consensus", "nowcast"):
            value = override.get(field)
            if value:
                if not all(value.get(k) for k in ("value", "source", "fetched_at")):
                    raise ValueError(f"{field} requires value/source/fetched_at: {ev['id']}")
                ev[field] = value
        ev["notes"].extend(str(n) for n in (override.get("notes") or []) if n)
    return events


def latest_snapshot():
    files = sorted(SNAPSHOTS.glob("*.json"))
    return read_json(files[-1]) if files else None


def carry_forward_failed_sources(events, previous, failures,
                                 *, macro_missing=False, earnings_missing=False):
    """Retain future events from sources that failed, preserving old freshness.

    A successful source omission still goes through diff anti-flicker. Carrying
    forward is reserved for an explicit source failure, where absence contains
    no scheduling information at all.
    """
    if not previous:
        return events
    failed = {(f.get("source"), f.get("key")) for f in failures}
    fred_all = any(src == "fred" and key is None for src, key in failed)
    treasury_failed = any(src == "treasury" for src, _ in failed)
    earnings_failed = earnings_missing or any(src in ("finnhub", "earnings") for src, _ in failed)
    current_ids = {ev["id"] for ev in events}

    def affected(ev):
        if ev.get("kind") == "earnings":
            return earnings_failed
        if ev.get("kind") != "macro":
            return False
        if macro_missing:
            return True
        key = ev.get("source_key")
        if key == "treasury" or str(ev.get("source", "")).startswith("TreasuryDirect"):
            return treasury_failed
        if str(ev.get("source", "")).startswith("FRED"):
            return fred_all or ("fred", key) in failed
        return False

    for old in previous.get("events", []):
        if old["id"] in current_ids or et_date(old["date_utc"]) < today_et() or not affected(old):
            continue
        kept = dict(old)
        kept["notes"] = list(old.get("notes") or []) + ["⚠ 数据源失败，沿用上一份有效快照"]
        kept["carried_forward"] = True
        events.append(kept)
        current_ids.add(kept["id"])
    return events


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
    failures = list((macro_raw or {}).get("failures") or [])
    failures += list((earn_raw or {}).get("failures") or [])

    # Carry old facts before applying overrides, so audited enrichment also
    # remains deterministic on a degraded run.
    events = carry_forward_failed_sources(
        events, latest_snapshot(), failures,
        macro_missing=not bool(macro_raw), earnings_missing=not bool(earn_raw))
    events = apply_overrides(events, load_yaml("event_overrides.yaml"))

    # Deduplicate by id, keeping the first occurrence.
    seen, uniq = set(), []
    for ev in sorted(events, key=lambda e: (e["date_utc"], e["id"])):
        if ev["id"] in seen:
            continue
        seen.add(ev["id"])
        uniq.append(ev)

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
        other = c.get("bls_ics_et") or c.get("official_et")
        source = "BLS" if c.get("bls_ics_et") else c.get("official_source", "official")
        print(f"  [conflict] {c['key']} {c['date']}: "
              f"静态表 {c['static_table_et']} vs {source} {other}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
