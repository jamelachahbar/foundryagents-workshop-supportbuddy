"""Stage 4 - Technical diagnostic.

request -> customer -> service -> equipment -> technical check (simulated ping)

Run from the src folder:  uv run python -m solutions.stage_4_diagnostic ["prompt"]
"""

from lab_agent import build_agent, run
from tools.support_tools import check_equipment, get_line_status, identify_customer


def tools() -> list:
    return [identify_customer, get_line_status, check_equipment]


if __name__ == "__main__":
    run(build_agent(__file__, tools()))
