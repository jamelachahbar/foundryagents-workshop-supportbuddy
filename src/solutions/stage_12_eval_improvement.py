"""Stage 12 - Evaluation-driven prompt improvement reference.

Uses Stage 11's tools and customer-scoped memory with improved companion instructions.
It is a candidate to evaluate, not a guarantee of higher scores or Stage 13 enforcement.

From src:
    uv run python evaluate.py solutions.stage_12_eval_improvement --repeat 3 --foundry --foundry-safety
    uv run python -m solutions.stage_12_eval_improvement --customer CUST-1002 ["prompt"]
"""

from lab_agent import build_agent, run, signed_in_customer
from solutions.stage_11_memory import context_providers
from solutions.stage_9_toolbox import tools


if __name__ == "__main__":
    run(build_agent(__file__, tools(), context_providers(signed_in_customer())))
