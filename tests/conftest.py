import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import agent, store  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_data(tmp_path, monkeypatch):
    """Each test gets its own copy of the seed in a temp folder."""
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    # The app's real STOP file (Pause agent) must not leak into tests.
    monkeypatch.setattr(agent, "STOP_FILE", tmp_path / "STOP")
    store.reset_data()
    return tmp_path
