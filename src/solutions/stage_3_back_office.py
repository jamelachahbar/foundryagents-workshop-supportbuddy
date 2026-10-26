"""Stage 3 - Back-office investigation.

request -> customer -> relevant service -> service information

Run from the src folder:  uv run python -m solutions.stage_3_back_office ["prompt"]
"""

from lab_agent import build_agent, run
from tools.support_tools import get_line_status, identify_customer


def tools() -> list:
    return [identify_customer, get_line_status]


if __name__ == "__main__":
    run(build_agent(__file__, tools()))
