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
        self.assertIn("08/13 04:30 北京时间", rendered)


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
    def test_bls_schedule_uses_calendar_client_headers(self) -> None:
        with mock.patch.object(fetch_macro, "http_get", return_value=None) as get:
            self.assertIsNone(fetch_macro.bls_schedule())
        get.assert_called_once_with(
            fetch_macro.BLS_ICS, as_json=False, headers=fetch_macro.BLS_HEADERS)
        self.assertTrue(fetch_macro.BLS_HEADERS["User-Agent"].startswith("Mozilla/"))
        self.assertIn("text/calendar", fetch_macro.BLS_HEADERS["Accept"])

    def test_bls_empty_calendar_is_source_unavailable(self) -> None:
        empty_ics = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n"
        with mock.patch.object(fetch_macro, "http_get", return_value=empty_ics):
            self.assertIsNone(fetch_macro.bls_schedule())

    def test_yfinance_ticker_failure_carries_only_that_tickers_events(self) -> None:
        day = common.today_et() + dt.timedelta(days=7)
        iso, confidence = common.et_to_utc(day, "16:30")
        previous = {"events": [
            {"id": "earnings:NVDA:x", "kind": "earnings",
             "date_utc": iso, "time_confidence": confidence, "notes": []},
            {"id": "earnings:TSLA:x", "kind": "earnings", "ticker": "TSLA",
             "date_utc": iso, "time_confidence": confidence, "notes": []},
        ]}
        got = normalize.carry_forward_failed_sources(
            [], previous, [{"source": "yfinance", "key": "NVDA"}])
        self.assertEqual([event["id"] for event in got], ["earnings:NVDA:x"])
        self.assertTrue(got[0]["carried_forward"])

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
             mock.patch.object(fetch_macro, "bls_schedule", return_value=None), \
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
        bls_failure = next(f for f in written["failures"]
                           if f["source"] == "bls_ics")
        self.assertEqual(bls_failure["severity"], "advisory")
        self.assertEqual(bls_failure["impact"], "cross_check_only")


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

    def test_bls_cross_check_failure_is_a_soft_notice(self) -> None:
        # Legacy cached failures have no explicit advisory severity.
        self.doc["failures"] = [{
            "source": "bls_ics", "reason": "获取或解析失败", "notify": True,
        }]
        output = render.render_week(self.doc, [], self.cfg, short=True)
        self.assertIn("ℹ bls_ics 辅助校验暂不可用", output)
        self.assertIn("主要日程不受此项影响", output)
        self.assertNotIn("相关事件可能缺失", output)
        self.assertLessEqual(len(output.rstrip().splitlines()), 15)

    def test_unchanged_advisory_is_suppressed_without_notify(self) -> None:
        self.doc["failures"] = [{
            "source": "bls_ics", "reason": "获取或解析失败",
        }]
        output = render.render_week(self.doc, [], self.cfg, short=True)
        self.assertNotIn("辅助校验暂不可用", output)

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

    def test_week_short_lists_b_events_without_dead_long_version_copy(self) -> None:
        day = common.today_et() + dt.timedelta(days=1)
        event = macro("manual:b", day)
        event["tier"] = "B"
        self.doc["events"] = [event]
        output = render.render_week(self.doc, [], self.cfg, short=True)
        self.assertIn("本周 A/B 日程", output)
        self.assertIn("🟡 中等", output)
        self.assertNotIn("见长版", output)
        self.assertLessEqual(len(output.rstrip().splitlines()), 15)


class IdempotencyTests(unittest.TestCase):
    class Adapter:
        def __init__(self) -> None:
            self.calls = []

        def deliver(self, **kwargs) -> None:
            self.calls.append(kwargs)

    def test_second_identical_delivery_is_skipped(self) -> None:
        state = {}
        adapter = self.Adapter()
        first, key1 = run.deliver_once(
            state, adapter, tier="week", short="short\n", long="long\n",
            day="2026-08-12", delivered_at="2026-08-12T00:00:00+00:00")
        second, key2 = run.deliver_once(
            state, adapter, tier="week", short="short\n", long="long\n",
            day="2026-08-12", delivered_at="2026-08-12T01:00:00+00:00")
        self.assertEqual((first, second), (True, False))
        self.assertEqual(key1, key2)
        self.assertIn("-week-", key1)
        self.assertEqual(len(adapter.calls), 1)

    def test_changed_content_gets_a_new_delivery_key(self) -> None:
        state = {}
        adapter = self.Adapter()
        first, key1 = run.deliver_once(
            state, adapter, tier="week", short="short\n", long="long\n",
            day="2026-08-12", delivered_at="2026-08-12T00:00:00+00:00")
        second, key2 = run.deliver_once(
            state, adapter, tier="week", short="changed\n", long="long\n",
            day="2026-08-12", delivered_at="2026-08-12T01:00:00+00:00")
        self.assertTrue(first and second)
        self.assertNotEqual(key1, key2)
        self.assertEqual(len(adapter.calls), 2)

    def test_legacy_delivery_key_is_migrated_without_redelivery(self) -> None:
        version = run.content_version("short\n", "long\n")
        legacy = f"week:2026-08-12-{version}"
        state = {"delivered_content": {legacy: {
            "tier": "week", "day": "2026-08-12", "version": version,
            "at": "2026-08-12T00:00:00+00:00",
        }}}
        adapter = self.Adapter()
        delivered, key = run.deliver_once(
            state, adapter, tier="week", short="short\n", long="long\n",
            day="2026-08-12", delivered_at="2026-08-12T01:00:00+00:00")
        self.assertFalse(delivered)
        self.assertEqual(adapter.calls, [])
        self.assertIn(key, state["delivered_content"])
        self.assertEqual(state["delivered_content"][key]["migrated_from"], legacy)

    def test_failed_delivery_is_not_marked_complete(self) -> None:
        class FailingAdapter:
            def deliver(self, **_kwargs) -> None:
                raise OSError("provider unavailable")

        state = {}
        with self.assertRaises(OSError):
            run.deliver_once(
                state, FailingAdapter(), tier="week", short="short\n",
                long="long\n", day="2026-08-12",
                delivered_at="2026-08-12T00:00:00+00:00")
        self.assertEqual(state.get("delivered_content"), {})


class OrchestratorFailureTests(unittest.TestCase):
    def test_diff_failure_stops_before_rendering(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(run, "DATA", Path(td)), \
             mock.patch.object(run, "_run", side_effect=[0, 0, 0, 7]):
            self.assertEqual(run._run_tier("week", False, None), 3)
            health = common.read_json(Path(td) / "health.json")
            self.assertFalse(health["healthy"])
            self.assertEqual(health["failures"][0]["source"], "diff_engine")

    def test_doctor_detects_missing_fred_release_ids(self) -> None:
        entries = [
            {"key": "cpi", "source": "fred"},
            {"key": "beige_book", "source": "manual"},
        ]
        self.assertEqual(run.fred_config_gaps({}, entries), {"cpi"})
        self.assertEqual(
            run.fred_config_gaps({"cpi": {"release_id": 10}}, entries), set())

    def test_bls_cross_check_failure_keeps_health_healthy(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(run, "DATA", Path(td)):
            run._write_health(
                healthy=True, tier="week", failures=[{"source": "bls_ics"}],
                delivery={"configured": False}, outputs=[])
            health = common.read_json(Path(td) / "health.json")
        self.assertTrue(health["healthy"])
        self.assertEqual(health["status"], "healthy")
        self.assertEqual(health["advisory_failure_count"], 1)

    def test_noncritical_primary_failure_health_is_degraded(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(run, "DATA", Path(td)):
            run._write_health(
                healthy=True, tier="week", failures=[{"source": "treasury"}],
                delivery={"configured": False}, outputs=[])
            health = common.read_json(Path(td) / "health.json")
        self.assertTrue(health["healthy"])
        self.assertEqual(health["status"], "degraded")
        self.assertEqual(health["advisory_failure_count"], 0)

    def test_render_only_run_does_not_write_health(self) -> None:
        # A render-only run (no delivery config) must not write health.json:
        # the IM dispatcher would otherwise see a stale idempotency key and
        # re-deliver under a filename fallback key (the duplicate-delivery bug).
        import tempfile
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(run, "DATA", Path(td)), \
             mock.patch.object(run, "LOGS", Path(td)), \
             mock.patch.object(render, "DATA", Path(td)), \
             mock.patch.dict("os.environ", {"FINCAL_DELIVERY_DIR": ""}):
            common.write_json(Path(td) / "events.json", {
                "events": [], "failures": [],
                "source_fetched_at": {"macro": common.now_utc_iso()},
                "blackout_profile": {},
            })
            code = run._run_tier("day", True, None)  # no_fetch=True, no delivery
        self.assertEqual(code, 0)
        self.assertFalse((Path(td) / "health.json").exists())

    def test_advisory_notify_only_on_first_sight_or_change(self) -> None:
        def advisory(reason: str) -> dict:
            return {"source": "bls_ics", "severity": "advisory", "reason": reason}

        state: dict = {}
        # 首次出现 → notify
        doc = {"failures": [advisory("403")]}
        run._apply_advisory_notify(doc, state)
        self.assertTrue(doc["failures"][0].get("notify"))
        # 指纹不变 → 不再 notify
        doc = {"failures": [advisory("403")]}
        run._apply_advisory_notify(doc, state)
        self.assertNotIn("notify", doc["failures"][0])
        # 原因变化 → 再次 notify
        doc = {"failures": [advisory("timeout")]}
        run._apply_advisory_notify(doc, state)
        self.assertTrue(doc["failures"][0].get("notify"))
        # 恢复（无 advisory）→ 状态更新为空，不 notify
        doc = {"failures": []}
        run._apply_advisory_notify(doc, state)
        self.assertEqual(state["advisory_state"], [])
        # 恢复后再出现 → 再次 notify
        doc = {"failures": [advisory("403")]}
        run._apply_advisory_notify(doc, state)
        self.assertTrue(doc["failures"][0].get("notify"))


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
                            idempotency_key="2026-08-12-week-abcd")
            self.assertEqual(
                (Path(td) / "2026-08-12-week-abcd.md").read_text(), "long\n")
            self.assertEqual(
                (Path(td) / "2026-08-12-week-abcd-short.md").read_text(), "short\n")

    def test_json_state_store_round_trip(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            store = adapters.JsonFileStateStore(Path(td) / "state.json")
            store.save({"pending_missing": {"x": {"misses": 1}}})
            self.assertEqual(store.load()["pending_missing"]["x"]["misses"], 1)


if __name__ == "__main__":
    unittest.main()
