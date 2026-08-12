"""Compare the current events.json against the last snapshot.

This is the most important component. Switching from active query to passive
push introduces one dominant new risk: the calendar changes and you never find
out. (2025's shutdown pushed a batch of BLS releases — precedent, not theory.)

Change classes: NEW / MOVED / CANCELLED / CONFIRMED.

Anti-flicker: a source returning incomplete data must NOT be read as a
cancellation. A single miss marks STALE and keeps the last known value; only
`miss_before_cancel` consecutive misses produce CANCELLED. False alarms train
you to ignore the channel, and that is harder to repair than a missed event.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (DATA, SNAPSHOTS, fmt_dual, now_utc_iso, read_json,  # noqa: E402
                    settings, today_et, write_json)


def latest_snapshot() -> dict | None:
    files = sorted(SNAPSHOTS.glob("*.json"))
    if not files:
        return None
    return read_json(files[-1])


def diff(prev: dict | None, cur: dict, miss_before_cancel: int,
         pending: dict) -> tuple[list[dict], dict]:
    """pending: {id: {"event": <full record>, "misses": n}}

    Events that vanished but are not yet cancelled MUST be carried forward here.
    A snapshot alone cannot do it: the snapshot is rewritten every run, so a
    disappeared event drops out of the baseline immediately and the miss counter
    can never reach the threshold — which silently disables anti-flicker and,
    with it, all cancellation reporting.
    """
    cur_map = {e["id"]: e for e in cur.get("events", [])}
    changes: list[dict] = []

    if prev is None:
        return changes, {}   # first run: everything is "new", which is noise

    prev_map = {e["id"]: e for e in prev.get("events", [])}
    for eid, blob in (pending or {}).items():
        prev_map.setdefault(eid, blob["event"])

    # Only treat sources that actually returned data as authoritative for
    # disappearance. If a source failed this run, its events can't be cancelled.
    failed_sources = {f.get("source") for f in cur.get("failures") or []}
    fred_failed = "fred" in failed_sources

    for eid, ev in cur_map.items():
        old = prev_map.get(eid)
        if old is None:
            # A pure date move shows up as an id change (dates are in the id).
            # Pair it with a same-key predecessor before calling it NEW.
            key = eid.rsplit(":", 1)[0]
            moved_from = [o for oid, o in prev_map.items()
                          if oid.rsplit(":", 1)[0] == key
                          and oid not in cur_map
                          and dt.date.fromisoformat(o["date_utc"][:10]) >= today_et()]
            if moved_from:
                src = moved_from[0]
                changes.append({
                    "type": "MOVED", "id": eid, "label": ev["label"],
                    "old": src["date_utc"], "new": ev["date_utc"],
                    "old_display": fmt_dual(src["date_utc"], src["time_confidence"]),
                    "new_display": fmt_dual(ev["date_utc"], ev["time_confidence"]),
                    "tier": ev["tier"],
                })
                continue
            changes.append({"type": "NEW", "id": eid, "label": ev["label"],
                            "new": ev["date_utc"],
                            "new_display": fmt_dual(ev["date_utc"], ev["time_confidence"]),
                            "tier": ev["tier"]})
            continue

        if old["date_utc"] != ev["date_utc"]:
            changes.append({
                "type": "MOVED", "id": eid, "label": ev["label"],
                "old": old["date_utc"], "new": ev["date_utc"],
                "old_display": fmt_dual(old["date_utc"], old["time_confidence"]),
                "new_display": fmt_dual(ev["date_utc"], ev["time_confidence"]),
                "tier": ev["tier"],
            })
        if (old.get("date_confidence") == "estimated"
                and ev.get("date_confidence") == "confirmed"):
            changes.append({
                "type": "CONFIRMED", "id": eid, "label": ev["label"],
                "new": ev["date_utc"],
                "new_display": fmt_dual(ev["date_utc"], ev["time_confidence"]),
                "tier": ev["tier"],
            })

    # Disappearances — with anti-flicker.
    new_pending: dict = {}
    today = today_et()
    moved_ids = {c["id"].rsplit(":", 1)[0] for c in changes if c["type"] == "MOVED"}
    for eid, old in prev_map.items():
        if eid in cur_map:
            continue        # reappeared → drop from pending, no change reported
        # Past events legitimately fall out of the forward window.
        if dt.date.fromisoformat(old["date_utc"][:10]) < today:
            continue
        # Already accounted for as a MOVED predecessor.
        if eid.rsplit(":", 1)[0] in moved_ids:
            continue

        prior = (pending or {}).get(eid, {}).get("misses", 0)
        if fred_failed and str(old.get("source", "")).startswith("FRED"):
            # The source itself failed — absence carries no information.
            new_pending[eid] = {"event": old, "misses": prior}
            continue

        n = prior + 1
        if n >= miss_before_cancel:
            changes.append({
                "type": "CANCELLED", "id": eid, "label": old["label"],
                "old": old["date_utc"],
                "old_display": fmt_dual(old["date_utc"], old["time_confidence"]),
                "tier": old["tier"], "misses": n,
            })
            # Cancelled → stop tracking; it will not be reported again.
        else:
            new_pending[eid] = {"event": old, "misses": n}
            changes.append({
                "type": "STALE", "id": eid, "label": old["label"],
                "old": old["date_utc"],
                "old_display": fmt_dual(old["date_utc"], old["time_confidence"]),
                "tier": old["tier"], "misses": n,
                "note": f"源中缺失 {n} 次，保留上次值，未判定取消",
            })

    order = {"MOVED": 0, "CANCELLED": 1, "CONFIRMED": 2, "STALE": 3, "NEW": 4}
    changes.sort(key=lambda c: (order.get(c["type"], 9),
                                {"A": 0, "B": 1, "C": 2}.get(c["tier"], 3),
                                c.get("new") or c.get("old") or ""))
    return changes, new_pending


def main() -> int:
    cfg = settings()
    cur = read_json(DATA / "events.json")
    if not cur:
        sys.stderr.write("[fatal] data/events.json missing — run normalize.py first.\n")
        return 3

    state = read_json(DATA / "state.json", {}) or {}
    pending = state.get("pending_missing", {})

    prev = latest_snapshot()
    changes, new_pending = diff(prev, cur,
                                int(cfg["staleness"]["miss_before_cancel"]),
                                pending)

    write_json(DATA / "changes.json", {
        "generated_at": now_utc_iso(),
        "compared_against": None if prev is None else prev.get("generated_at"),
        "first_run": prev is None,
        "changes": changes,
    })

    state["pending_missing"] = new_pending
    write_json(DATA / "state.json", state)

    snap = SNAPSHOTS / f"{today_et().isoformat()}.json"
    write_json(snap, cur)

    kinds = {}
    for c in changes:
        kinds[c["type"]] = kinds.get(c["type"], 0) + 1
    print(f"[ok] changes={len(changes)} {kinds} "
          f"{'(first run — diff suppressed)' if prev is None else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
