"""ASGI entry point: ``uv run uvicorn wakequest.main:app --host 127.0.0.1 --port 8000``.

Environment: WAKEQUEST_DATA_DIR, WAKEQUEST_DEVICE (=fake), WAKEQUEST_DEVICE_HOST,
WAKEQUEST_DEVICE_PORT, WAKEQUEST_TEST_MODE (=1), WAKEQUEST_TEST_ALARM_DELAY_S,
WAKEQUEST_FAKE_DEVICE_PORT.
"""

from __future__ import annotations

import logging

from .api import create_app
from .settings import Settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

settings = Settings.from_env()
app = create_app(settings)
