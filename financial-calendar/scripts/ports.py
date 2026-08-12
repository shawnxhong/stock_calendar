"""Production-facing ports.

Core calendar logic stays transport- and scheduler-agnostic. Production code
implements these protocols around the deterministic scripts; Hermes is not a
dependency and no local scheduler is enabled by this module.
"""
from __future__ import annotations

from typing import Protocol


class SecretProvider(Protocol):
    def get(self, name: str) -> str | None: ...


class StateStore(Protocol):
    def load(self) -> dict: ...
    def save(self, state: dict) -> None: ...


class DeliveryAdapter(Protocol):
    def deliver(self, *, tier: str, short: str, long: str,
                idempotency_key: str) -> None: ...


class HealthReporter(Protocol):
    def report(self, *, healthy: bool, details: dict) -> None: ...


class Scheduler(Protocol):
    def install(self, jobs: list[dict]) -> None: ...
