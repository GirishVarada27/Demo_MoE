import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(autouse=True)
def _isolate_mock_stores(tmp_path, monkeypatch):
    """Every test writes escalation tickets and drafts to its own tmp_path,
    never to the real data/*.json stores -- including tests that reach an
    escalation path they didn't plan for (e.g. a fake model running out of
    scripted replies). Tests that patch these paths themselves still can."""
    monkeypatch.setattr("tools.escalation._QUEUE_PATH", tmp_path / "escalation_queue.json")
    monkeypatch.setattr("tools.draft_application._STORE_PATH", tmp_path / "draft_applications.json")
