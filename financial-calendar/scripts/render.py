"""Render the three brief tiers, each in a short (IM) and long (email) version.

Fixed layout rules:
  - Changes go FIRST, above every new event, in every tier.
  - ET and Beijing time side by side, always.
  - `estimated` earnings never appear in a "confirmed" block.
  - Staleness is stated at the top, never hidden.
  - The buyback blackout line is written in calendar language ("回购受限"),
    never as a flow figure — it is a static seasonal approximation, not data.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import resonance  # noqa: E402
from common import (DATA, WEEKDAY_CN, et_date, fmt_dual, parse_utc,  # noqa: E402
                    read_json, settings, tier_rank, today_et)

TIER_MARK = {"A": "🔴 A", "B": "🟡 B", "C": "⚪ C"}
CHANGE_MARK = {"MOVED": "⚠ 改期", "CANCELLED": "⚠ 取消", "CONFIRMED": "✅ 已确认",
               "NEW": "＋ 新增", "STALE": "… 源中缺失"}


# ── helpers ──────────────────────────────────────────────────────────────────

def _staleness(doc: dict, cfg: dict) -> tuple[int | None, str | None]:
    fetched = (doc.get("source_fetched_at") or {}).get("macro")
    if not fetched:
        return None, None
    age = (dt.datetime.now(dt.timezone.utc) - parse_utc(fetched)).days
    if age >= int(cfg["staleness"]["degrade_days"]):
        return age, f"⚠ 数据陈旧 {age} 天 —— 本简报仅为历史快照，A 类事件请人工核实"
    if age >= int(cfg["staleness"]["warn_days"]):
        return age, f"⚠ 数据陈旧 {age} 天"
    return age, None


def _failure_banner(doc: dict) -> list[str]:
    """Source failures go at the very top of every tier, short version included.

    A brief that quietly renders without its macro source looks identical to a
    week with no macro events. That mistake is unrecoverable by the reader, so
    it is never allowed to be silent.
    """
    fails = doc.get("failures") or []
    if not fails:
        return []
    crit = [f for f in fails if f.get("severity") == "critical"]
    lines = []
    for f in crit:
        lines.append(f"🚨 **{f.get('source')} 源失败：{f.get('reason')}** "
                     "—— 本简报不完整，请勿据此判断日程")
    others = [f for f in fails if f.get("severity") != "critical"]
    if others:
        names = "、".join(str(f.get("key") or f.get("source")) for f in others[:6])
        lines.append(f"⚠ {len(others)} 项拉取失败（{names}），相关事件可能缺失")
    return lines + [""] if lines else []


def _window(events, start: dt.date, days: int, tiers: list[str]):
    end = start + dt.timedelta(days=days)
    out = [e for e in events
           if start <= et_date(e["date_utc"]) <= end and e["tier"] in tiers]
    return sorted(out, key=lambda e: (e["date_utc"], tier_rank(e["tier"])))


def _split_earnings(events):
    conf = [e for e in events if e["kind"] != "earnings"
            or e.get("date_confidence") == "confirmed"]
    est = [e for e in events if e["kind"] == "earnings"
           and e.get("date_confidence") != "confirmed"]
    return conf, est


def _short_date(iso: str) -> str:
    d = parse_utc(iso).astimezone(dt.timezone.utc)
    from common import ET as _ET
    e = d.astimezone(_ET)
    return f"{e:%m/%d}（{WEEKDAY_CN[e.weekday()]}）"


def _line(ev: dict, short: bool = False) -> str:
    mark = TIER_MARK.get(ev["tier"], ev["tier"])
    s = f"- {mark} {fmt_dual(ev['date_utc'], ev['time_confidence'])} — {ev['label']}"
    if short:
        # The IM version carries dates and names only; numbers live in the long
        # version. Truncating mid-event is worse than omitting detail by design.
        return s
    if ev.get("prior_value"):
        s += f"\n  前值：{ev['prior_value']}"
    if ev.get("consensus"):
        c = ev["consensus"]
        s += f"\n  共识：{c.get('value')}（来源：{c.get('source')}，{c.get('fetched_at')}）"
    if ev.get("nowcast"):
        n = ev["nowcast"]
        s += f"\n  Nowcast：{n.get('value')}（{n.get('source')}，模型 nowcast，非卖方共识）"
    for note in ev.get("notes") or []:
        if note:
            s += f"\n  · {note}"
    return s


def _changes_block(changes: list[dict], limit: int | None = None,
                   compact: bool = False) -> list[str]:
    if not changes:
        return []
    lines = (["## ⚠ 日历变更（优先阅读）"] if compact
             else ["## ⚠ 日历变更（优先阅读）", ""])
    shown = changes if limit is None else changes[:limit]
    for c in shown:
        mark = CHANGE_MARK.get(c["type"], c["type"])
        if c["type"] == "MOVED":
            if compact:
                lines.append(f"- {mark} {c['label']}：{_short_date(c['old'])} → "
                             f"{_short_date(c['new'])}")
            else:
                lines.append(f"- {mark} {c['label']}：{c['old_display']} → {c['new_display']}")
        elif c["type"] in ("CANCELLED", "STALE"):
            base = f"- {mark} {c['label']}（原 {_short_date(c['old']) if compact else c['old_display']}）"
            lines.append(base + ("" if compact or not c.get("note")
                                 else f" · {c['note']}"))
        else:
            lines.append(f"- {mark} {c['label']}："
                         + (_short_date(c['new']) if compact else c['new_display']))
    if limit is not None and len(changes) > limit:
        lines.append(f"- …另有 {len(changes) - limit} 条变更，见长版")
    lines.append("")
    return lines


def _blackout_line(doc: dict) -> str | None:
    bp = doc.get("blackout_profile") or {}
    if not bp:
        return None
    share = bp.get("today_share")
    peak = bp.get("peak") or {}
    return (f"回购静默期：当前约 {share:.0%} 市值处于静默窗口，"
            f"窗口高峰在 {peak.get('date')}（约 {peak.get('share', 0):.0%}）。"
            f"（{bp.get('disclaimer')}）")


def _resonance_block(events, days_scope) -> list[str]:
    res = resonance.detect(events)
    hits = {d: b for d, b in res.items() if d in days_scope}
    if not hits:
        return []
    lines = ["## 🔀 共振日", ""]
    for d in sorted(hits):
        day = dt.date.fromisoformat(d)
        lines.append(f"- {day:%m/%d}（{WEEKDAY_CN[day.weekday()]}）— "
                     f"{resonance.summarize(d, hits[d])}")
    lines.append("")
    return lines


# ── tiers ────────────────────────────────────────────────────────────────────

def render_month(doc, changes, cfg, short: bool) -> str:
    start = today_et()
    days = int(cfg["horizon_days"]["month"])
    tiers = cfg["tiers"]["month"] if short else ["A", "B", "C"]
    evs = _window(doc["events"], start, days, tiers)
    conf, est = _split_earnings(evs)
    scope = {(start + dt.timedelta(days=i)).isoformat() for i in range(days + 1)}

    L = [f"# 月度全景 · {start:%Y年%m月}", ""]
    L += _failure_banner(doc)
    age, warn = _staleness(doc, cfg)
    if warn:
        L += [warn, ""]
    L += _changes_block(changes, limit=2 if short else None, compact=short)

    a_events = [e for e in conf if e["tier"] == "A"]
    L += ["## 🔴 本月 A 类事件", ""]
    L += [_line(e, short) for e in a_events] or ["- （无）"]
    L += [""]

    L += _resonance_block(doc["events"], scope)

    earn = [e for e in conf if e["kind"] == "earnings"]
    if earn:
        weeks: dict = {}
        for e in earn:
            wk = et_date(e["date_utc"]).isocalendar()
            weeks.setdefault(f"{wk[0]}-W{wk[1]:02d}", []).append(e)
        L += ["## 📊 财报季形状", ""]
        for wk in sorted(weeks):
            names = "、".join(sorted({x["label"].split(" ")[0] for x in weeks[wk]}))
            flag = "　← 密集周" if len(weeks[wk]) >= 3 else ""
            L.append(f"- {wk}（{len(weeks[wk])} 家）{flag}：{names}")
        L += [""]

    mech = [e for e in conf if e["kind"] == "mechanical"]
    if mech and not short:
        L += ["## ⚙ 机械日历", ""]
        L += [_line(e, short) for e in mech]
        L += [""]
    bl = _blackout_line(doc)
    if bl and not short:
        L += [bl, ""]

    if est and not short:
        L += ["## ❓ 未确认财报日期", ""]
        L += [_line(e, short) for e in est]
        L += [""]

    return _finish(L, cfg, short)


def render_week(doc, changes, cfg, short: bool) -> str:
    start = today_et()
    days = int(cfg["horizon_days"]["week"])
    tiers = ["A", "B"] if short else ["A", "B", "C"]
    evs = _window(doc["events"], start, days, tiers)
    conf, est = _split_earnings(evs)
    scope = {(start + dt.timedelta(days=i)).isoformat() for i in range(days + 1)}

    L = [f"# 周度清单 · {start:%m/%d} 起", ""]
    L += _failure_banner(doc)
    age, warn = _staleness(doc, cfg)
    if warn:
        L += [warn, ""]
    L += _changes_block(changes, limit=2 if short else None, compact=short)
    L += _resonance_block(doc["events"], scope)

    by_day: dict = {}
    for e in conf:
        by_day.setdefault(et_date(e["date_utc"]), []).append(e)

    if short:
        keep = [e for e in conf if e["tier"] == "A"
                or (e["kind"] == "earnings" and e.get("watchlist") == "core")]
        by_day = {}
        for e in keep:
            by_day.setdefault(et_date(e["date_utc"]), []).append(e)

    L += ["## 📅 本周日程", ""]
    if not by_day:
        L += ["- （无）", ""]
    for day in sorted(by_day):
        L.append(f"### {day:%m/%d}（{WEEKDAY_CN[day.weekday()]}）")
        L += [_line(e, short) for e in by_day[day]]
        L.append("")

    if est and not short:
        L += ["## ❓ 可能落在本周（日期未确认）", ""]
        L += [_line(e, short) for e in est]
        L += [""]

    if not short:
        top = [e for e in conf if e["tier"] == "A"][:int(cfg["consensus"]["top_n"])]
        missing = [e for e in top if not e.get("consensus")]
        if missing:
            L += ["## 📌 待补共识（agent 检索项）", ""]
            L += [f"- {e['label']}（{fmt_dual(e['date_utc'], e['time_confidence'])}）"
                  " — 无共识数据" for e in missing]
            L += [""]

    return _finish(L, cfg, short)


def render_day(doc, changes, cfg, short: bool) -> str:
    start = today_et()
    tomorrow = start + dt.timedelta(days=1)
    tiers = ["A", "B"] if short else ["A", "B", "C"]
    evs = _window(doc["events"], start, int(cfg["horizon_days"]["day"]), tiers)
    conf, est = _split_earnings(evs)

    today_evs = [e for e in conf if et_date(e["date_utc"]) == start]
    tmr_evs = [e for e in conf if et_date(e["date_utc"]) == tomorrow]

    L = [f"# 日度提醒 · {start:%m/%d}（{WEEKDAY_CN[start.weekday()]}）", ""]
    L += _failure_banner(doc)
    age, warn = _staleness(doc, cfg)
    if warn:
        L += [warn, ""]
    L += _changes_block(changes, limit=2 if short else None, compact=short)

    L += ["## 今日", ""]
    L += [_line(e, short) for e in today_evs] or ["- （无）"]
    L += ["", "## 明日预告", ""]
    L += [_line(e, short) for e in tmr_evs] or ["- （无）"]
    L += [""]

    if est and not short:
        near = [e for e in est if et_date(e["date_utc"]) <= tomorrow]
        if near:
            L += ["## ❓ 日期未确认", ""]
            L += [_line(e, short) for e in near]
            L += [""]

    return _finish(L, cfg, short)


def _finish(lines: list[str], cfg: dict, short: bool) -> str:
    if not short:
        return "\n".join(lines).rstrip() + "\n"
    cap = int(cfg["short_version"]["max_lines"])
    flat = [x for x in "\n".join(lines).split("\n")]
    if len(flat) <= cap:
        return "\n".join(flat).rstrip() + "\n"
    kept = flat[:cap - 1]
    kept.append(f"…（另有 {len(flat) - cap + 1} 行，见长版）")
    return "\n".join(kept).rstrip() + "\n"


RENDERERS = {"month": render_month, "week": render_week, "day": render_day}


def render(tier: str, short: bool) -> str:
    cfg = settings()
    doc = read_json(DATA / "events.json")
    if not doc:
        return "⚠ data/events.json 缺失 —— 请先运行 normalize.py\n"
    ch = (read_json(DATA / "changes.json") or {}).get("changes", [])
    ch = [c for c in ch if c["type"] != "STALE"] if short else ch
    return RENDERERS[tier](doc, ch, cfg, short)


if __name__ == "__main__":
    t = sys.argv[1] if len(sys.argv) > 1 else "week"
    s = "--short" in sys.argv
    print(render(t, s))
