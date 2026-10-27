"""Synthetic back-office systems for the POST connectivity-support lab.

LAB CONSTRUCT: these functions simulate the kind of enterprise capabilities a
support agent needs (customer lookup, service/line status, an equipment ping,
known incidents and a human handover queue). They are NOT POST systems or
APIs; POST has not supplied its production interfaces. All data comes from
``data/scenarios.json`` and is fictional.

The functions are deterministic so every participant (and every evaluation
run) sees the same evidence for the same request. Every response carries
``"source": "mock"`` so nothing downstream can mistake it for real data.
"""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import threading
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

SOURCE = "mock"
DATA_FILE = Path(__file__).parent / "data" / "scenarios.json"
FIRST_HANDOVER_NUMBER = 9001

# The handover queue and the action log are shared by every process that runs the agent's tools
# (contact centre, DevUI, console), so they live in a small JSON file instead of in memory.
_state_lock = threading.Lock()


def state_file() -> Path:
    if os.getenv("LAB_STATE_DIR"):
        return Path(os.environ["LAB_STATE_DIR"]) / "handovers.json"
    directory = Path(__file__).resolve().parents[1] / ".lab_state"
    try:
        directory.mkdir(exist_ok=True)
    except OSError:
        pass
    if not os.access(directory, os.W_OK):
        # A hosted agent's code folder can be read-only; the container's temp folder is not.
        directory = Path(tempfile.gettempdir()) / "supportbuddy_lab_state"
    return directory / "handovers.json"


def _load_state() -> dict[str, Any]:
    path = state_file()
    if not path.is_file():
        return {"handovers": [], "actions": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_state(state: dict[str, Any]) -> None:
    path = state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@cache
def _data() -> dict[str, Any]:
    return json.loads(DATA_FILE.read_text(encoding="utf-8"))


def _normalise_phone(phone: str) -> str:
    digits = re.sub(r"[^\d+]", "", phone)
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if not digits.startswith("+") and digits.startswith("352"):
        digits = "+" + digits
    return digits


def _public_customer(record: dict[str, Any]) -> dict[str, Any]:
    # "scenario" is instructor metadata; "language" is withheld so replies follow the conversation's language.
    return {k: v for k, v in record.items() if k not in ("scenario", "email", "phone", "language")}


def identify_customer(
    customer_id: str | None = None,
    email: str | None = None,
    phone: str | None = None,
) -> dict[str, Any]:
    """Find a customer by customer ID, e-mail address or phone number."""
    if not any((customer_id, email, phone)):
        return {
            "found": False,
            "reason": "no_identifier",
            "message": "Provide a customer ID, e-mail address or phone number.",
            "source": SOURCE,
        }

    customers = _data()["customers"]
    matches: dict[str, dict[str, Any]] = {}
    if customer_id:
        wanted = customer_id.strip().upper()
        matches.update({c["customer_id"]: c for c in customers if c["customer_id"] == wanted})
    if email:
        wanted = email.strip().lower()
        matches.update({c["customer_id"]: c for c in customers if c["email"] == wanted})
    if phone:
        wanted = _normalise_phone(phone)
        matches.update({c["customer_id"]: c for c in customers if c["phone"] == wanted})

    if not matches:
        return {
            "found": False,
            "reason": "not_found",
            "message": "No customer matches the identifier(s) provided.",
            "source": SOURCE,
        }
    if len(matches) > 1:
        return {
            "found": False,
            "reason": "identifiers_conflict",
            "message": "The identifiers provided belong to different customers.",
            "source": SOURCE,
        }

    (record,) = matches.values()
    return {"found": True, "customer": _public_customer(copy.deepcopy(record)), "source": SOURCE}


def get_line_status(line_id: str) -> dict[str, Any]:
    """Return the current status of an internet line (service) from the synthetic service inventory."""
    wanted = line_id.strip().upper()
    for line in _data()["lines"]:
        if line["line_id"] == wanted:
            return {"found": True, "line": copy.deepcopy(line), "source": SOURCE}
    return {"found": False, "reason": "not_found", "line_id": wanted, "source": SOURCE}


def check_equipment(customer_id: str) -> dict[str, Any]:
    """Run a simulated ping against the customer's equipment and return its diagnostic state."""
    wanted = customer_id.strip().upper()
    equipment = [copy.deepcopy(e) for e in _data()["equipment"] if e["customer_id"] == wanted]
    if not equipment:
        return {"found": False, "reason": "not_found", "customer_id": wanted, "source": SOURCE}

    for item in equipment:
        ping = item["ping"]
        item["reachable"] = ping["received"] > 0
        item["packet_loss_pct"] = round(100 * (ping["sent"] - ping["received"]) / ping["sent"])
    return {
        "found": True,
        "customer_id": wanted,
        "check": "simulated_ping",
        "equipment": equipment,
        "source": SOURCE,
    }


def get_known_incidents(postcode: str | None = None, area: str | None = None) -> dict[str, Any]:
    """List known network incidents (open or recently resolved) for a postcode or area."""
    if not postcode and not area:
        return {
            "found": False,
            "reason": "no_location",
            "message": "Provide a postcode or an area.",
            "source": SOURCE,
        }

    wanted_postcode = postcode.strip().upper() if postcode else None
    wanted_area = area.strip().lower() if area else None
    incidents = [
        copy.deepcopy(i)
        for i in _data()["incidents"]
        if (wanted_postcode and wanted_postcode in i["postcodes"])
        or (wanted_area and wanted_area == i["area"].lower())
    ]
    return {"found": bool(incidents), "incidents": incidents, "source": SOURCE}


def escalate_to_human(
    reported_problem: str,
    capabilities_checked: list[str],
    evidence: list[str],
    unresolved_issue: str,
    recommended_next_investigation: str,
    customer_id: str | None = None,
    customer_language: str | None = None,
    preferred_contact_channel: str | None = None,
) -> dict[str, Any]:
    """Record a structured handover for a human support engineer in the synthetic handover queue."""
    with _state_lock:
        state = _load_state()
        number = FIRST_HANDOVER_NUMBER + len(state["handovers"])
        handover = {
            "handover_id": f"HO-{number}",
            "status": "waiting_for_advisor",
            "created_at": _now(),
            "customer_id": customer_id.strip().upper() if customer_id else None,
            "customer_language": customer_language,
            "preferred_contact_channel": preferred_contact_channel,
            "reported_problem": reported_problem,
            "capabilities_checked": list(capabilities_checked),
            "evidence": list(evidence),
            "unresolved_issue": unresolved_issue,
            "recommended_next_investigation": recommended_next_investigation,
            "advisor": None,
            "advisor_message": None,
            "updated_at": None,
        }
        state["handovers"].append(handover)
        _save_state(state)
    return {
        **copy.deepcopy(handover),
        "note": (
            "Synthetic lab handover record only. A human advisor picks it up in the lab contact centre; "
            "no real POST ticket, queue, channel or person was contacted."
        ),
        "source": SOURCE,
    }


def get_handover_status(handover_id: str, customer_id: str) -> dict[str, Any]:
    """Return the status of a handover and the advisor's message, for the customer it belongs to."""
    wanted = handover_id.strip().upper()
    owner = customer_id.strip().upper()
    for handover in _load_state()["handovers"]:
        if handover["handover_id"] == wanted and handover["customer_id"] == owner:
            return {
                "found": True,
                "handover_id": wanted,
                "status": handover["status"],
                "advisor": handover["advisor"],
                "advisor_message": handover["advisor_message"],
                "updated_at": handover["updated_at"],
                "source": SOURCE,
            }
    # Same answer for "does not exist" and "belongs to someone else": do not reveal other customers' cases.
    return {"found": False, "handover_id": wanted, "source": SOURCE}


def restart_modem(customer_id: str) -> dict[str, Any]:
    """Send a (simulated) remote restart command to the customer's modem and log the action."""
    wanted = customer_id.strip().upper()
    modems = [e for e in _data()["equipment"] if e["customer_id"] == wanted and e["type"] == "modem"]
    if not modems:
        return {"restart_sent": False, "reason": "no_modem_found", "customer_id": wanted, "source": SOURCE}
    action = {
        "action": "remote_modem_restart",
        "customer_id": wanted,
        "equipment_id": modems[0]["equipment_id"],
        "requested_at": _now(),
    }
    with _state_lock:
        state = _load_state()
        state["actions"].append(action)
        _save_state(state)
    # Not named "accepted": AG-UI reads a tool result with that key as an approval decision.
    return {
        "restart_sent": True,
        **action,
        "expected_back_in_minutes": 3,
        "note": "Simulated command. No real equipment was contacted. The customer's connection drops while it restarts.",
        "source": SOURCE,
    }


def list_handovers() -> list[dict[str, Any]]:
    """All handovers, newest first."""
    return list(reversed(_load_state()["handovers"]))


def list_actions() -> list[dict[str, Any]]:
    """All actions agents executed after approval, newest first (contact centre desk)."""
    return list(reversed(_load_state()["actions"]))


def list_customers() -> list[dict[str, Any]]:
    """The fictional lab customers (contact centre sign-in picker)."""
    return [_public_customer(c) for c in _data()["customers"]]


def update_handover(
    handover_id: str, *, status: str, advisor: str, advisor_message: str | None = None
) -> dict[str, Any] | None:
    """An advisor takes or resolves a handover (contact centre desk)."""
    if status not in ("in_progress", "resolved"):
        raise ValueError(f"Unknown status {status!r}")
    with _state_lock:
        state = _load_state()
        for handover in state["handovers"]:
            if handover["handover_id"] == handover_id:
                handover.update(status=status, advisor=advisor, updated_at=_now())
                if advisor_message is not None:
                    handover["advisor_message"] = advisor_message
                _save_state(state)
                return copy.deepcopy(handover)
    return None


def recorded_handovers() -> list[dict[str, Any]]:
    """Return the recorded handovers, oldest first (for instructors and tests)."""
    return _load_state()["handovers"]


def reset() -> None:
    """Clear recorded handovers and actions, and restart handover numbering."""
    with _state_lock:
        _save_state({"handovers": [], "actions": []})
