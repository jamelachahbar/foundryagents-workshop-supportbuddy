"""Your SupportBuddy agent. You will extend this file stage by stage (see docs/workshop.md).

Run from the src folder:
    uv run python main.py            # opens DevUI in your browser
    uv run python main.py "prompt"   # answers one prompt in the console and shows the tool calls

Stuck? Every stage has a complete reference in solutions/. To catch up, copy
solutions/stage_N_<name>.py over main.py and stage_N_<name>.instructions.md over
main.instructions.md.
"""

from lab_agent import build_agent, run


def tools() -> list:
    return []


if __name__ == "__main__":
    run(build_agent(__file__, tools()))
