"""Resonance: days where an A-tier macro event lands on a mechanical event.

Pure render logic over existing data — no new source, no estimation. This is
the main practical payoff of keeping the mechanical calendar in the same
system: two calendars that don't know about each other cannot produce the line
"Jackson Hole 讲话 + 8 月 OPEX 到期，同日".
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import et_date  # noqa: E402


def detect(events: list[dict]) -> dict:
    by_day: dict = {}
    for ev in events:
        by_day.setdefault(et_date(ev["date_utc"]), []).append(ev)

    out = {}
    for day, evs in by_day.items():
        macro_a = [e for e in evs if e["kind"] == "macro" and e["tier"] == "A"]
        mech = [e for e in evs if e["kind"] == "mechanical"]
        core_earn = [e for e in evs if e["kind"] == "earnings"
                     and e.get("watchlist") == "core"
                     and e.get("date_confidence") == "confirmed"]
        # Resonance requires a mechanical event plus something that moves on
        # information. Two mechanical events on one day is just the calendar.
        if mech and (macro_a or core_earn):
            out[day.isoformat()] = {
                "macro_a": [e["label"] for e in macro_a],
                "mechanical": [e["label"] for e in mech],
                "core_earnings": [e["label"] for e in core_earn],
            }
    return out


def summarize(day_iso: str, blob: dict) -> str:
    parts = blob["macro_a"] + blob["core_earnings"] + blob["mechanical"]
    return " + ".join(parts)
