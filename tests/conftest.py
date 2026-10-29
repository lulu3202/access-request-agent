import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import store  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_data(tmp_path, monkeypatch):
    """Each test gets its own copy of the seed in a temp folder."""
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    store.reset_data()
    return tmp_path
