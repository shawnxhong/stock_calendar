"""Reference local adapters safe for development and shadow runs."""
from __future__ import annotations

import os
from pathlib import Path

from common import atomic_write_text, read_json, write_json


class EnvironmentSecrets:
    def get(self, name: str) -> str | None:
        return os.environ.get(name)


class JsonFileStateStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict:
        return read_json(self.path, {}) or {}

    def save(self, state: dict) -> None:
        write_json(self.path, state)


class DirectoryDelivery:
    """Write report pairs atomically enough for local/shadow consumption.

    A production IM/email implementation should satisfy DeliveryAdapter and
    use idempotency_key at the provider boundary.
    """

    def __init__(self, directory: Path):
        self.directory = directory

    def deliver(self, *, tier: str, short: str, long: str,
                idempotency_key: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        stem = idempotency_key
        atomic_write_text(self.directory / f"{stem}.md", long)
        atomic_write_text(self.directory / f"{stem}-short.md", short)
