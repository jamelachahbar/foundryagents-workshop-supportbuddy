"""Agent Framework function tools for the connectivity-support agent.

LAB CONSTRUCT: each tool is a thin wrapper around the synthetic back-office in
``lab_backend``. The tool names and parameters are the *contract* the agent
relies on; in a real deployment the same contract would be backed by an
enterprise API instead of a JSON file, without changing the agent.

None of these tools call a real system.
"""

from __future__ import annotations

from typing import Annotated, Any

from agent_framework import tool
from pydantic import Field

from lab_backend import backend


@tool(approval_mode="never_require")
def identify_customer(
    customer_id: Annotated[str | None, Field(description="Customer ID, for example CUST-1001.")] = None,
    email: Annotated[str | None, Field(description="Customer e-mail address.")] = None,
    phone: Annotated[str | None, Field(description="Customer phone number, any format.")] = None,
) -> dict[str, Any]:
    """Identify the customer in the synthetic customer system (lab capability).

    Provide at least one identifier given by the customer. Returns the customer's
    profile, including the IDs of their internet lines (services).
    """
    return backend.identify_customer(customer_id=customer_id, email=email, phone=phone)


@tool(approval_mode="never_require")
def get_line_status(
    line_id: Annotated[str, Field(description="Line (service) ID, for example LINE-2001.")],
) -> dict[str, Any]:
    """Get the status of a customer's internet line from the synthetic service inventory (lab capability)."""
    return backend.get_line_status(line_id)


@tool(approval_mode="never_require")
def check_equipment(
    customer_id: Annotated[str, Field(description="Customer ID returned by identify_customer.")],
) -> dict[str, Any]:
    """Ping the customer's equipment (simulated) and return reachability and Wi-Fi diagnostics (lab capability)."""
    return backend.check_equipment(customer_id)


@tool(approval_mode="never_require")
def get_known_incidents(
    postcode: Annotated[str | None, Field(description="Customer postcode, for example MV-0001.")] = None,
    area: Annotated[str | None, Field(description="Customer area name.")] = None,
) -> dict[str, Any]:
    """List known network incidents for a postcode or area from the synthetic operations system (lab capability)."""
    return backend.get_known_incidents(postcode=postcode, area=area)


@tool(approval_mode="never_require")
def escalate_to_human(
    reported_problem: Annotated[str, Field(description="The problem as reported by the customer.")],
    capabilities_checked: Annotated[
        list[str], Field(description="Names of the tools/capabilities you called.")
    ],
    evidence: Annotated[
        list[str], Field(description="Facts returned by the tools, one per item, with their values.")
    ],
    unresolved_issue: Annotated[str, Field(description="What you could not determine, and why.")],
    recommended_next_investigation: Annotated[
        str, Field(description="What a human support engineer should check next.")
    ],
    customer_id: Annotated[str | None, Field(description="Customer ID, if identified.")] = None,
    customer_language: Annotated[str | None, Field(description="Customer language: fr or en.")] = None,
    preferred_contact_channel: Annotated[
        str | None, Field(description="How the customer prefers to be contacted (sms, email, phone), if known.")
    ] = None,
) -> dict[str, Any]:
    """Hand the case over to a human support engineer (lab capability).

    Records a structured handover in the synthetic handover queue and returns its
    handover_id. It does not open a real ticket.
    """
    return backend.escalate_to_human(
        reported_problem=reported_problem,
        capabilities_checked=capabilities_checked,
        evidence=evidence,
        unresolved_issue=unresolved_issue,
        recommended_next_investigation=recommended_next_investigation,
        customer_id=customer_id,
        customer_language=customer_language,
        preferred_contact_channel=preferred_contact_channel,
    )


@tool(approval_mode="never_require")
def get_handover_status(
    handover_id: Annotated[str, Field(description="Handover reference, for example HO-9001.")],
    customer_id: Annotated[str, Field(description="Customer ID the handover belongs to.")],
) -> dict[str, Any]:
    """Get the status of a handover and the human advisor's message for the customer (lab capability)."""
    return backend.get_handover_status(handover_id, customer_id)


# A state-changing action: Agent Framework pauses before running it until a person approves the call.
@tool(approval_mode="always_require")
def restart_modem(
    customer_id: Annotated[str, Field(description="Customer ID whose modem should be restarted remotely.")],
) -> dict[str, Any]:
    """Restart the customer's modem remotely (simulated, lab capability).

    The customer's connection drops for about 3 minutes while the modem restarts.
    Requires approval before it runs.
    """
    return backend.restart_modem(customer_id)
