"""Stage 5 - Correlation.

customer context + service information + equipment diagnostic + operational information -> conclusion

Run from the src folder:  uv run python -m solutions.stage_5_correlation ["prompt"]
"""

from lab_agent import build_agent, run
from tools.support_tools import check_equipment, get_known_incidents, get_line_status, identify_customer


def tools() -> list:
    return [identify_customer, get_line_status, get_known_incidents, check_equipment]


if __name__ == "__main__":
    run(build_agent(__file__, tools()))
