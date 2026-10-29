"""Service settings, read from WAKEQUEST_* environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(v: str | None) -> bool:
    return (v or "").strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    v = os.environ.get(name)
    return float(v) if v not in (None, "") else default


@dataclass
class Settings:
    data_dir: str = "./data"
    device_mode: str = "real"  # "real" | "fake"
    device_host: str = "wakequest.local"
    device_port: int = 81
    test_mode: bool = False
    test_alarm_delay_s: float = 10.0
    # liveness / timing (docs/protocol.md "Liveness")
    ping_interval_s: float = 1.0
    ping_timeout_s: float = 2.0
    watchdog_s: float = 3.0
    ack_timeout_s: float = 2.0
    hello_timeout_s: float = 3.0
    open_timeout_s: float = 3.0
    resolve_timeout_s: float = 3.0
    backoff_initial_s: float = 0.5
    backoff_cap_active_s: float = 2.0
    backoff_cap_idle_s: float = 5.0
    tick_interval_s: float = 0.2
    # fake device
    fake_device_port: int = 0
    fake_status_interval_s: float = 1.0
    extra: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ
        return cls(
            data_dir=e.get("WAKEQUEST_DATA_DIR", "./data"),
            device_mode="fake" if e.get("WAKEQUEST_DEVICE", "").strip().lower() == "fake" else "real",
            device_host=e.get("WAKEQUEST_DEVICE_HOST", "wakequest.local").strip() or "wakequest.local",
            device_port=int(e.get("WAKEQUEST_DEVICE_PORT", "81")),
            test_mode=_bool(e.get("WAKEQUEST_TEST_MODE")),
            test_alarm_delay_s=_float("WAKEQUEST_TEST_ALARM_DELAY_S", 10.0),
            fake_device_port=int(e.get("WAKEQUEST_FAKE_DEVICE_PORT", "0")),
        )
