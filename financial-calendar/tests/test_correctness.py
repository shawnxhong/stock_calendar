from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402
import mechanical_calendar  # noqa: E402
import normalize  # noqa: E402
import diff_engine  # noqa: E402
import fetch_macro  # noqa: E402


class FomcTests(unittest.TestCase):
    def test_non_sep_meeting_still_has_presser(self) -> None:
        raw = {
            "fetched_at": common.now_utc_iso(), "fred": {}, "treasury": [],
            "manual": {"fomc": [{"decision": "2026-10-28", "sep": False,
                                     "presser": True, "minutes": "2026-11-18"}]},
        }
        whitelist = [
            {"key": "fomc_decision", "tier": "A", "time_et": "14:00"},
            {"key": "fomc_presser", "tier": "A", "time_et": "14:30"},
            {"key": "fomc_minutes", "tier": "A", "time_et": "14:00"},
        ]
        labels = {e["label"] for e in normalize.normalize_macro(raw, whitelist, [])}
        self.assertEqual(labels, {"FOMC 利率决议", "FOMC 主席新闻发布会", "FOMC 会议纪要"})

    def test_manual_verification_and_source_are_preserved(self) -> None:
        source = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
        raw = {
            "fetched_at": common.now_utc_iso(), "fred": {}, "treasury": [],
            "manual": {"fomc": [{"decision": "2026-10-28", "presser": True,
                                  "verified": True, "source": source}]},
        }
        whitelist = [
            {"key": "fomc_decision", "tier": "A", "time_et": "14:00"},
            {"key": "fomc_presser", "tier": "A", "time_et": "14:30"},
        ]
        events = normalize.normalize_macro(raw, whitelist, [])
        self.assertTrue(all(e["date_confidence"] == "confirmed" for e in events))
        self.assertTrue(all(e["source"] == source for e in events))
        self.assertEqual({e["source_key"] for e in events},
                         {"fomc_decision", "fomc_presser"})

    def test_unverified_private_release_is_estimated(self) -> None:
        raw = {
            "fetched_at": common.now_utc_iso(), "fred": {}, "treasury": [],
            "manual": {"private": [{"key": "umich", "date": "2026-08-14",
                                     "verified": False}]},
        }
        whitelist = [{"key": "umich", "label": "Michigan", "tier": "B",
                      "time_et": "10:00"}]
        event = normalize.normalize_macro(raw, whitelist, [])[0]
        self.assertEqual(event["date_confidence"], "estimated")


class PolicyClassificationTests(unittest.TestCase):
    def test_manual_event_is_policy(self) -> None:
        original = mechanical_calendar.load_yaml
        mechanical_calendar.load_yaml = lambda _: {"manual_events": [{
            "date": "2026-08-21", "label": "Jackson Hole 主席讲话",
            "tier": "A", "time_et": "10:00",
        }]}
        try:
            events = mechanical_calendar.generate(dt.date(2026, 8, 20), 2)
        finally:
            mechanical_calendar.load_yaml = original
        policy = next(e for e in events if e["label"] == "Jackson Hole 主席讲话")
        self.assertEqual(policy["kind"], "policy")


class EnrichmentTests(unittest.TestCase):
    def _earning(self) -> dict:
        return {
            "id": "earnings:TEST:2026-08-20", "kind": "earnings",
            "label": "TEST 财报", "date_confidence": "estimated",
            "notes": ["日期未确认，勿据此安排头寸"],
            "consensus": None, "nowcast": None,
        }

    def test_ir_override_persists_audited_confirmation(self) -> None:
        event = self._earning()
        overrides = {"events": {event["id"]: {
            "date_confidence": "confirmed",
            "confirmation": {"source": "https://ir.example.test/release",
                             "fetched_at": "2026-08-12T10:00:00Z"},
        }}}
        normalize.apply_overrides([event], overrides)
        self.assertEqual(event["date_confidence"], "confirmed")
        self.assertNotIn("日期未确认，勿据此安排头寸", event["notes"])

    def test_confirmation_without_source_is_rejected(self) -> None:
        event = self._earning()
        overrides = {"events": {event["id"]: {
            "date_confidence": "confirmed", "confirmation": {},
        }}}
        with self.assertRaises(ValueError):
            normalize.apply_overrides([event], overrides)


class FailureFallbackTests(unittest.TestCase):
    def test_failed_fred_key_carries_previous_future_event(self) -> None:
        future = common.today_et() + dt.timedelta(days=7)
        iso, tconf = common.et_to_utc(future, "08:30")
        old = {
            "id": f"fred:10:{future.isoformat()}", "kind": "macro", "label": "CPI",
            "date_utc": iso, "time_confidence": tconf, "source_key": "cpi",
            "source": "FRED release_id=10", "notes": [],
        }
        result = normalize.carry_forward_failed_sources(
            [], {"events": [old]}, [{"source": "fred", "key": "cpi"}])
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["carried_forward"])
        self.assertEqual(result[0]["source_fetched_at"] if "source_fetched_at" in result[0] else None,
                         old.get("source_fetched_at"))

    def test_successful_source_omission_is_not_carried(self) -> None:
        future = common.today_et() + dt.timedelta(days=7)
        iso, tconf = common.et_to_utc(future, "08:30")
        old = {"id": "fred:10:x", "kind": "macro", "date_utc": iso,
               "time_confidence": tconf, "source_key": "cpi",
               "source": "FRED release_id=10", "notes": []}
        self.assertEqual(normalize.carry_forward_failed_sources([], {"events": [old]}, []), [])


class DiffIdentityTests(unittest.TestCase):
    def test_new_recurring_release_does_not_move_past_release(self) -> None:
        past = common.today_et() - dt.timedelta(days=1)
        future = common.today_et() + dt.timedelta(days=30)
        old = self._event(f"fred:10:{past.isoformat()}", past)
        new = self._event(f"fred:10:{future.isoformat()}", future)
        changes, _ = diff_engine.diff({"events": [old]}, {"events": [new]}, 2, {})
        self.assertEqual([c["type"] for c in changes], ["NEW"])

    @staticmethod
    def _event(event_id, day):
        iso, tconf = common.et_to_utc(day, "08:30")
        return {"id": event_id, "kind": "macro", "label": "CPI", "date_utc": iso,
                "tier": "A", "time_confidence": tconf, "date_confidence": "confirmed",
                "source": "FRED release_id=10", "notes": []}


class OfficialScheduleTests(unittest.TestCase):
    def test_parse_census_calendar(self) -> None:
        html = """
        <table id="calendar">
          <tr><th>Indicator</th><th>Release Date</th><th>Time</th><th>Period Covered</th></tr>
          <tr><td>Advance Monthly Sales for Retail and Food Services</td>
              <td>August 14, 2026</td><td>8:30 AM</td><td>July 2026</td></tr>
        </table>
        """
        rows = fetch_macro.parse_census_calendar(
            html, dt.date(2026, 8, 1), dt.date(2026, 8, 31))
        self.assertEqual(rows, [{
            "title": "Advance Monthly Sales for Retail and Food Services",
            "date": "2026-08-14", "time_et": "08:30", "period": "July 2026",
        }])

    def test_bea_crosscheck_can_fill_missing_fred_date(self) -> None:
        raw = {
            "fetched_at": common.now_utc_iso(), "fred": {}, "treasury": [],
            "bea": [{"title": "Gross Domestic Product",
                     "datetime": "2026-08-26T12:30:00+00:00"}],
            "census": [], "manual": {},
        }
        whitelist = [{"key": "gdp", "label": "GDP", "tier": "B",
                      "match": ["Gross Domestic Product"],
                      "official_match": ["Gross Domestic Product"],
                      "time_et": "08:30"}]
        events = normalize.normalize_macro(raw, whitelist, [])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["source_key"], "gdp")
        self.assertEqual(events[0]["source"], "BEA release_dates.json")

    def test_similar_bea_title_does_not_impersonate_national_gdp(self) -> None:
        raw = {"bea": [{"title": "Gross Domestic Product by State and Personal Income by State",
                         "datetime": "2026-09-30T12:30:00+00:00"}], "census": []}
        whitelist = [{"key": "gdp", "official_match": ["Gross Domestic Product"]}]
        self.assertEqual(normalize._official_schedule_index(raw, whitelist), {})

    def test_parse_ism_official_calendar(self) -> None:
        html = """
        <table><thead><tr><th>Month</th><th>Manufacturing PMI</th>
        <th>Services PMI</th></tr></thead><tbody>
        <tr><th>September 2026</th><td>1</td><td>3</td></tr>
        </tbody></table>
        """
        rows = fetch_macro.parse_ism_calendar(
            html, dt.date(2026, 9, 1), dt.date(2026, 9, 30))
        self.assertEqual(rows, [
            {"key": "ism_manufacturing", "date": "2026-09-01",
             "time_et": "10:00"},
            {"key": "ism_services", "date": "2026-09-03",
             "time_et": "10:00"},
        ])

    def test_parse_adp_stops_before_weekly_pulse_dates(self) -> None:
        data = {"reportType": "NER", "futureReports": [
            {"reportDate": "September 02, 2026"},
            {"reportDate": ""},
            {"reportDate": "Upcoming reports (weekly NER pulse):"},
            {"reportDate": "September 09, 2026"},
        ]}
        rows = fetch_macro.parse_adp_calendar(
            data, dt.date(2026, 9, 1), dt.date(2026, 9, 30))
        self.assertEqual(rows, [
            {"key": "adp", "date": "2026-09-02", "time_et": "08:15"},
        ])

    def test_official_private_rows_normalize_as_confirmed(self) -> None:
        raw = {
            "fetched_at": common.now_utc_iso(), "fred": {}, "treasury": [],
            "ism": [{"key": "ism_services", "date": "2026-09-03",
                     "time_et": "10:00"}],
            "adp": [{"key": "adp", "date": "2026-09-02", "time_et": "08:15"}],
            "manual": {},
        }
        whitelist = [
            {"key": "ism_services", "label": "ISM Services", "tier": "B",
             "time_et": "10:00"},
            {"key": "adp", "label": "ADP", "tier": "B", "time_et": "08:15"},
        ]
        events = normalize.normalize_macro(raw, whitelist, [])
        self.assertEqual({e["source_key"] for e in events},
                         {"ism_services", "adp"})
        self.assertTrue(all(e["date_confidence"] == "confirmed" for e in events))


class DoctorSourceContractTests(unittest.TestCase):
    def test_empty_census_table_is_failure_not_no_events(self) -> None:
        original = fetch_macro.http_get
        fetch_macro.http_get = lambda *args, **kwargs: b"<table id='calendar'></table>"
        try:
            rows = fetch_macro.census_schedule(dt.date(2026, 1, 1), dt.date(2026, 12, 31))
        finally:
            fetch_macro.http_get = original
        self.assertIsNone(rows)

    def test_ism_retries_with_same_session_after_sso_cookie_page(self) -> None:
        html = b"""
        <table><tr><th>Month</th><th>Manufacturing PMI</th><th>Services PMI</th></tr>
        <tr><th>September 2026</th><td>1</td><td>3</td></tr></table>
        """

        class Response:
            def __init__(self, content):
                self.content = content

            def raise_for_status(self):
                return None

        class Session:
            def __init__(self):
                self.headers = {}
                self.calls = 0

            def get(self, *_args, **_kwargs):
                self.calls += 1
                return Response(b"<html>SSO cookie setup</html>" if self.calls == 1 else html)

        with unittest.mock.patch("requests.Session", Session):
            rows = fetch_macro.ism_schedule(
                dt.date(2026, 9, 1), dt.date(2026, 9, 30))
        self.assertEqual(len(rows), 2)


class DotenvTests(unittest.TestCase):
    def test_dotenv_loads_without_overriding_process_secret(self) -> None:
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / ".env"
            path.write_text("FRED_API_KEY=file-value\nNEW_CAL_KEY='quoted'\n",
                            encoding="utf-8")
            with unittest.mock.patch.dict(
                    os.environ, {"FRED_API_KEY": "process-value"}, clear=True):
                common.load_dotenv(path)
                self.assertEqual(os.environ["FRED_API_KEY"], "process-value")
                self.assertEqual(os.environ["NEW_CAL_KEY"], "quoted")


if __name__ == "__main__":
    unittest.main()
