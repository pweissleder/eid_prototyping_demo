"""WakeQuest local service.

The service is the single source of truth: it owns the alarm schedule, the
session state machine, tap validation, derived points and the event log. The
website renders snapshots pushed over ``/ws``; the ESP32 is reached through
``device_link`` (protocol in ``docs/protocol.md``).
"""

__version__ = "0.1.0"
