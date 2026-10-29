"""The synthetic scenario clock is independent of the workshop's actual dates."""

from datetime import datetime
from pathlib import Path

import pytest

import lab_agent
from lab_backend import backend

SRC = Path(__file__).resolve().parents[1] / "src"
SCENARIO_TIME = "2026-11-18T10:00:00Z"


@pytest.mark.parametrize(
    "path",
    sorted(SRC.rglob("*.instructions.md")),
    ids=lambda path: str(path.relative_to(SRC)),
)
def test_every_instruction_file_receives_the_shared_scenario_time(path):
    instructions = lab_agent.load_instructions(str(path))

    assert instructions.startswith("# Synthetic scenario time\n")
    assert SCENARIO_TIME in instructions
    assert 'Interpret "today", "this morning" and "yesterday"' in instructions
    assert "date, time and timezone" in instructions
    assert instructions.endswith(path.read_text(encoding="utf-8").strip())


def test_script_and_instruction_paths_load_the_same_context():
    script = SRC / "solutions" / "stage_7_answer_or_handoff.py"

    assert lab_agent.load_instructions(str(script)) == lab_agent.load_instructions(
        str(script.with_suffix(".instructions.md"))
    )


@pytest.mark.parametrize("run_date", ["2025-03-10", "2027-07-14", "2030-01-08"])
def test_run_dates_do_not_age_the_snapshot_or_freeze_operational_records(monkeypatch, run_date):
    actual_time = f"{run_date}T16:00:00+00:00"
    monkeypatch.setattr(backend, "_now", lambda: actual_time)
    instructions = lab_agent.load_instructions(str(SRC / "main.py"))
    incident = backend.get_known_incidents(postcode="MV-0001")["incidents"][0]
    scenario_time = datetime.fromisoformat(lab_agent.SCENARIO_TIME)

    assert lab_agent.SCENARIO_TIME == SCENARIO_TIME
    assert SCENARIO_TIME in instructions
    assert run_date not in instructions
    assert incident["status"] == "open"
    assert datetime.fromisoformat(incident["started"]) < scenario_time
    assert scenario_time < datetime.fromisoformat(incident["estimated_restore"])
    assert scenario_time != datetime.fromisoformat(actual_time)
    assert backend.get_known_incidents(postcode="MV-0005")["incidents"][0]["status"] == "resolved"

    handover = backend.escalate_to_human("No internet", [], [], "Unknown cause", "Check service")
    assert handover["created_at"] == actual_time
