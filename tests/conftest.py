import pytest


@pytest.fixture(autouse=True)
def _isolated_lab_state(tmp_path, monkeypatch):
    # The handover queue is a file shared with the operator desk; tests must never touch the real one.
    monkeypatch.setenv("LAB_STATE_DIR", str(tmp_path / "lab_state"))
