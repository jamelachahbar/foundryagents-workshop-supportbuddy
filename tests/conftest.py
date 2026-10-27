import pytest


@pytest.fixture(autouse=True)
def _isolated_lab_state(tmp_path, monkeypatch):
    # The handover queue is a file shared by every process that runs the tools; tests must never touch the real one.
    monkeypatch.setenv("LAB_STATE_DIR", str(tmp_path / "lab_state"))
    # Tests set their own settings; a participant's src/.env must not leak into them.
    import lab_agent

    monkeypatch.setattr(lab_agent, "load_dotenv", lambda *args, **kwargs: False)
