"""Deterministic IM delivery dispatcher.

Reads the latest report + health.json produced by run.py and delivers the
short IM version to the configured channels (Feishu, WeChat) via `hermes send`.
Per-channel idempotency is recorded in DATA/im_delivery.json so a partially
failed fan-out retries only the failed channels on the next poll. Each
successful send archives the exact text to IM_ARCHIVE/{key}-{channel}.md.

    python scripts/deliver_im.py             # deliver pending channels
    python scripts/deliver_im.py --dry-run   # print what WOULD be sent, send nothing
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from common import (DATA, atomic_write_text, load_yaml, now_utc_iso,
                    read_json, write_json)

LEDGER = DATA / "im_delivery.json"
IM_ARCHIVE = DATA.parent / "im-delivery"
_BANNER_MARK = "⚠"


def _safe_key(key: str) -> str:
    """Make an idempotency/alert key safe for use as a filename stem."""
    return key.replace(":", "_").replace("/", "_").replace("+", "_")


def _cfg() -> dict:
    c = load_yaml("delivery.yaml")
    return {"channels": c.get("channels") or [],
            "alerts": c.get("alert_channels") or []}


def load_ledger() -> dict:
    return read_json(LEDGER, {}) or {}


def save_ledger(ledger: dict) -> None:
    write_json(LEDGER, ledger)


def channel_done(ledger: dict, key: str, channel: str) -> bool:
    entry = (ledger.get(key) or {}).get(channel)
    return bool(entry and entry.get("status") == "ok")


def hermes_send(target: str, text: str) -> bool:
    """Send text to a Hermes channel. True iff exit code 0."""
    proc = subprocess.run(
        ["hermes", "send", "--to", target, "--quiet", text],
        capture_output=True, text=True, timeout=60)
    return proc.returncode == 0


def _alert_text(health: dict) -> str:
    tier = health.get("tier", "?")
    fails = health.get("failures") or []
    lines = [f"⚠️ 财经日历（{tier}）运行异常，未发送常规简报", ""]
    for f in fails[:5]:
        lines.append(f"  - {f.get('source', 'unknown')}: {f.get('reason', '')}")
    lines += ["", f"checked_at {health.get('checked_at', '?')}"]
    return "\n".join(lines)


def _short_path(health: dict) -> Path | None:
    for p in health.get("outputs") or []:
        if p.endswith("-short.md"):
            return Path(p)
    return None


def _long_path(health: dict) -> Path | None:
    """Long (non-short) report path from health.outputs, if any."""
    for p in health.get("outputs") or []:
        if p.endswith(".md") and not p.endswith("-short.md"):
            return Path(p)
    return None


def _resolve_texts(cfg: dict, health: dict, short_text: str) -> dict[str, str]:
    """Map channel name -> body, honoring per-channel ``version`` (short/long).

    ``version: long`` channels (e.g. email) receive the full report; every
    other channel receives the short IM version.
    """
    long_text = short_text
    lp = _long_path(health)
    if lp and lp.exists():
        long_text = lp.read_text(encoding="utf-8").strip()
    return {
        ch["name"]: long_text if ch.get("version") == "long" else short_text
        for ch in cfg.get("channels") or []
        if isinstance(ch, dict)
    }


def _fan_out(entries: list, ledger: dict, key: str, text: str | dict,
             dry_run: bool) -> list[str]:
    """Deliver to each pending channel; return failed channel names.

    ``entries`` may be channel names (alerts — same body for all) or channel
    dicts. ``text`` is either a str (same body for every channel) or a dict
    mapping channel name -> body (per-channel short/long routing).
    """
    failed = []
    for ch in entries:
        if isinstance(ch, str):
            name = target = ch
        else:
            name = ch["name"]
            target = ch.get("target") or ch["name"]
        body: str = text[name] if isinstance(text, dict) else text
        if channel_done(ledger, key, name):
            continue
        if dry_run:
            print(f"[dry-run] -> {name} ({target}):\n{body}\n")
            continue
        if hermes_send(target, body):
            ledger.setdefault(key, {})[name] = {"status": "ok", "at": now_utc_iso()}
            atomic_write_text(IM_ARCHIVE / f"{_safe_key(key)}-{name}.md", body)
            print(f"[ok] {key} -> {name}")
        else:
            ledger.setdefault(key, {})[name] = {"status": "failed", "at": now_utc_iso()}
            failed.append(name)
            print(f"[fail] {key} -> {name}")
    return failed


def dispatch(health: dict, cfg: dict, ledger: dict, dry_run: bool) -> int:
    """Apply health gating and fan out. Mutates ledger; returns exit code."""
    status = health.get("status", "healthy")

    if status == "unhealthy":
        key = "alert:" + health.get("checked_at", "unknown")
        _fan_out(cfg["alerts"], ledger, key, _alert_text(health), dry_run)
        return 0

    short = _short_path(health)
    if not short or not short.exists():
        return 0

    text = short.read_text(encoding="utf-8").strip()
    if status == "degraded" and _BANNER_MARK not in text[:200]:
        text = "⚠ 部分数据源异常，简报可能不完整\n\n" + text

    key = (health.get("delivery") or {}).get("idempotency_key")
    if not key:
        # A render-only run (run.py --no-fetch without FINCAL_DELIVERY_DIR)
        # overwrote health.json without an idempotency key. Falling back to
        # the filename stem here re-delivers already-sent content under a new
        # key, so we refuse instead.
        print("[skip] delivery.idempotency_key 缺失 —— 拒绝发送（不做文件名兜底）")
        return 0

    texts = _resolve_texts(cfg, health, text)
    failed = _fan_out(cfg["channels"], ledger, key, texts, dry_run)
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    health = read_json(DATA / "health.json", {}) or {}
    if not health:
        return 0
    cfg = _cfg()
    ledger = load_ledger()
    code = dispatch(health, cfg, ledger, args.dry_run)
    save_ledger(ledger)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
