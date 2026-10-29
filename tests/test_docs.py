"""docs/api.md is the website contract: its snapshot examples must match the model."""

from __future__ import annotations

import json
import re
from pathlib import Path

from wakequest.models import Snapshot

DOC = Path(__file__).resolve().parent.parent / "docs" / "api.md"


def test_api_doc_snapshot_examples_validate():
    blocks = re.findall(r"```json\n(.*?)\n```", DOC.read_text(encoding="utf-8"), re.S)
    snaps = [json.loads(b) for b in blocks if '"type": "snapshot"' in b]
    states = {s["run"]["state"] for s in snaps}
    assert states == {"idle", "armed", "test_pending", "ringing", "completed", "aborted"}
    assert any(s["device"]["status"] == "reconnecting" for s in snaps)
    for s in snaps:
        assert Snapshot.model_validate(s).model_dump(mode="json") == s
