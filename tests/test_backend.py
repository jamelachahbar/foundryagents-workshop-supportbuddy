"""The synthetic back-office must be deterministic and must give each scenario its intended evidence."""

import pytest

from lab_backend import backend


@pytest.fixture(autouse=True)
def _reset_backend():
    backend.reset()
    yield
    backend.reset()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"customer_id": "cust-1003"},
        {"email": " Sofia.Costa@example.com "},
        {"phone": "+1 202 555 0103"},
        {"phone": "001-202-555-0103"},
        {"phone": "(202) 555-0103"},
        {"customer_id": "CUST-1003", "phone": "+12025550103"},
    ],
)
def test_identify_customer_by_any_identifier(kwargs):
    result = backend.identify_customer(**kwargs)
    assert result["found"] is True
    assert result["customer"]["customer_id"] == "CUST-1003"
    assert result["customer"]["line_ids"] == ["LINE-2003"]


def test_identify_customer_hides_instructor_metadata_contact_details_and_profile_language():
    customer = backend.identify_customer(customer_id="CUST-1001")["customer"]
    assert {"scenario", "email", "phone", "language"}.isdisjoint(customer)


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({}, "no_identifier"),
        ({"email": "nobody@example.com"}, "not_found"),
        ({"customer_id": "CUST-1001", "email": "luc.martin@example.com"}, "identifiers_conflict"),
    ],
)
def test_identify_customer_refuses_to_guess(kwargs, reason):
    result = backend.identify_customer(**kwargs)
    assert result["found"] is False
    assert result["reason"] == reason


def test_every_response_is_marked_as_mock():
    results = [
        backend.identify_customer(customer_id="CUST-1001"),
        backend.identify_customer(customer_id="CUST-9999"),
        backend.get_line_status("LINE-2001"),
        backend.get_line_status("LINE-0000"),
        backend.check_equipment("CUST-1001"),
        backend.check_equipment("CUST-9999"),
        backend.get_known_incidents(postcode="MV-0001"),
        backend.get_known_incidents(),
        backend.escalate_to_human("x", [], [], "y", "z"),
    ]
    assert all(r["source"] == "mock" for r in results)


def test_ping_result_drives_reachability():
    unreachable = backend.check_equipment("CUST-1002")["equipment"][0]
    assert unreachable["reachable"] is False
    assert unreachable["packet_loss_pct"] == 100

    flaky = backend.check_equipment("CUST-1004")["equipment"][0]
    assert flaky["reachable"] is True
    assert flaky["packet_loss_pct"] == 50


def test_incident_lookup_by_postcode_or_area():
    by_postcode = backend.get_known_incidents(postcode="mv-0001")
    by_area = backend.get_known_incidents(area="mock-ville north")
    assert [i["incident_id"] for i in by_postcode["incidents"]] == ["INC-5001"]
    assert by_postcode["incidents"] == by_area["incidents"]
    assert backend.get_known_incidents(postcode="MV-0003") == {"found": False, "incidents": [], "source": "mock"}


def _evidence(customer_id):
    customer = backend.identify_customer(customer_id=customer_id)["customer"]
    line = backend.get_line_status(customer["line_ids"][0])["line"]
    equipment = backend.check_equipment(customer_id)["equipment"][0]
    incidents = backend.get_known_incidents(postcode=customer["postcode"])["incidents"]
    return line, equipment, incidents


def test_scenario_a_area_outage_is_explained_by_an_open_incident():
    line, equipment, incidents = _evidence("CUST-1001")
    assert line["sync"] == "down"
    assert equipment["reachable"] is False
    assert [(i["incident_id"], i["status"]) for i in incidents] == [("INC-5001", "open")]


def test_scenario_b_modem_unreachable_on_a_healthy_line():
    line, equipment, incidents = _evidence("CUST-1002")
    assert line["sync"] == "up"
    assert equipment["reachable"] is False
    assert incidents == []


def test_scenario_c_local_wifi_problem():
    line, equipment, incidents = _evidence("CUST-1003")
    assert line["sync"] == "up"
    assert equipment["reachable"] is True
    assert equipment["wifi"]["band_2g"]["status"] == "congested"
    assert incidents == []


def test_scenario_d_line_fault_without_incident():
    line, _, incidents = _evidence("CUST-1004")
    assert line["sync"] == "unstable"
    assert line["resyncs_24h"] > 10
    assert incidents == []


def test_scenario_g_evidence_is_healthy_so_it_cannot_explain_an_outage():
    line, equipment, incidents = _evidence("CUST-1005")
    assert line["sync"] == "up"
    assert line["errors_24h"] == 0
    assert equipment["reachable"] is True
    assert [i["status"] for i in incidents] == ["resolved"]


def test_handover_ids_are_deterministic_and_never_claim_a_real_ticket():
    first = backend.escalate_to_human("No internet", ["identify_customer"], ["a"], "b", "c", customer_id="cust-1005")
    second = backend.escalate_to_human("No internet", [], [], "b", "c")
    assert (first["handover_id"], second["handover_id"]) == ("HO-9001", "HO-9002")
    assert first["customer_id"] == "CUST-1005"
    assert first["status"] == "waiting_for_advisor"
    assert "no real ticket" in first["note"]
    assert [h["handover_id"] for h in backend.recorded_handovers()] == ["HO-9001", "HO-9002"]


def test_advisor_reply_reaches_the_customer_through_the_handover_status():
    backend.escalate_to_human("No internet", [], [], "b", "c", customer_id="CUST-1005")
    assert backend.get_handover_status("ho-9001", "CUST-1005")["status"] == "waiting_for_advisor"

    backend.update_handover("HO-9001", status="in_progress", advisor="Advisor Lea")
    backend.update_handover("HO-9001", status="resolved", advisor="Advisor Lea", advisor_message="Router replaced.")

    status = backend.get_handover_status("HO-9001", "cust-1005")
    assert (status["status"], status["advisor"], status["advisor_message"]) == (
        "resolved",
        "Advisor Lea",
        "Router replaced.",
    )
    assert backend.list_handovers()[0]["status"] == "resolved"


def test_handover_status_is_only_visible_to_its_own_customer():
    backend.escalate_to_human("No internet", [], [], "b", "c", customer_id="CUST-1005")
    assert backend.get_handover_status("HO-9001", "CUST-1001") == {
        "found": False,
        "handover_id": "HO-9001",
        "source": "mock",
    }


def test_unknown_handover_status_is_rejected():
    backend.escalate_to_human("No internet", [], [], "b", "c")
    with pytest.raises(ValueError):
        backend.update_handover("HO-9001", status="deleted", advisor="x")
    assert backend.update_handover("HO-0000", status="resolved", advisor="x") is None


def test_remote_modem_restart_is_simulated_and_logged():
    result = backend.restart_modem("cust-1002")
    assert result["restart_sent"] is True
    assert result["equipment_id"] == "EQ-3002"
    assert result["source"] == "mock"
    assert [a["customer_id"] for a in backend.list_actions()] == ["CUST-1002"]
    assert backend.restart_modem("CUST-9999")["restart_sent"] is False


def test_backend_returns_copies_not_its_own_data():
    backend.get_line_status("LINE-2001")["line"]["sync"] = "tampered"
    assert backend.get_line_status("LINE-2001")["line"]["sync"] == "down"
