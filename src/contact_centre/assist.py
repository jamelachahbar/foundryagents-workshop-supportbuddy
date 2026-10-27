"""The advisor assist agent: AI help for the human advisor, never for the customer directly.

In Dynamics 365 Contact Center this role is played by Copilot for service representatives
(summaries, suggested replies, ask a question). Here it is a second Agent Framework agent with the
same diagnostic tools as SupportBuddy. Its state-changing tool still needs approval: the advisor's.
"""

from __future__ import annotations

from agent_framework import Agent

from lab_agent import agent_name, build_agent
from tools.support_tools import (
    check_equipment,
    get_handover_status,
    get_known_incidents,
    get_line_status,
    identify_customer,
    restart_modem,
)


def build_assist_agent() -> Agent:
    return build_agent(
        __file__,
        [identify_customer, get_line_status, get_known_incidents, check_equipment, get_handover_status, restart_modem],
        name=f"{agent_name()}-assist",
    )
