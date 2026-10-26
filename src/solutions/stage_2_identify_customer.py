"""Stage 2 - Customer identification.

request -> customer identification

Run from the src folder:  uv run python -m solutions.stage_2_identify_customer ["prompt"]
"""

from lab_agent import build_agent, run
from tools.support_tools import identify_customer


def tools() -> list:
    return [identify_customer]


if __name__ == "__main__":
    run(build_agent(__file__, tools()))
