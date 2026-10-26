"""Stage 1 - Agent only.

The agent understands the support request but cannot inspect any system.

Run from the src folder:  uv run python -m solutions.stage_1_agent_only ["prompt"]
"""

from lab_agent import build_agent, run


def tools() -> list:
    return []


if __name__ == "__main__":
    run(build_agent(__file__, tools()))
