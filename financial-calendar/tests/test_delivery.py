"""Tests for the deterministic IM delivery dispatcher (deliver_im.py)."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import deliver_im


CFG = {
    "channels": [
        {"name": "feishu", "target": "feishu:oc_x"},
        {"name": "weixin", "target": "weixin:o9_x"},
    ],
    "alerts": ["feishu", "telegram"],
}


class DeliveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        # Point ledger + archive at temp paths so tests never touch runtime/.
        deliver_im.LEDGER = self.dir / "im_delivery.json"
        deliver_im.IM_ARCHIVE = self.dir / "im-delivery"

    def tearDown(self):
        self.tmp.cleanup()

    def _health(self, status, **kw):
        h = {"status": status, "tier": "day", "outputs": [], "failures": []}
        h.update(kw)
        return h

    def _short_file(self, text):
        p = self.dir / "day-short.md"
        p.write_text(text, encoding="utf-8")
        return p


class ChannelDoneTest(DeliveryTest):
    def test_marks_ok_only(self):
        ledger = {"k": {"feishu": {"status": "ok"}, "weixin": {"status": "failed"}}}
        self.assertTrue(deliver_im.channel_done(ledger, "k", "feishu"))
        self.assertFalse(deliver_im.channel_done(ledger, "k", "weixin"))
        self.assertFalse(deliver_im.channel_done(ledger, "k", "telegram"))


class FanOutTest(DeliveryTest):
    def test_partial_failure_retries_only_failed_channel(self):
        ledger = {"k": {"feishu": {"status": "ok"}, "weixin": {"status": "failed"}}}
        calls = []
        with mock.patch.object(deliver_im, "hermes_send",
                               side_effect=lambda t, x: calls.append(t) or True):
            failed = deliver_im._fan_out(CFG["channels"], ledger, "k", "hi",
                                         dry_run=False)
        self.assertEqual(calls, ["weixin:o9_x"])  # feishu skipped
        self.assertEqual(failed, [])
        self.assertEqual(ledger["k"]["weixin"]["status"], "ok")

    def test_records_failure_per_channel(self):
        ledger = {}
        with mock.patch.object(deliver_im, "hermes_send", return_value=False):
            failed = deliver_im._fan_out(CFG["channels"], ledger, "k", "hi",
                                         dry_run=False)
        self.assertEqual(set(failed), {"feishu", "weixin"})
        self.assertEqual(ledger["k"]["feishu"]["status"], "failed")
        self.assertEqual(ledger["k"]["weixin"]["status"], "failed")

    def test_archive_written_on_success(self):
        ledger = {}
        with mock.patch.object(deliver_im, "hermes_send", return_value=True):
            deliver_im._fan_out(CFG["channels"], ledger, "2026-08-14-day-abcd",
                                "CPI\n", dry_run=False)
        for name in ("feishu", "weixin"):
            p = self.dir / "im-delivery" / f"2026-08-14-day-abcd-{name}.md"
            self.assertTrue(p.exists(), f"missing archive for {name}")
            self.assertEqual(p.read_text(encoding="utf-8"), "CPI\n")


class DispatchTest(DeliveryTest):
    def test_unhealthy_sends_alert_not_report(self):
        health = self._health("unhealthy",
                              failures=[{"source": "fred", "reason": "down"}],
                              checked_at="2026-08-14T00:00:00+00:00")
        captured = []
        with mock.patch.object(
                deliver_im, "_fan_out",
                side_effect=lambda e, l, k, t, d: captured.append((e, k, t)) or []):
            code = deliver_im.dispatch(health, CFG, {}, dry_run=False)
        self.assertEqual(code, 0)
        self.assertEqual(len(captured), 1)
        entries, key, text = captured[0]
        self.assertEqual(entries, CFG["alerts"])
        self.assertTrue(key.startswith("alert:"))
        self.assertIn("未发送常规简报", text)

    def test_degraded_prepends_banner_when_missing(self):
        short = self._short_file("今日无事件\n")
        health = self._health("degraded", outputs=["x-day.md", str(short)])
        texts = []
        with mock.patch.object(
                deliver_im, "_fan_out",
                side_effect=lambda e, l, k, t, d: texts.append(t) or []):
            deliver_im.dispatch(health, CFG, {}, dry_run=False)
        self.assertIn("数据源异常", texts[0])

    def test_degraded_keeps_existing_banner(self):
        short = self._short_file("⚠ 数据陈旧 2 天\nCPI\n")
        health = self._health("degraded", outputs=["x-day.md", str(short)])
        texts = []
        with mock.patch.object(
                deliver_im, "_fan_out",
                side_effect=lambda e, l, k, t, d: texts.append(t) or []):
            deliver_im.dispatch(health, CFG, {}, dry_run=False)
        self.assertEqual(texts[0], "⚠ 数据陈旧 2 天\nCPI")  # no extra banner

    def test_no_short_report_is_noop(self):
        health = self._health("healthy", outputs=[])
        with mock.patch.object(deliver_im, "_fan_out") as fan:
            code = deliver_im.dispatch(health, CFG, {}, dry_run=False)
        self.assertEqual(code, 0)
        fan.assert_not_called()


class HermesSendTest(DeliveryTest):
    def test_uses_expected_args_and_exit_zero(self):
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=0)) as run:
            self.assertTrue(deliver_im.hermes_send("feishu:oc_x", "hello"))
        args, _ = run.call_args
        self.assertEqual(args[0][:4], ["hermes", "send", "--to", "feishu:oc_x"])

    def test_nonzero_exit_means_failure(self):
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=1)):
            self.assertFalse(deliver_im.hermes_send("feishu:oc_x", "hello"))


if __name__ == "__main__":
    unittest.main()
