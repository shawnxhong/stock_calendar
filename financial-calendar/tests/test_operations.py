from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest import mock

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402
import diff_engine  # noqa: E402
import fetch_macro  # noqa: E402
import normalize  # noqa: E402
import render  # noqa: E402
import run  # noqa: E402
import adapters  # noqa: E402


def macro(event_id: str, day: dt.date, time_et: str = "08:30",
          source: str = "FRED release_id=10", source_key: str = "cpi") -> dict:
    iso, time_confidence = common.et_to_utc(day, time_et)
    return {
        "id": event_id, "kind": "macro", "label": "Test", "date_utc": iso,
        "tier": "A", "time_confidence": time_confidence,
        "date_confidence": "confirmed", "source": source,
        "source_key": source_key, "notes": [],
    }


class TimezoneTests(unittest.TestCase):
    def test_dst_spring_and_fall_use_real_offsets(self) -> None:
        spring, _ = common.et_to_utc(dt.date(2026, 3, 9), "08:30")
        fall, _ = common.et_to_utc(dt.date(2026, 11, 2), "08:30")
        self.assertEqual(spring, "2026-03-09T12:30:00+00:00")
        self.assertEqual(fall, "2026-11-02T13:30:00+00:00")

    def test_beijing_date_rolls_forward(self) -> None:
        iso, confidence = common.et_to_utc(dt.date(2026, 8, 12), "16:30")
        rendered = common.fmt_dual(iso, confidence)
        self.assertIn("08/13 04:30 北京", rendered)


class DiffOperationalTests(unittest.TestCase):
    def test_same_id_time_change_is_moved(self) -> None:
        day = common.today_et() + dt.timedelta(days=7)
        old = macro("manual:test", day, "08:30")
        new = macro("manual:test", day, "09:30")
        changes, _ = diff_engine.diff(
            {"events": [old]}, {"events": [new]}, 2, {})
        self.assertEqual([c["type"] for c in changes], ["MOVED"])

    def test_pending_state_survives_empty_snapshot_until_cancel(self) -> None:
        day = common.today_et() + dt.timedelta(days=7)
        old = macro("manual:test", day)
        first, pending = diff_engine.diff(
            {"events": [old]}, {"events": []}, 2, {})
        second, pending = diff_engine.diff(
            {"events": []}, {"events": []}, 2, pending)
        self.assertEqual(first[0]["type"], "STALE")
        self.assertEqual(second[0]["type"], "CANCELLED")
        self.assertEqual(pending, {})

    def test_failed_fred_does_not_increment_miss(self) -> None:
        day = common.today_et() + dt.timedelta(days=7)
        old = macro("fred:10:test", day)
        changes, pending = diff_engine.diff(
            {"events": [old]},
            {"events": [], "failures": [{"source": "fred"}]}, 2, {})
        self.assertEqual(changes, [])
        self.assertEqual(pending["fred:10:test"]["misses"], 0)

    def test_new_past_event_is_not_reported(self) -> None:
        day = common.today_et() - dt.timedelta(days=1)
        changes, _ = diff_engine.diff(
            {"events": []}, {"events": [macro("manual:past", day)]}, 2, {})
        self.assertEqual(changes, [])


class SourceFallbackTests(unittest.TestCase):
    def test_failed_ism_carries_both_official_series(self) -> None:
        day = common.today_et() + dt.timedelta(days=7)
        olds = [
            macro("official:ism_manufacturing:x", day,
                  source="ISM official report calendar",
                  source_key="ism_manufacturing"),
            macro("official:ism_services:x", day,
                  source="ISM official report calendar",
                  source_key="ism_services"),
        ]
        got = normalize.carry_forward_failed_sources(
            [], {"events": olds}, [{"source": "ism"}])
        self.assertEqual(len(got), 2)
        self.assertTrue(all(e["carried_forward"] for e in got))

    def test_fetch_macro_without_fred_still_writes_keyless_sources(self) -> None:
        written = {}
        calendar = {"fomc_meetings": [{"decision": "2026-09-16"}],
                    "private_releases": []}
        with mock.patch.object(fetch_macro, "env_key", return_value=None), \
             mock.patch.object(fetch_macro, "settings",
                               return_value={"fred": {"lookahead_days": 30,
                                                      "observation_limit": 2}}), \
             mock.patch.object(fetch_macro, "load_yaml",
                               side_effect=lambda name: (
                                   {"macro": []} if name == "events.yaml"
                                   else calendar if name == "calendar.yaml" else {})), \
             mock.patch.object(fetch_macro, "bls_schedule", return_value=[]), \
             mock.patch.object(fetch_macro, "treasury_long_auctions",
                               return_value=[{"date": "2026-08-13",
                                              "term": "10-Year"}]), \
             mock.patch.object(fetch_macro, "bea_schedule", return_value=[]), \
             mock.patch.object(fetch_macro, "census_schedule", return_value=[]), \
             mock.patch.object(fetch_macro, "ism_schedule",
                               return_value=[{"key": "ism_services",
                                              "date": "2026-09-03"}]), \
             mock.patch.object(fetch_macro, "adp_schedule", return_value=[]), \
             mock.patch.object(fetch_macro, "write_json",
                               side_effect=lambda _path, obj: written.update(obj)):
            code = fetch_macro.main()
        self.assertEqual(code, 0)
        self.assertEqual(written["manual"]["fomc"], calendar["fomc_meetings"])
        self.assertEqual(len(written["treasury"]), 1)
        self.assertEqual(len(written["ism"]), 1)
        self.assertTrue(any(f["source"] == "fred" and
                            f["severity"] == "critical"
                            for f in written["failures"]))


class RenderingFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = common.load_yaml("settings.yaml")
        self.doc = {
            "events": [], "failures": [],
            "source_fetched_at": {"macro": common.now_utc_iso()},
            "blackout_profile": {},
        }

    def test_critical_failure_banner_survives_short_cap(self) -> None:
        self.doc["failures"] = [{
            "source": "macro", "severity": "critical", "reason": "missing",
        }]
        output = render.render_week(self.doc, [], self.cfg, short=True)
        self.assertIn("🚨", output)
        self.assertLessEqual(len(output.rstrip().splitlines()), 15)

    def test_stale_over_three_days_degrades_to_history_only(self) -> None:
        old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=4)
        self.doc["source_fetched_at"]["macro"] = old.isoformat()
        output = render.render_day(self.doc, [], self.cfg, short=True)
        self.assertIn("仅为历史快照", output)

    def test_short_cap_does_not_end_with_orphan_day_heading(self) -> None:
        start = common.today_et()
        self.doc["events"] = [
            macro(f"manual:{i}", start + dt.timedelta(days=i % 3))
            for i in range(20)
        ]
        output = render.render_week(self.doc, [], self.cfg, short=True)
        lines = output.rstrip().splitlines()
        self.assertFalse(lines[-2].startswith("### "))


class IdempotencyTests(unittest.TestCase):
    def test_second_identical_run_has_no_fresh_events(self) -> None:
        day = common.today_et() + dt.timedelta(days=1)
        doc = {"events": [macro("manual:test", day)]}
        state = {}
        first = run.update_delivery_state(
            state, doc, [], "week", "2026-08-12T00:00:00+00:00")
        second = run.update_delivery_state(
            state, doc, [], "week", "2026-08-12T01:00:00+00:00")
        self.assertEqual((first, second), (1, 0))

    def test_confirmed_change_requeues_existing_event(self) -> None:
        day = common.today_et() + dt.timedelta(days=1)
        doc = {"events": [macro("earnings:TEST:x", day)]}
        state = {"pushed": {"week": {
            "earnings:TEST:x": "2026-08-12T00:00:00+00:00"}}}
        fresh = run.update_delivery_state(
            state, doc, [{"id": "earnings:TEST:x", "type": "CONFIRMED"}],
            "week", "2026-08-12T01:00:00+00:00")
        self.assertEqual(fresh, 1)


class ManualWindowTests(unittest.TestCase):
    def test_manual_events_outside_raw_window_are_excluded(self) -> None:
        raw = {
            "fetched_at": common.now_utc_iso(),
            "window": {"start": "2026-08-12", "end": "2026-12-31"},
            "fred": {}, "treasury": [],
            "manual": {"fomc": [
                {"decision": "2026-07-29", "verified": True},
                {"decision": "2026-09-16", "verified": True},
            ]},
        }
        whitelist = [{"key": "fomc_decision", "label": "FOMC",
                      "tier": "A", "time_et": "14:00"}]
        events = normalize.normalize_macro(raw, whitelist, [])
        self.assertEqual({common.et_date(e["date_utc"]) for e in events},
                         {dt.date(2026, 9, 16)})
        self.assertEqual(len(events), 2)  # decision + independent presser


class PortabilityTests(unittest.TestCase):
    def test_directory_delivery_writes_both_versions(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            adapter = adapters.DirectoryDelivery(Path(td))
            adapter.deliver(tier="week", short="short\n", long="long\n",
                            idempotency_key="2026-08-12")
            self.assertEqual(
                (Path(td) / "2026-08-12-week.md").read_text(), "long\n")
            self.assertEqual(
                (Path(td) / "2026-08-12-week-short.md").read_text(), "short\n")

    def test_json_state_store_round_trip(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            store = adapters.JsonFileStateStore(Path(td) / "state.json")
            store.save({"pending_missing": {"x": {"misses": 1}}})
            self.assertEqual(store.load()["pending_missing"]["x"]["misses"], 1)


if __name__ == "__main__":
    unittest.main()
