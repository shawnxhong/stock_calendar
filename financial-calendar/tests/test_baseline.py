from __future__ import annotations

import datetime as dt
import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402
import diff_engine  # noqa: E402
import fetch_earnings  # noqa: E402
import mechanical_calendar  # noqa: E402
import normalize  # noqa: E402
import render  # noqa: E402


def event(event_id: str, day: dt.date, *, confidence: str = "confirmed") -> dict:
    iso, time_confidence = common.et_to_utc(day, "08:30")
    return {
        "id": event_id,
        "kind": "macro",
        "label": "测试事件",
        "date_utc": iso,
        "tier": "A",
        "time_confidence": time_confidence,
        "date_confidence": confidence,
        "source": "FRED release_id=10",
        "source_fetched_at": common.now_utc_iso(),
        "prior_value": None,
        "consensus": None,
        "nowcast": None,
        "notes": [],
    }


class MechanicalCalendarTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config_patch = mock.patch.object(common, "CONFIG", SKILL_ROOT / "config")
        self.config_patch.start()
        importlib.reload(mechanical_calendar)

    def tearDown(self) -> None:
        self.config_patch.stop()

    def test_date_primitives(self) -> None:
        self.assertEqual(mechanical_calendar.third_friday(2026, 8), dt.date(2026, 8, 21))
        self.assertEqual(mechanical_calendar.last_bday(2026, 10), dt.date(2026, 10, 30))
        self.assertEqual(
            mechanical_calendar.quarter_end_anchor(dt.date(2026, 8, 12)),
            dt.date(2026, 6, 30),
        )

    def test_witching_has_rebalance_and_ids_are_unique(self) -> None:
        events = mechanical_calendar.generate(dt.date(2026, 9, 1), 40)
        labels = {e["label"] for e in events if common.et_date(e["date_utc"]) == dt.date(2026, 9, 18)}
        self.assertIn("三重魔咒到期（季度）", labels)
        self.assertIn("标普指数季度再平衡生效", labels)
        ids = [e["id"] for e in events]
        self.assertEqual(len(ids), len(set(ids)))

    def test_market_holiday_month_end_and_good_friday_opex(self) -> None:
        self.assertEqual(mechanical_calendar.last_bday(2027, 5), dt.date(2027, 5, 28))
        events = mechanical_calendar.generate(dt.date(2025, 4, 1), 30)
        opex = next(e for e in events if e["id"].startswith("mech:opex:"))
        self.assertEqual(common.et_date(opex["date_utc"]), dt.date(2025, 4, 17))


class DiffEngineTests(unittest.TestCase):
    def test_moved(self) -> None:
        old_day = common.today_et() + dt.timedelta(days=10)
        new_day = old_day + dt.timedelta(days=1)
        old = event(f"fred:10:{old_day.isoformat()}", old_day)
        new = event(f"fred:10:{new_day.isoformat()}", new_day)
        changes, pending = diff_engine.diff({"events": [old]}, {"events": [new]}, 2, {})
        self.assertEqual([c["type"] for c in changes], ["MOVED"])
        self.assertEqual(pending, {})

    def test_confirmed(self) -> None:
        day = common.today_et() + dt.timedelta(days=10)
        event_id = f"earnings:TEST:{day.isoformat()}"
        old = event(event_id, day, confidence="estimated")
        new = event(event_id, day, confidence="confirmed")
        changes, _ = diff_engine.diff({"events": [old]}, {"events": [new]}, 2, {})
        self.assertEqual([c["type"] for c in changes], ["CONFIRMED"])

    def test_stale_then_cancelled_then_silent(self) -> None:
        day = common.today_et() + dt.timedelta(days=10)
        old = event(f"fred:10:{day.isoformat()}", day)
        changes1, pending1 = diff_engine.diff({"events": [old]}, {"events": []}, 2, {})
        self.assertEqual(changes1[0]["type"], "STALE")
        changes2, pending2 = diff_engine.diff({"events": []}, {"events": []}, 2, pending1)
        self.assertEqual(changes2[0]["type"], "CANCELLED")
        self.assertEqual(pending2, {})
        changes3, pending3 = diff_engine.diff({"events": []}, {"events": []}, 2, pending2)
        self.assertEqual(changes3, [])
        self.assertEqual(pending3, {})


class NormalizationTests(unittest.TestCase):
    def test_bls_time_conflict_is_recorded(self) -> None:
        raw = {
            "fetched_at": common.now_utc_iso(),
            "fred": {"cpi": {"release_id": 10, "dates": ["2026-08-20"]}},
            "priors": {},
            "bls": [{
                "summary": "Consumer Price Index release",
                "datetime": "2026-08-20T09:30:00-04:00",
                "has_time": True,
            }],
            "treasury": [],
            "manual": {},
        }
        whitelist = [{
            "key": "cpi",
            "label": "CPI",
            "tier": "A",
            "match": ["Consumer Price Index"],
            "time_et": "08:30",
        }]
        conflicts: list[dict] = []
        normalize.normalize_macro(raw, whitelist, conflicts)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["static_table_et"], "08:30")
        self.assertEqual(conflicts[0]["bls_ics_et"], "09:30")


class EarningsTests(unittest.TestCase):
    def test_vendor_agreement_is_not_company_confirmation(self) -> None:
        result = fetch_earnings.reconcile(
            "TEST", [{"date": "2026-08-20", "hour": "amc"}],
            ["2026-08-20"], "conservative",
        )
        self.assertEqual(result[0]["date_confidence"], "estimated")
        self.assertEqual(result[0]["vendor_corroboration"], "agreed")

    def test_watchlist_preserves_canonical_symbol_with_provider_alias(self) -> None:
        core, monitor, aliases = fetch_earnings.parse_watchlist({
            "core": [{"ticker": "BRK.B", "yfinance_ticker": "BRK-B"}],
            "monitor": [{"ticker": "GOOGL"}],
        })
        self.assertEqual(core, ["BRK.B"])
        self.assertEqual(monitor, ["GOOGL"])
        self.assertEqual(aliases["BRK.B"], {
            "finnhub": "BRK.B", "yfinance": "BRK-B",
        })

    def test_watchlist_rejects_duplicate_symbols_across_tiers(self) -> None:
        with self.assertRaises(ValueError):
            fetch_earnings.parse_watchlist({
                "core": [{"ticker": "NVDA"}],
                "monitor": [{"ticker": "nvda"}],
            })

    def test_watchlist_rejects_empty_and_colliding_provider_aliases(self) -> None:
        with self.assertRaises(ValueError):
            fetch_earnings.parse_watchlist({
                "core": [{"ticker": "NVDA", "yfinance_ticker": " "}],
            })
        with self.assertRaises(ValueError):
            fetch_earnings.parse_watchlist({
                "core": [
                    {"ticker": "BRK.B", "yfinance_ticker": "BRK-B"},
                    {"ticker": "BRK-B", "yfinance_ticker": "BRK-B"},
                ],
            })

    def test_reconcile_does_not_compare_different_earnings_cycles(self) -> None:
        result = fetch_earnings.reconcile(
            "NVDA", [{"date": "2027-05-18", "hour": "amc"}],
            ["2026-08-27"], "conservative",
        )
        self.assertEqual({row["date"] for row in result},
                         {"2027-05-18", "2026-08-27"})
        self.assertTrue(all(row["disagreement"] is None for row in result))

    def test_reconcile_nearby_disagreement_uses_both_provenances(self) -> None:
        result = fetch_earnings.reconcile(
            "TEST", [{"date": "2026-08-20", "hour": "amc"}],
            ["2026-08-25"], "conservative",
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["date"], "2026-08-25")
        self.assertEqual(result[0]["sources"], ["finnhub", "yfinance"])
        self.assertEqual(result[0]["vendor_corroboration"], "disagreed")

    def test_yfinance_exception_is_structured_failure(self) -> None:
        fake = types.SimpleNamespace(
            Ticker=mock.Mock(side_effect=RuntimeError("provider down")))
        with mock.patch.dict(sys.modules, {"yfinance": fake}):
            rows, failures = fetch_earnings.yfinance_earnings(["BRK-B"])
        self.assertEqual(rows, {})
        self.assertEqual(failures[0]["source"], "yfinance")
        self.assertEqual(failures[0]["key"], "BRK-B")

    def test_yfinance_empty_calendar_is_structured_failure(self) -> None:
        fake = types.SimpleNamespace(
            Ticker=mock.Mock(return_value=types.SimpleNamespace(calendar=None)))
        with mock.patch.dict(sys.modules, {"yfinance": fake}):
            rows, failures = fetch_earnings.yfinance_earnings(["NVDA"])
        self.assertEqual(rows, {})
        self.assertEqual(failures[0]["key"], "NVDA")
        self.assertIn("not a mapping", failures[0]["reason"])


class RenderingTests(unittest.TestCase):
    def test_estimated_earnings_not_in_confirmed_week_schedule(self) -> None:
        day = common.today_et() + dt.timedelta(days=1)
        iso, time_confidence = common.et_to_utc(day, "16:30")
        estimated = {
            "id": f"earnings:TEST:{day.isoformat()}",
            "kind": "earnings",
            "label": "TEST 财报（盘后）",
            "date_utc": iso,
            "tier": "B",
            "time_confidence": time_confidence,
            "date_confidence": "estimated",
            "source": "finnhub",
            "source_fetched_at": common.now_utc_iso(),
            "watchlist": "core",
            "prior_value": None,
            "consensus": None,
            "nowcast": None,
            "notes": [],
        }
        cfg = common.load_yaml("settings.yaml")
        doc = {
            "events": [estimated],
            "failures": [],
            "source_fetched_at": {"macro": common.now_utc_iso()},
            "blackout_profile": {},
        }
        output = render.render_week(doc, [], cfg, short=False)
        schedule, possible = output.split("## ❓ 可能落在本周（日期未确认）")
        self.assertNotIn("TEST 财报", schedule)
        self.assertIn("TEST 财报", possible)

    def test_all_short_renderers_respect_line_cap(self) -> None:
        cfg = common.load_yaml("settings.yaml")
        start = common.today_et()
        events = [event(f"manual:test{i}:{(start + dt.timedelta(days=i % 5)).isoformat()}",
                        start + dt.timedelta(days=i % 5)) for i in range(30)]
        doc = {
            "events": events,
            "failures": [],
            "source_fetched_at": {"macro": common.now_utc_iso()},
            "blackout_profile": {},
        }
        for renderer in (render.render_day, render.render_week, render.render_month):
            with self.subTest(renderer=renderer.__name__):
                output = renderer(doc, [], cfg, short=True)
                self.assertLessEqual(len(output.rstrip().splitlines()), 15)


class NoKeyTests(unittest.TestCase):
    def test_env_key_missing_exits_cleanly(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(SystemExit) as ctx:
                common.env_key("FRED_API_KEY")
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
