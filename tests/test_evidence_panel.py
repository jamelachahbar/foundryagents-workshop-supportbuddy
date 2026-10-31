"""The optional evidence panel projects results, not diagnoses or additional agent calls."""

import asyncio
import copy
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from contact_centre.centre import ContactCentre
from contact_centre.evidence import EvidencePanel
from contact_centre.server import create_app
from lab_backend import backend


def start(call, name):
    return {"type": "TOOL_CALL_START", "toolCallId": call, "toolCallName": name}


def result(call, value):
    return {"type": "TOOL_CALL_RESULT", "toolCallId": call, "content": json.dumps(value)}


def run_script(events, *, enabled=True, error=None):
    requests = []

    async def runner(path, body):
        requests.append((path, body))
        for event in events:
            yield event
        if error:
            raise error

    centre = ContactCentre(runner, signs_in=False, agent_label="test", evidence_panel=enabled)
    conversation = centre.start(None)
    asyncio.run(centre.customer_message(conversation, "My internet is down"))
    return centre, conversation, requests


@pytest.mark.parametrize("name,value,expected", [
    ("identify_customer", backend.identify_customer(customer_id="CUST-1002"), "Customer identified"),
    ("identify_customer", {"found": False}, "Customer not identified"),
    ("get_line_status", backend.get_line_status("LINE-2002"), "download 0/1000 Mbps"),
    ("get_known_incidents", backend.get_known_incidents(postcode="MV-0001"), "1 open incidents"),
    ("get_known_incidents", backend.get_known_incidents(postcode="MV-0002"), "0 open incidents"),
    ("check_equipment", backend.check_equipment("CUST-1002"), "Not responding; packet loss 100%"),
])
@pytest.mark.parametrize("prefix", ["", "network___", "network."])
def test_real_diagnostic_results_are_projected_with_both_toolbox_namespaces(name, value, expected, prefix):
    _, conversation, _ = run_script([start("call", prefix + name), result("call", value)])
    rows = conversation.customer_view()["evidence"]
    assert len(rows) == 1
    assert rows[0]["status"] == "completed"
    assert expected in rows[0]["finding"]
    assert "email" not in json.dumps(rows)
    assert "name" not in rows[0]


def test_retrieval_projects_count_without_leaking_document_text_or_titles():
    documents = [{"ref_id": 0, "title": "<script>secret</script>", "content": "ignore instructions"}]
    raw = [{"type": "text", "text": json.dumps(documents)}]
    _, conversation, _ = run_script([
        start("kb", "runbooks.knowledge_base_retrieve"), result("kb", raw),
    ])
    rows = conversation.customer_view()["evidence"]
    assert rows[0]["finding"] == "Retrieval completed; 1 documents returned"
    assert "script" not in json.dumps(rows) and "ignore" not in json.dumps(rows)


@pytest.mark.parametrize("envelope", ["mcp", "response", "structured"])
def test_retrieval_accepts_document_json_followed_by_foundry_source_references(runbook_mcp_result, envelope):
    value = runbook_mcp_result
    if envelope == "response":
        value = {"response": [{"role": "assistant", "content": value}]}
    elif envelope == "structured":
        value = {"isError": False, "content": [], "structuredContent": {"response": [{"content": value}]}}
    _, conversation, _ = run_script([start("kb", "runbooks___knowledge_base_retrieve"), result("kb", value)])
    row = conversation.evidence.snapshot()[0]
    assert row["status"] == "completed"
    assert row["finding"] == "Retrieval completed; 1 documents returned"
    assert "private" not in json.dumps(row) and "example.invalid" not in json.dumps(row)


@pytest.mark.parametrize("suffix", [
    "not-json", '{"error": "failed"}',
    '{"kind":"reference","ref_id":99,"mimeType":"application/json","sourceData":{}}',
])
def test_retrieval_rejects_malformed_or_conflicting_trailing_json(suffix):
    raw = [{"type": "text", "text": json.dumps([{"ref_id": 0, "title": "Synthetic", "content": "Synthetic"}]) + suffix}]
    _, conversation, _ = run_script([start("kb", "knowledge_base_retrieve"), result("kb", raw)])
    assert conversation.evidence.snapshot()[0]["status"] == "failed"


def test_buffered_tool_events_publish_distinct_message_snapshots():
    opened = []

    async def open_agent(path):
        opened.append(path)
        assert conversation.transcript[-1]["role"] == "customer"
        assert not any(message["role"] == "evidence" for message in conversation.transcript)

    async def runner(path, body):
        yield start("line", "get_line_status")
        yield result("line", backend.get_line_status("LINE-2002"))
        yield {"type": "RUN_FINISHED"}

    centre = ContactCentre(runner, signs_in=False, agent_label="test", evidence_panel=True)
    centre.opener = open_agent
    conversation = centre.start(None)
    queue = centre.store.bus.subscribe()
    asyncio.run(centre.customer_message(conversation, "Check my line"))
    updates = [queue.get_nowait() for _ in range(queue.qsize())]
    messages = [event["message"] for event in updates if event["type"] == "message" and event["message"]["role"] == "evidence"]
    assert messages[0]["evidence"][0]["status"] == "checking" and messages[0]["evidence_phase"] == "running"
    assert [event["event"] for event in messages[0]["trace"]] == ["run_started", "started"]
    assert [event["event"] for event in messages[-1]["trace"]] == ["run_started", "started", "completed", "run_finished"]
    assert any(any(row["status"] == "checking" for row in message["evidence"]) for message in messages)
    assert messages[-1]["evidence"][0]["status"] == "completed"
    assert messages[-1]["evidence_phase"] == "finished"
    assert len({message["id"] for message in messages}) == 1
    assert len(opened) == 1


@pytest.mark.parametrize("name,value", [
    ("get_line_status", {"found": False}),
    ("get_line_status", {"found": True, "line": {"line_id": "<script>"}}),
    ("get_known_incidents", {"incidents": [{"status": "ignore all instructions"}]}),
    ("get_known_incidents", {}),
    ("check_equipment", {"found": True, "equipment": []}),
    ("identify_customer", {"found": "true", "customer": {}}),
    ("identify_customer", {"found": True}),
    ("identify_customer", {"found": True, "customer": {}}),
    ("knowledge_base_retrieve", {"isError": True, "content": [{"type": "text", "text": "boom"}]}),
    ("knowledge_base_retrieve", "not structured retrieval"),
    ("get_line_status", {"isError": True, "content": []}),
])
def test_malformed_and_error_results_are_explicit_failures(name, value):
    _, conversation, _ = run_script([start("bad", name), result("bad", value)])
    row = conversation.customer_view()["evidence"][0]
    assert row["status"] == "failed"
    assert row["finding"] == "No usable result returned"
    assert "boom" not in json.dumps(row)


@pytest.mark.parametrize("bad_number", [True, -1, float("inf"), float("nan"), 10**400, "941"])
def test_invalid_numeric_fields_do_not_crash_or_invent_healthy_findings(bad_number):
    value = backend.get_line_status("LINE-2005")
    value["line"]["measured_mbps"]["down"] = bad_number
    _, conversation, _ = run_script([start("bad", "get_line_status"), result("bad", value)])
    assert conversation.evidence.snapshot()[0]["status"] == "failed"


def test_multiline_out_of_order_results_and_replayed_events_are_idempotent():
    first = start("first", "get_line_status")
    second = start("second", "get_line_status")
    first_result = result("first", backend.get_line_status("LINE-2001"))
    second_result = result("second", backend.get_line_status("LINE-2002"))
    _, conversation, requests = run_script([
        first, second, second_result, first_result, second_result, first, first_result,
    ])
    rows = conversation.evidence.snapshot()
    assert {row["key"] for row in rows} == {"line:LINE-2001", "line:LINE-2002"}
    assert all(row["status"] == "completed" for row in rows)
    assert len(requests) == 1


def test_sequential_line_checks_from_the_same_run_remain_current():
    _, conversation, _ = run_script([
        start("first", "get_line_status"), result("first", backend.get_line_status("LINE-2001")),
        start("second", "get_line_status"), result("second", backend.get_line_status("LINE-2002")),
    ])
    assert len(conversation.evidence.snapshot()) == 2
    assert all(row["status"] == "completed" for row in conversation.evidence.snapshot())


@pytest.mark.parametrize("terminal", ["RUN_FINISHED", "RUN_ERROR", None])
def test_unanswered_checks_end_incomplete(terminal):
    events = [start("line", "get_line_status")]
    if terminal:
        events.append({"type": terminal, "message": "provider details"})
    _, conversation, _ = run_script(events)
    assert conversation.evidence.snapshot()[0]["status"] == "incomplete"
    expected = {"RUN_FINISHED": "finished", "RUN_ERROR": "failed", None: "incomplete"}[terminal]
    assert conversation.customer_view()["evidence_phase"] == expected
    card = next(message for message in conversation.transcript if message["role"] == "evidence")
    trace = [event for event in card["trace"] if event["capability"] == "get_line_status"]
    assert [event["event"] for event in trace] == ["started", "incomplete"]
    assert {event["call_id"] for event in trace} == {"line"}


def test_http_failure_preserves_visible_incomplete_state():
    _, conversation, _ = run_script(
        [start("line", "get_line_status")], error=httpx.ConnectError("unavailable"),
    )
    assert conversation.evidence.snapshot()[0]["status"] == "incomplete"
    assert conversation.transcript[-1]["role"] == "system"


def test_new_check_marks_old_findings_previous_and_does_not_replace_them_on_error():
    panel = EvidencePanel()
    value = backend.get_line_status("LINE-2002")
    assert panel.start("old", "get_line_status", "run1")
    assert panel.result("old", value, value)
    assert panel.start("new", "get_line_status", "run2")
    assert panel.rows["line:LINE-2002"].status == "previous"
    assert panel.result("new", None, "malformed")
    assert {row["status"] for row in panel.snapshot()} == {"previous", "failed"}
    assert not panel.start("old", "get_line_status", "run3")
    assert [event["event"] for event in panel.trace_snapshot("run1")] == ["started", "completed"]
    assert [event["event"] for event in panel.trace_snapshot("run2")] == ["started", "failed"]


def test_feature_off_preserves_existing_views_and_agent_request():
    events = [start("line", "get_line_status"), result("line", backend.get_line_status("LINE-2002"))]
    _, off, off_requests = run_script(copy.deepcopy(events), enabled=False)
    _, on, on_requests = run_script(copy.deepcopy(events))
    assert "evidence" not in off.customer_view()
    assert "trace" not in off.customer_view()
    assert "evidence" not in off.desk_view()
    assert not any(message["role"] == "evidence" for message in off.transcript)
    assert any(message["role"] == "evidence" for message in on.transcript)
    assert off_requests[0][0] == on_requests[0][0]
    assert off_requests[0][1]["messages"][0]["content"] == on_requests[0][1]["messages"][0]["content"]
    assert len(off_requests) == len(on_requests) == 1
    assert "evidence" not in json.dumps(on.agent_history)


def test_unprojected_tools_create_a_trace_backed_topology_without_fabricated_evidence():
    _, conversation, requests = run_script([
        start("action", "restart_modem"), result("action", {"private": "Do not expose"}),
        start("action", "restart_modem"), result("action", {"private": "Do not expose"}),
        {"type": "RUN_FINISHED"},
    ])
    card = next(message for message in conversation.transcript if message["role"] == "evidence")
    assert card["evidence"] == []
    trace = conversation.customer_view()["trace"]
    assert card["trace"] == trace
    assert [event["event"] for event in trace] == ["run_started", "started", "result", "run_finished"]
    assert trace[1]["capability"] == "restart_modem"
    assert "Do not expose" not in json.dumps(trace)
    assert len(requests) == 1


def test_new_tools_are_traced_without_leaking_tool_names_arguments_or_results():
    _, conversation, requests = run_script([
        start("one", 'new_tool_<script>alert("private")</script>'),
        {"type": "TOOL_CALL_ARGS", "toolCallId": "one", "delta": '{"secret":"private arguments"}'},
        result("one", {"secret": "private result"}),
        start("two", "future_tool"), result("two", "non-JSON result"),
        {"type": "RUN_FINISHED"},
    ])
    card = next(message for message in conversation.transcript if message["role"] == "evidence")
    events = [event for event in card["trace"] if event["capability"] == "other_tool"]
    assert [(event["call_id"], event["event"]) for event in events] == [
        ("one", "started"), ("one", "result"), ("two", "started"), ("two", "result"),
    ]
    assert "private" not in json.dumps(conversation.customer_view())
    assert "future_tool" not in json.dumps(conversation.customer_view())
    assert card["evidence"] == []
    assert len(requests) == 1


def test_optional_processing_steps_are_observed_without_exposing_internal_step_names():
    _, conversation, _ = run_script([
        {"type": "STEP_STARTED", "stepName": "private internal planning"},
        start("line", "get_line_status"), result("line", backend.get_line_status("LINE-2002")),
        {"type": "STEP_FINISHED", "stepName": "private internal planning"},
        {"type": "RUN_FINISHED"},
    ])
    trace = conversation.customer_view()["trace"]
    steps = [event for event in trace if event["event"].startswith("step_")]
    assert [event["event"] for event in steps] == ["step_started", "step_finished"]
    assert steps[0]["call_id"] == steps[1]["call_id"]
    assert "private internal planning" not in json.dumps(conversation.customer_view())
    card = next(message for message in conversation.transcript if message["role"] == "evidence")
    assert card["trace"] == trace


def test_trace_history_spans_turns_and_excludes_other_conversations_and_private_assist():
    async def runner(path, body):
        call = body["runId"] + "_line"
        yield start(call, "get_line_status")
        yield result(call, backend.get_line_status("LINE-2002"))
        yield {"type": "RUN_FINISHED"}

    centre = ContactCentre(runner, signs_in=False, agent_label="test", evidence_panel=True)
    conversation, other = centre.start(None), centre.start(None)
    asyncio.run(centre.customer_message(conversation, "Check my line"))
    asyncio.run(centre.customer_message(conversation, "Check again"))
    trace = conversation.customer_view()["trace"]
    assert sum(event["event"] == "run_started" for event in trace) == 2
    assert sum(event["event"] == "run_finished" for event in trace) == 2
    assert len(trace) == 8
    asyncio.run(centre.assist(conversation, "Lea", "Privately check again"))
    assert conversation.customer_view()["trace"] == trace
    assert other.customer_view()["trace"] == []
    cards = [message for message in conversation.transcript if message["role"] == "evidence"]
    assert len(cards) == 2
    assert len(cards[0]["trace"]) == 4
    assert cards[0]["trace"] != cards[1]["trace"]


def test_diagnostic_cards_begin_with_the_first_tool_and_preserve_earlier_turns():
    scripts = [
        [
            {"type": "TEXT_MESSAGE_CONTENT", "messageId": "before", "delta": "I will check."},
            start("first", "get_line_status"), result("first", backend.get_line_status("LINE-2002")),
            {"type": "TEXT_MESSAGE_CONTENT", "messageId": "after", "delta": "The line is up."},
            {"type": "RUN_FINISHED"},
        ],
        [{"type": "TEXT_MESSAGE_CONTENT", "messageId": "thanks", "delta": "You are welcome."}, {"type": "RUN_FINISHED"}],
        [start("second", "get_line_status"), result("second", backend.get_line_status("LINE-2001")), {"type": "RUN_FINISHED"}],
    ]

    async def runner(path, body):
        for event in scripts.pop(0):
            yield event

    centre = ContactCentre(runner, signs_in=False, agent_label="test", evidence_panel=True)
    conversation = centre.start(None)
    assert not any(message["role"] == "evidence" for message in conversation.transcript)
    asyncio.run(centre.customer_message(conversation, "Check"))
    roles = [message["role"] for message in conversation.transcript]
    assert roles[-4:] == ["customer", "agent", "evidence", "agent"]
    first = copy.deepcopy(next(message for message in conversation.transcript if message["role"] == "evidence"))
    asyncio.run(centre.customer_message(conversation, "Thanks"))
    assert sum(message["role"] == "evidence" for message in conversation.transcript) == 1
    asyncio.run(centre.customer_message(conversation, "Check another line"))
    cards = [message for message in conversation.transcript if message["role"] == "evidence"]
    assert len(cards) == 2 and cards[0] == first
    assert cards[1]["evidence"][0]["key"] == "line:LINE-2001"
    assert all(message["role"] != "evidence" for message in conversation.agent_history)


def test_live_state_snapshots_are_safe_isolated_and_survive_handover():
    async def runner(path, body):
        yield start("identify", "identify_customer")
        yield result("identify", backend.identify_customer(customer_id="CUST-1002"))
        yield start("equipment", "check_equipment")
        yield result("equipment", backend.check_equipment("CUST-1002"))
        yield start("handover", "escalate_to_human")
        yield result("handover", {"handover_id": "HO-9001"})

    centre = ContactCentre(runner, signs_in=False, agent_label="test", evidence_panel=True)
    first, other = centre.start(None), centre.start(None)
    queue = centre.store.bus.subscribe()
    asyncio.run(centre.customer_message(first, "Help"))
    assert first.mode == "waiting"
    before = first.evidence.snapshot()
    centre.take(first, "Lea")
    assert first.evidence.snapshot() == before
    assert first.customer_view()["trace"][-1]["event"] == "advisor_joined"
    centre.hand_back(first, "Lea")
    assert first.customer_view()["trace"][-1]["event"] == "handed_back"
    centre.customer_leave(first)
    assert first.customer_view()["trace"][-1]["event"] == "customer_left"
    assert other.customer_view()["evidence"] == []
    assert first.customer_view()["evidence"] == first.desk_view()["evidence"]
    assert "Luc Martin" not in json.dumps(before)
    updates = []
    while not queue.empty():
        updates.append(queue.get_nowait())
    snapshots = [event["summary"]["evidence"] for event in updates if event["type"] == "conversation"]
    assert any(any(row["status"] == "checking" for row in snapshot) for snapshot in snapshots)
    assert snapshots[-1] == before
    assert all(event["conversation"] == first.id for event in updates)


def test_private_assist_checks_cannot_modify_customer_evidence():
    async def runner(path, body):
        yield start("private", "get_line_status")
        yield result("private", backend.get_line_status("LINE-2002"))

    centre = ContactCentre(runner, signs_in=False, agent_label="test", evidence_panel=True)
    conversation = centre.start(None)
    asyncio.run(centre.assist(conversation, "Lea", "Check privately"))
    assert conversation.evidence.snapshot() == []
    assert conversation.assist_transcript


def test_optional_config_and_fresh_conversation_snapshot_over_http():
    async def runner(path, body):
        if False:
            yield {}

    centre = ContactCentre(runner, signs_in=False, agent_label="test", evidence_panel=True)
    with TestClient(create_app(centre, {})) as client:
        assert client.get("/api/config").json()["evidence_panel"] is True
        first = client.post("/api/conversations", json={}).json()
        assert first["evidence"] == []
        assert centre.store.get(first["id"]).desk_view()["evidence"] == []


@pytest.mark.parametrize("approved", [True, False])
def test_evidence_does_not_change_approval_or_execute_extra_actions(approved):
    requests = []

    async def runner(path, body):
        requests.append(body)
        if not body.get("resume"):
            yield start("line", "get_line_status")
            yield result("line", backend.get_line_status("LINE-2002"))
            yield start("restart", "restart_modem")
            yield {"type": "RUN_FINISHED", "outcome": {"interrupts": [{
                "id": "approval", "metadata": {"agent_framework": {"function_call": {
                    "name": "restart_modem", "arguments": {"customer_id": "CUST-1002"},
                }}},
            }]}}
        else:
            if body["resume"][0]["payload"]["approved"]:
                yield result("restart", backend.restart_modem("CUST-1002"))
            yield {"type": "TEXT_MESSAGE_CONTENT", "messageId": "answer", "delta": "Decision processed."}
            yield {"type": "RUN_FINISHED"}

    centre = ContactCentre(runner, signs_in=False, agent_label="test", evidence_panel=True)
    conversation = centre.start(None)
    asyncio.run(centre.customer_message(conversation, "Please restart my modem."))
    assert backend.list_actions() == []
    assert conversation.evidence.phase == "approval"
    cards = [message for message in conversation.transcript if message["role"] == "evidence"]
    assert len(cards) == 1 and cards[0]["evidence_phase"] == "approval"
    assert any(event["event"] == "approval_requested" for event in cards[0]["trace"])
    before = conversation.evidence.snapshot()
    approval = next(message for message in conversation.transcript if message["role"] == "approval")
    assert approval["action_details"]["impact"] == "The connection drops for about 3 minutes."
    assert approval["action_details"]["evidence"] == before
    asyncio.run(centre.customer_approval(conversation, "approval", approved))
    assert len(backend.list_actions()) == int(approved)
    assert conversation.evidence.phase == "finished"
    assert len([message for message in conversation.transcript if message["role"] == "evidence"]) == 1
    assert cards[0]["evidence_phase"] == "finished"
    assert conversation.evidence.snapshot() == before
    assert any(event["event"] == ("approval_approved" if approved else "approval_declined")
               for event in cards[0]["trace"])
    if approved:
        assert any(event["event"] == "result" and event["capability"] == "restart_modem"
                   for event in cards[0]["trace"])
    assert len(requests) == 2
    assert requests[1]["resume"] == [{
        "interruptId": "approval", "status": "resolved", "payload": {"approved": approved},
    }]
    assert conversation.transcript[-1]["text"] == "Decision processed."
    with pytest.raises(ValueError, match="already decided"):
        asyncio.run(centre.customer_approval(conversation, "approval", approved))
    assert len(backend.list_actions()) == int(approved)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("terminal", ["RUN_FINISHED", "RUN_ERROR"])
def test_optional_reply_draft_is_private_and_only_available_after_successful_requested_reply(enabled, terminal):
    async def runner(path, body):
        yield {"type": "TEXT_MESSAGE_CONTENT", "messageId": "reply", "delta": "Please check the power cable."}
        yield {"type": terminal, "message": "Synthetic error"}

    centre = ContactCentre(runner, signs_in=False, agent_label="test", evidence_panel=enabled)
    conversation = centre.start(None)
    asyncio.run(centre.assist(conversation, "Lea", "Suggest a reply to the customer."))
    message = next(message for message in conversation.assist_transcript if message["role"] == "agent")
    assert ("reply_draft" in message) == (enabled and terminal == "RUN_FINISHED")
    assert all(message["role"] != "agent" for message in conversation.transcript)
    assert "reply_draft" not in json.dumps(conversation.assist_history)
