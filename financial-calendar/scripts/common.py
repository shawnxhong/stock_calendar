"""Shared utilities: paths, config loading, timezone handling, HTTP.

Timezone rule (do not violate): every instant is stored as UTC in events.json.
Conversion to ET / Beijing happens ONLY at render time via zoneinfo. Hand-written
UTC offsets are forbidden — US daylight-saving transitions would silently shift
release times by one hour, in the direction that makes you late.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
DATA = Path(os.environ.get("FINCAL_DATA_DIR", ROOT / "data")).expanduser().resolve()
SNAPSHOTS = DATA / "snapshots"
LOGS = Path(os.environ.get("FINCAL_LOG_DIR", ROOT / "logs")).expanduser().resolve()

UTC = dt.timezone.utc
ET = ZoneInfo("America/New_York")
CN = ZoneInfo("Asia/Shanghai")

for _d in (DATA, SNAPSHOTS, LOGS):
    _d.mkdir(parents=True, exist_ok=True)


def load_dotenv(path: Path | None = None) -> None:
    """Load a local gitignored .env without overriding process secrets."""
    env_path = path or ROOT.parent / ".env"
    if not env_path.exists():
        return
    for lineno, raw in enumerate(env_path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            raise ValueError(f"invalid .env line {lineno}: expected NAME=VALUE")
        name, value = line.split("=", 1)
        name = name.strip()
        if not name.replace("_", "").isalnum() or name[0].isdigit():
            raise ValueError(f"invalid .env variable name on line {lineno}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(name, value)


load_dotenv()


# ── config / io ──────────────────────────────────────────────────────────────

def load_yaml(name: str) -> dict:
    path = CONFIG / name if not name.startswith("/") else Path(name)
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_yaml(path: Path, obj: Any, header: str = "") -> None:
    text = (header.rstrip() + "\n\n" if header else "")
    text += yaml.safe_dump(obj, allow_unicode=True, sort_keys=False)
    atomic_write_text(path, text)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, obj: Any) -> None:
    atomic_write_text(
        path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically replace a UTF-8 text file in its destination directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=path.parent,
                prefix=f".{path.name}.", delete=False) as tmp:
            tmp_name = tmp.name
            tmp.write(text)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_name, path)
    finally:
        if tmp_name and os.path.exists(tmp_name):
            os.unlink(tmp_name)


def settings() -> dict:
    return load_yaml("settings.yaml")


# ── secrets ──────────────────────────────────────────────────────────────────

def env_key(name: str, required: bool = True) -> str | None:
    """Read an API key from the environment.

    Keys are NEVER stored in this repo. Set them in your shell or a
    gitignored .env file sourced before running.
    """
    val = os.environ.get(name)
    if not val and required:
        sys.stderr.write(
            f"[fatal] environment variable {name} is not set.\n"
            f"        export {name}=... (or source your .env) before running.\n")
        raise SystemExit(2)
    return val


# ── time ─────────────────────────────────────────────────────────────────────

def et_to_utc(date: dt.date, time_et: str | None) -> tuple[str, str]:
    """(date, 'HH:MM' in ET) -> (iso_utc, time_confidence).

    time_et=None means the release time is unknown; a noon-ET placeholder is
    stored so the instant sorts on the right calendar day, and the record is
    marked date_only so no renderer ever prints a time for it.
    """
    if time_et:
        hh, mm = (int(x) for x in time_et.split(":"))
        local = dt.datetime(date.year, date.month, date.day, hh, mm, tzinfo=ET)
        return local.astimezone(UTC).isoformat(), "exact"
    local = dt.datetime(date.year, date.month, date.day, 12, 0, tzinfo=ET)
    return local.astimezone(UTC).isoformat(), "date_only"


def parse_utc(iso: str) -> dt.datetime:
    d = dt.datetime.fromisoformat(iso)
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def et_date(iso: str) -> dt.date:
    """Calendar date in ET — the date a US market event 'belongs to'."""
    return parse_utc(iso).astimezone(ET).date()


WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def fmt_dual(iso: str, time_confidence: str) -> str:
    """Render an instant as 'ET | 北京' side by side."""
    u = parse_utc(iso)
    e, c = u.astimezone(ET), u.astimezone(CN)
    if time_confidence != "exact":
        return f"{e:%m/%d}（{WEEKDAY_CN[e.weekday()]}）时点未定"
    same_day = "" if e.date() == c.date() else f"{c:%m/%d} "
    return (f"{e:%m/%d}（{WEEKDAY_CN[e.weekday()]}）{e:%H:%M} ET"
            f" | {same_day}{c:%H:%M} 北京")


def today_et() -> dt.date:
    return dt.datetime.now(UTC).astimezone(ET).date()


def now_utc_iso() -> str:
    return dt.datetime.now(UTC).isoformat()


# ── http ─────────────────────────────────────────────────────────────────────

def http_get(url: str, params: dict | None = None, *, timeout: int = 30,
             retries: int = 3, as_json: bool = True, headers: dict | None = None):
    """GET with linear backoff. Returns None on failure — callers must treat
    None as 'source unavailable', never as 'nothing scheduled'."""
    import requests

    hdrs = {"User-Agent": "financial-calendar-skill/1.0"}
    hdrs.update(headers or {})
    def safe_error(exc: Exception) -> str:
        """Keep transport diagnostics without leaking credentials in URLs."""
        message = str(exc)
        for key, value in (params or {}).items():
            lowered = str(key).lower()
            if value and any(marker in lowered for marker in (
                    "key", "token", "secret", "password", "authorization")):
                message = message.replace(str(value), "[REDACTED]")
        for key, value in (headers or {}).items():
            lowered = str(key).lower()
            if value and any(marker in lowered for marker in (
                    "key", "token", "secret", "password", "authorization")):
                message = message.replace(str(value), "[REDACTED]")
        return f"{type(exc).__name__}: {message}"

    last = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout, headers=hdrs)
            r.raise_for_status()
            return r.json() if as_json else r.content
        except Exception as exc:  # noqa: BLE001
            last = safe_error(exc)
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
    sys.stderr.write(f"[warn] GET failed after {retries} tries: {url} ({last})\n")
    return None


# ── misc ─────────────────────────────────────────────────────────────────────

def norm(s: str) -> str:
    """Normalize a name for fuzzy matching."""
    keep = [ch.lower() if ch.isalnum() else " " for ch in s]
    return " ".join("".join(keep).split())


def tier_rank(tier: str) -> int:
    return {"A": 0, "B": 1, "C": 2}.get(tier, 3)


def is_advisory_failure(failure: dict) -> bool:
    """Return whether a source failure does not remove primary event data.

    The source-name fallback keeps cached snapshots produced before advisory
    severity was added from turning BLS cross-check loss into a hard warning.
    """
    return (failure.get("severity") == "advisory"
            or failure.get("source") == "bls_ics")
