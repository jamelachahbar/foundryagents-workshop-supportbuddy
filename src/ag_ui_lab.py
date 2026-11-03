"""Independent live-agent baseline for the optional AG-UI lab.

Uses its own companion instructions and the Stage 9 reference tool factory.
It never loads participant stage files, memory providers or later-stage middleware.
The toolbox and runbooks remain live Azure dependencies, not local fixtures.

From src/: uv run python -m contact_centre ag_ui_lab --evidence-panel
"""

from lab_agent import build_agent, run
from solutions.stage_9_toolbox import tools


if __name__ == "__main__":
    run(build_agent(__file__, tools()))
