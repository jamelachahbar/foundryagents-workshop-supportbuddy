"""The contact centre: who answers the customer, handover, live chat, approvals and assist (no Azure needed).

SupportBuddy and the assist agent are replaced by scripted AG-UI event streams.
"""

import asyncio
import json
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from contact_centre.centre import ASSIST_PATH, ContactCentre, tool_name
from contact_centre.server import create_app
from lab_backend import backend

STATIC = Path(__file__).resolve().parents[1] / "src" / "contact_centre" / "static"


class ScriptedAgents:
    """A fake AG-UI runner: returns the next scripted list of events and records every request."""

    def __init__(self, *scripts):
        self.scripts = list(scripts)
        self.calls = []

    async def __call__(self, path, body):
        self.calls.append((path, json.loads(json.dumps(body))))
        for event in self.scripts.pop(0):
            yield event


def text(message_id, *chunks):
    return [{"type": "TEXT_MESSAGE_CONTENT", "messageId": message_id, "delta": c} for c in chunks]


def tool(call_id, name, result=None):
    events = [{"type": "TOOL_CALL_START", "toolCallId": call_id, "toolCallName": name}]
    if result is not None:
        events.append({"type": "TOOL_CALL_RESULT", "toolCallId": call_id, "content": json.dumps(result)})
    return events


def snapshot(*messages):
    return [{"type": "MESSAGES_SNAPSHOT", "messages": list(messages)}]


def handover_record():
    return backend.escalate_to_human(
        reported_problem="No internet at all",
        capabilities_checked=["identify_customer", "get_line_status", "get_known_incidents", "check_equipment"],
        evidence=["LINE-2005 up, 941/1000 Mbps"],
        unresolved_issue="Everything is healthy",
        recommended_next_investigation="Check the customer's router",
        customer_id="CUST-1005",
    )


def run(coroutine):
    return asyncio.run(coroutine)


@pytest.mark.parametrize("name, expected", [
    ("network___identify_customer", "identify_customer"),
    ("network.identify_customer", "identify_customer"),
    ("runbooks.knowledge_base_retrieve", "knowledge_base_retrieve"),
    ("another_namespace___custom_tool", "custom_tool"),
    ("restart_modem", "restart_modem"),
    ("unrelated.custom_tool", "unrelated.custom_tool"),
])
def test_tool_names_preserve_local_names_and_normalize_toolbox_namespaces(name, expected):
    assert tool_name(name) == expected


def test_supportbuddy_answers_with_progress_and_streamed_text():
    agents = ScriptedAgents(
        tool("c1", "network___identify_customer", {"found": True})
        + tool("c2", "identify_customer", {"found": True})
        + text("m1", "Your modem ", "is not responding.")
        + snapshot({"id": "x", "role": "user", "content": "hi"})
        + [{"type": "RUN_FINISHED"}]
    )
    centre = ContactCentre(agents, signs_in=False, agent_label="test")
    conversation = centre.start(None)
    run(centre.customer_message(conversation, "My internet is down, I'm CUST-1002"))

    path, body = agents.calls[0]
    assert path == "/ag-ui/supportbuddy/guest"
    assert body["threadId"] == conversation.id
    assert body["messages"][-1]["content"] == "My internet is down, I'm CUST-1002"
    roles = [(m["role"], m["text"]) for m in conversation.transcript[1:]]
    assert roles == [
        ("customer", "My internet is down, I'm CUST-1002"),
        ("activity", "Looking up your account"),
        ("agent", "Your modem is not responding."),
    ]
    assert conversation.agent_history == [{"id": "x", "role": "user", "content": "hi"}]
    assert conversation.mode == "agent" and not conversation.busy
    # The desk learns who the anonymous customer is, also from MCP-wrapped toolbox results.
    assert conversation.identified is None


@pytest.mark.parametrize("name", ["network___identify_customer", "network.identify_customer"])
def test_desk_learns_the_customer_supportbuddy_identified(name):
    found = {"found": True, "customer": {"customer_id": "CUST-1003", "name": "Sofia Costa"}}
    agents = ScriptedAgents(
        [{"type": "TOOL_CALL_START", "toolCallId": "c1", "toolCallName": name},
         {"type": "TOOL_CALL_RESULT", "toolCallId": "c1", "content": json.dumps([{"type": "text", "text": json.dumps(found)}])}]
        + text("m1", "Found you.") + [{"type": "RUN_FINISHED"}]
    )
    centre = ContactCentre(agents, signs_in=False, agent_label="test")
    conversation = centre.start(None)
    run(centre.customer_message(conversation, "Phone +1 202 555 0103"))
    assert conversation.identified == {"customer_id": "CUST-1003", "name": "Sofia Costa"}
    assert conversation.summary()["identified"]["customer_id"] == "CUST-1003"


def test_tool_results_are_read_from_local_tools_and_from_the_foundry_toolbox():
    from contact_centre.centre import tool_result

    data = {"found": True, "customer": {"customer_id": "CUST-1001"}}
    toolbox = {"_meta": {"tool_configuration": {"type": "mcp"}}, "content": [{"type": "text", "text": json.dumps(data)}]}
    assert tool_result(json.dumps(data)) == data
    assert tool_result(json.dumps(toolbox)) == data
    assert tool_result("not json") is None


@pytest.mark.parametrize("with_meta", [False, True])
def test_desk_reads_nested_foundry_customer_results(with_meta):
    found = {"found": True, "customer": {"customer_id": "CUST-1003", "name": "Sofia Costa"}}
    envelope = {"content": [{"type": "text", "text": json.dumps(found)}], "isError": False}
    if with_meta:
        envelope["_meta"] = {"tool_configuration": {"type": "mcp"}}
    output = [{"type": "text", "text": json.dumps(envelope), "additional_properties": {}}]
    agents = ScriptedAgents(
        tool("c1", "network___identify_customer", output)
        + text("m1", "Your line is healthy.") + [{"type": "RUN_FINISHED"}]
    )
    centre = ContactCentre(agents, signs_in=False, agent_label="prompt agent")
    conversation = centre.start(None)
    run(centre.customer_message(conversation, "My Wi-Fi is slow"))
    assert conversation.identified == found["customer"]
    assert not conversation.busy
    assert conversation.handover is None

    centre.take(conversation, "Lea")
    centre.advisor_message(conversation, "Lea", "Hello, I can help.")
    run(centre.customer_message(conversation, "Thank you"))
    assert conversation.mode == "advisor"
    assert len(agents.calls) == 1
    assert any(m["role"] == "advisor" and m["text"] == "Hello, I can help." for m in conversation.transcript)


def test_tool_results_support_structured_mcp_output_and_ignore_errors():
    from contact_centre.centre import tool_result

    found = {"found": True, "customer": {"customer_id": "CUST-1003"}}
    assert tool_result({"content": [], "structuredContent": found}) == found
    assert tool_result({"content": [{"type": "text", "text": "not json"}]}) is None
    assert tool_result({"content": [], "structuredContent": found, "isError": True}) is None


@pytest.mark.parametrize("serialized", [False, True])
def test_desk_reads_foundry_text_with_duplicate_structured_response(serialized):
    found = {"found": True, "customer": {"customer_id": "CUST-1005", "name": "Claire Hoffmann"}}
    output = [{
        "type": "text",
        "text": json.dumps(found, indent=2) + "\n" + json.dumps({"structuredResponse": found}, indent=2) + "\n",
        "additional_properties": {},
    }]
    content = json.dumps(output) if serialized else output
    agents = ScriptedAgents([
        {"type": "TOOL_CALL_START", "toolCallId": "c1", "toolCallName": "network___identify_customer"},
        {"type": "TOOL_CALL_RESULT", "toolCallId": "c1", "content": content},
        {"type": "RUN_FINISHED"},
    ])
    centre = ContactCentre(agents, signs_in=False, agent_label="prompt agent")
    conversation = centre.start(None)
    run(centre.customer_message(conversation, "I'm CUST-1005, nothing loads."))
    assert conversation.identified == found["customer"]


@pytest.mark.parametrize("suffix", [
    "not json",
    '{"unrelated": true}',
    '{"structuredResponse": {"customer": {"customer_id": "CUST-1002"}}}',
    '{"structuredResponse": {"found": true}} trailing',
])
def test_tool_results_reject_conflicting_or_malformed_extra_json(suffix):
    from contact_centre.centre import tool_result

    output = [{"type": "text", "text": '{"found": true}\n' + suffix}]
    assert tool_result(output) is None


def test_handover_moves_the_conversation_to_a_human_advisor_and_back():
    record = handover_record()
    agents = ScriptedAgents(
        tool("c1", "escalate_to_human", record) + text("m1", "I handed this over.") + [{"type": "RUN_FINISHED"}],
        text("m2", "Welcome back.") + [{"type": "RUN_FINISHED"}],
    )
    centre = ContactCentre(agents, signs_in=False, agent_label="test")
    conversation = centre.start(None)
    run(centre.customer_message(conversation, "Nothing loads"))

    assert conversation.mode == "waiting"
    assert conversation.handover["handover_id"] == record["handover_id"]
    assert record["handover_id"] in conversation.transcript[-1]["text"]

    # While a human owns the conversation, SupportBuddy stays silent.
    run(centre.customer_message(conversation, "Hello?"))
    assert len(agents.calls) == 1
    with pytest.raises(ValueError):
        centre.advisor_message(conversation, "Lea", "Not taken yet")

    centre.take(conversation, "Lea")
    assert conversation.mode == "advisor" and conversation.advisor == "Lea"
    assert backend.recorded_handovers()[0]["status"] == "in_progress"

    centre.advisor_message(conversation, "Lea", "Hi, I'm Lea.")
    assert conversation.transcript[-1] == {**conversation.transcript[-1], "role": "advisor", "author": "Lea"}
    assert conversation.agent_history[-1]["content"] == "(Human advisor Lea) Hi, I'm Lea."

    centre.hand_back(conversation, "Lea")
    run(centre.customer_message(conversation, "Thanks"))
    assert len(agents.calls) == 2
    assert agents.calls[1][1]["messages"][-1]["content"] == "Thanks"

    centre.take(conversation, "Lea")
    centre.advisor_message(conversation, "Lea", "Your router needed a reset.")
    centre.close(conversation, "Lea")
    assert conversation.mode == "closed"
    stored = backend.recorded_handovers()[0]
    assert stored["status"] == "resolved"
    assert stored["advisor_message"] == "Your router needed a reset."
    with pytest.raises(ValueError):
        run(centre.customer_message(conversation, "Still there?"))


def test_customer_leaving_closes_the_conversation_for_the_desk():
    record = handover_record()
    agents = ScriptedAgents(tool("c1", "escalate_to_human", record) + [{"type": "RUN_FINISHED"}])
    centre = ContactCentre(agents, signs_in=False, agent_label="test")
    conversation = centre.start(None)
    events = centre.store.bus.subscribe()

    centre.customer_leave(conversation)
    assert conversation.mode == "closed"
    assert conversation.transcript[-1]["text"] == "The customer left the conversation."
    published = [events.get_nowait() for _ in range(events.qsize())]
    assert {"type": "conversation", "conversation": conversation.id} in [
        {k: e[k] for k in ("type", "conversation")} for e in published
    ]
    assert published[0]["summary"]["mode"] == "closed"

    # A SupportBuddy turn that finishes after the customer left keeps the case but does not reopen the chat.
    run(centre._run_supportbuddy(conversation, resume=None))
    assert conversation.mode == "closed"
    assert conversation.handover["handover_id"] == record["handover_id"]
    assert backend.recorded_handovers()[0]["status"] == "waiting_for_advisor"
    with pytest.raises(ValueError):
        run(centre.customer_message(conversation, "Still there?"))


def test_customer_approval_resumes_the_run():
    interrupt = {
        "id": "approval-1",
        "metadata": {"agent_framework": {"function_call": {"name": "restart_modem", "arguments": {"customer_id": "CUST-1002"}}}},
    }
    agents = ScriptedAgents(
        tool("c1", "restart_modem") + [{"type": "RUN_FINISHED", "outcome": {"type": "interrupt", "interrupts": [interrupt]}}],
        text("m1", "Restart sent.") + [{"type": "RUN_FINISHED"}],
    )
    centre = ContactCentre(agents, signs_in=False, agent_label="test")
    conversation = centre.start(None)
    run(centre.customer_message(conversation, "Please restart my modem"))

    approval = conversation.transcript[-1]
    assert approval["role"] == "approval" and approval["status"] == "pending"
    assert approval["tool"] == "restart_modem" and approval["arguments"] == {"customer_id": "CUST-1002"}

    run(centre.customer_approval(conversation, "approval-1", True))
    assert approval["status"] == "approved"
    assert agents.calls[1][1]["resume"] == [{"interruptId": "approval-1", "status": "resolved", "payload": {"approved": True}}]
    assert conversation.transcript[-1]["text"] == "Restart sent."
    with pytest.raises(ValueError):
        run(centre.customer_approval(conversation, "approval-1", False))
    with pytest.raises(LookupError):
        run(centre.customer_approval(conversation, "unknown", True))


@pytest.mark.parametrize("assist", [False, True])
@pytest.mark.parametrize("decisions", [(True, False), (False, True), (True, True), (False, False)])
def test_approval_batch_waits_for_all_decisions_and_does_not_replay_previous_batches(assist, decisions):
    def interrupted(*ids):
        return [{
            "type": "RUN_FINISHED",
            "outcome": {"type": "interrupt", "interrupts": [
                {"id": call_id, "metadata": {"agent_framework": {
                    "function_call": {"name": "restart_modem", "arguments": {"customer_id": "CUST-1002"}}
                }}}
                for call_id in ids
            ]},
        }]

    agents = ScriptedAgents(
        interrupted("approval-1", "approval-2"),
        interrupted("approval-3"),
        text("done", "Finished.") + [{"type": "RUN_FINISHED"}],
    )
    centre = ContactCentre(agents, signs_in=False, agent_label="test")
    conversation = centre.start(None)
    if assist:
        run(centre.assist(conversation, "Lea", "Restart the modem"))
    else:
        run(centre.customer_message(conversation, "Restart the modem"))

    def decide(call_id, approved):
        if assist:
            return centre.assist_approval(conversation, "Lea", call_id, approved)
        return centre.customer_approval(conversation, call_id, approved)

    run(decide("approval-2", decisions[1]))
    assert len(agents.calls) == 1
    assert not conversation.busy and not conversation.assist_busy
    if assist:
        assert not any(m["role"] == "system" and "restarted" in m["text"] for m in conversation.transcript)
    with pytest.raises(ValueError):
        run(decide("approval-2", decisions[1]))

    run(decide("approval-1", decisions[0]))
    assert len(agents.calls) == 2
    assert agents.calls[1][1]["resume"] == [
        {"interruptId": f"approval-{index}", "status": "resolved", "payload": {"approved": approved}}
        for index, approved in enumerate(decisions, 1)
    ]
    run(decide("approval-3", False))
    assert len(agents.calls) == 3
    assert agents.calls[2][1]["resume"] == [
        {"interruptId": "approval-3", "status": "resolved", "payload": {"approved": False}}
    ]


def test_assist_works_for_the_advisor_only():
    record = handover_record()
    interrupt = {"id": "a-1", "metadata": {"agent_framework": {"function_call": {"name": "restart_modem", "arguments": {}}}}}
    agents = ScriptedAgents(
        tool("c1", "escalate_to_human", record) + [{"type": "RUN_FINISHED"}],
        text("m1", "Suggested reply:\nHello") + [{"type": "RUN_FINISHED"}],
        [{"type": "RUN_FINISHED", "outcome": {"type": "interrupt", "interrupts": [interrupt]}}],
        tool("a-1", "restart_modem", {
            "restart_sent": True, "customer_id": "CUST-1005",
            "requested_at": "2026-11-04T14:39:03+00:00",
        }) + text("m2", "Done") + [{"type": "RUN_FINISHED"}],
    )
    centre = ContactCentre(agents, signs_in=False, agent_label="test")
    conversation = centre.start(None)
    run(centre.customer_message(conversation, "Nothing loads"))
    centre.take(conversation, "Lea")
    customer_messages = len(conversation.transcript)

    run(centre.assist(conversation, "Lea", "Suggest a reply."))
    path, body = agents.calls[1]
    assert path == ASSIST_PATH.format(who="CUST-1005")  # the customer named in the handover
    assert body["threadId"] == f"{conversation.id}-assist"
    assert record["handover_id"] in body["messages"][-1]["content"]
    assert "Nothing loads" in body["messages"][-1]["content"]
    assert [m["role"] for m in conversation.assist_transcript] == ["advisor", "agent"]
    assert len(conversation.transcript) == customer_messages  # the customer sees none of the assist thread

    run(centre.assist(conversation, "Lea", "Restart the modem."))
    assert conversation.assist_transcript[-1]["role"] == "approval"
    run(centre.assist_approval(conversation, "Lea", "a-1", True))
    assert agents.calls[3][1]["resume"][0]["payload"] == {"approved": True}
    # The customer is told about an action on their service, and who took it; SupportBuddy learns it is done.
    assert conversation.transcript[-1]["text"].startswith("Lea sent a remote restart command for your modem.")
    assert "do not run it again" in conversation.agent_history[-1]["content"]
    stored = backend.get_handover_status(record["handover_id"], "CUST-1005")
    assert stored["status"] == "in_progress"
    assert stored["advisor_message"] == conversation.transcript[-1]["text"]
    assert "2026-11-04T14:39:03+00:00" in stored["advisor_message"]


def test_assist_identifies_an_unknown_customer_then_works_on_that_customer_only():
    found = {"found": True, "customer": {"customer_id": "CUST-1004", "name": "Noah Martin"}}
    agents = ScriptedAgents(
        tool("c1", "identify_customer", found) + text("m1", "This is CUST-1004.") + [{"type": "RUN_FINISHED"}],
        text("m2", "Line is up.") + [{"type": "RUN_FINISHED"}],
    )
    centre = ContactCentre(agents, signs_in=False, agent_label="test")
    conversation = centre.start(None)
    conversation.mode = "advisor"

    run(centre.assist(conversation, "Lea", "Who is this customer?"))
    assert agents.calls[0][0] == ASSIST_PATH.format(who="unidentified")
    assert conversation.identified["customer_id"] == "CUST-1004"

    run(centre.assist(conversation, "Lea", "Check again."))
    assert agents.calls[1][0] == ASSIST_PATH.format(who="CUST-1004")


@pytest.mark.parametrize("approved,result,result_call", [
    (False, {"restart_sent": True, "customer_id": "CUST-1005"}, "restart-1"),
    (True, {"restart_sent": False, "customer_id": "CUST-1005"}, "restart-1"),
    (True, {"restart_sent": True, "customer_id": "CUST-1002"}, "restart-1"),
    (True, {"restart_sent": "true", "customer_id": "CUST-1005"}, "restart-1"),
    (True, {"restart_sent": True, "customer_id": "CUST-1005"}, "unrelated-call"),
    (True, {"isError": True, "content": [{"type": "text", "text": "restart failed"}]}, "restart-1"),
    (True, None, "restart-1"),
])
def test_advisor_restart_requires_approved_matching_success(approved, result, result_call):
    record = handover_record()
    interrupt = {"id": "restart-1", "metadata": {"agent_framework": {"function_call": {
        "name": "restart_modem", "arguments": {"customer_id": "CUST-1005"},
    }}}}
    events = [] if result is None else [{
        "type": "TOOL_CALL_RESULT", "toolCallId": result_call, "content": json.dumps(result),
    }]
    agents = ScriptedAgents(
        [{"type": "RUN_FINISHED", "outcome": {"interrupts": [interrupt]}}],
        events + [{"type": "RUN_FINISHED"}],
    )
    centre = ContactCentre(agents, signs_in=False, agent_label="test")
    conversation = centre.start(None)
    conversation.handover = record
    centre.take(conversation, "Lea")
    run(centre.assist(conversation, "Lea", "Restart the modem."))
    before = len(conversation.transcript)
    run(centre.assist_approval(conversation, "Lea", "restart-1", approved))
    assert len(conversation.transcript) == before
    assert backend.get_handover_status(record["handover_id"], "CUST-1005")["advisor_message"] is None
    assert not any("do not run it again" in message["content"] for message in conversation.agent_history)
    assert not conversation.assist_busy


def test_advisor_restart_uses_tool_call_identity_and_updates_only_current_case():
    record = handover_record()
    other = handover_record()
    interrupt = {"id": "approval-1", "toolCallId": "restart-1", "metadata": {"agent_framework": {"function_call": {
        "call_id": "restart-1", "name": "network___restart_modem", "arguments": {"customer_id": "CUST-1005"},
    }}}}
    result = {
        "restart_sent": True, "customer_id": "CUST-1005", "requested_at": "2026-11-04T14:39:03+00:00",
    }
    event = {"type": "TOOL_CALL_RESULT", "toolCallId": "restart-1", "content": json.dumps({
        "content": [{"type": "text", "text": json.dumps(result)}],
    })}
    agents = ScriptedAgents(
        [{"type": "RUN_FINISHED", "outcome": {"interrupts": [interrupt]}}],
        [event, event, {"type": "RUN_FINISHED"}],
    )
    centre = ContactCentre(agents, signs_in=False, agent_label="test")
    conversation = centre.start(None)
    conversation.handover = record
    centre.take(conversation, "Lea")
    run(centre.assist(conversation, "Lea", "Restart the modem."))
    before = len(conversation.transcript)
    run(centre.assist_approval(conversation, "Lea", "approval-1", True))
    assert len(conversation.transcript) == before + 1
    stored = backend.get_handover_status(record["handover_id"], "CUST-1005")
    assert stored["advisor_message"] == conversation.transcript[-1]["text"]
    assert stored["advisor"] == "Lea" and stored["status"] == "in_progress"
    assert conversation.handover["advisor_message"] == stored["advisor_message"]
    assert backend.get_handover_status(other["handover_id"], "CUST-1005")["advisor_message"] is None
    assert not backend.get_handover_status(record["handover_id"], "CUST-1002")["found"]
    centre.advisor_message(conversation, "Lea", "The customer will test the connection.")
    centre.close(conversation, "Lea")
    closed = backend.get_handover_status(record["handover_id"], "CUST-1005")
    assert closed["status"] == "resolved"
    assert stored["advisor_message"] in closed["advisor_message"]
    assert "The customer will test the connection." in closed["advisor_message"]


@pytest.mark.parametrize("failed", [False, True])
def test_handback_waits_for_advisor_execution_and_unblocks_after_completion(failed):
    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()
        interrupt = {"id": "restart-1", "metadata": {"agent_framework": {"function_call": {
            "name": "restart_modem", "arguments": {"customer_id": "CUST-1005"},
        }}}}

        async def runner(path, body):
            if not body.get("resume"):
                yield {"type": "RUN_FINISHED", "outcome": {"interrupts": [interrupt]}}
                return
            entered.set()
            await release.wait()
            if failed:
                raise httpx.ReadError("restart stream failed")
            yield {"type": "TOOL_CALL_RESULT", "toolCallId": "restart-1", "content": json.dumps({
                "restart_sent": True, "customer_id": "CUST-1005",
            })}
            yield {"type": "RUN_FINISHED"}

        record = handover_record()
        centre = ContactCentre(runner, signs_in=False, agent_label="test")
        conversation = centre.start(None)
        conversation.handover = record
        centre.take(conversation, "Lea")
        await centre.assist(conversation, "Lea", "Restart the modem.")
        task = asyncio.create_task(centre.assist_approval(conversation, "Lea", "restart-1", True))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            assert conversation.assist_busy
            assert backend.get_handover_status(record["handover_id"], "CUST-1005")["advisor_message"] is None
            with pytest.raises(ValueError, match="Wait for the assist agent"):
                centre.hand_back(conversation, "Lea")
            assert conversation.mode == "advisor"
        finally:
            release.set()
            await task
        assert not conversation.assist_busy
        centre.hand_back(conversation, "Lea")
        assert conversation.mode == "agent"
        stored = backend.get_handover_status(record["handover_id"], "CUST-1005")
        assert bool(stored["advisor_message"]) is not failed

    run(scenario())


def test_signed_in_conversations_use_one_agent_per_customer():
    agents = ScriptedAgents(text("m1", "Hi") + [{"type": "RUN_FINISHED"}])
    centre = ContactCentre(agents, signs_in=True, agent_label="test")
    with pytest.raises(ValueError):
        centre.start(None)
    conversation = centre.start("CUST-1002")
    run(centre.customer_message(conversation, "Hello"))
    assert agents.calls[0][0] == "/ag-ui/supportbuddy/CUST-1002"


def test_run_errors_are_shown_without_internal_details():
    agents = ScriptedAgents([{"type": "RUN_ERROR", "message": "boom"}])
    centre = ContactCentre(agents, signs_in=False, agent_label="test")
    conversation = centre.start(None)
    run(centre.customer_message(conversation, "Hello"))
    last = conversation.transcript[-1]
    assert last["role"] == "system" and "boom" not in last["text"]


# ----- HTTP API ---------------------------------------------------------------------------------


@pytest.fixture
def api():
    agents = ScriptedAgents(*[text("m1", "Hello!") + [{"type": "RUN_FINISHED"}] for _ in range(5)])
    centre = ContactCentre(agents, signs_in=False, agent_label="test")
    customers = [{"customer_id": "CUST-1002", "name": "Luc Martin"}]
    with TestClient(create_app(centre, {}, customers=customers)) as client:
        yield client, centre


def test_customer_conversation_over_http(api):
    client, centre = api
    assert client.get("/api/config").json() == {
        "agent": "test", "signs_in": False, "customers": [{"customer_id": "CUST-1002", "name": "Luc Martin"}],
    }
    conversation = client.post("/api/conversations", json={}).json()
    response = client.post(f"/api/conversations/{conversation['id']}/messages", json={"text": "Hi"})
    assert response.status_code == 202
    stored = centre.store.get(conversation["id"])
    for _ in range(50):
        if any(m["role"] == "agent" for m in stored.transcript):
            break
        time.sleep(0.05)
    assert stored.transcript[-1]["text"] == "Hello!"


def test_loopback_contact_centre_accepts_forwarded_host_and_streams_events():
    centre = ContactCentre(ScriptedAgents(), signs_in=False, agent_label="test")
    app = create_app(centre, {})
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}", headers={"Host": "x-8070.app.github.dev"}, trust_env=False,
            ) as client:
                for path in ("/chat", "/desk", "/chat.js", "/desk.js", "/common.js", "/api/config"):
                    assert client.get(path).status_code == 200
                conversation = client.post("/api/conversations", json={}).json()
                for path in (f"/api/conversations/{conversation['id']}/events", "/api/desk/events"):
                    with client.stream("GET", path) as response:
                        assert response.status_code == 200
                        assert response.headers["content-type"].startswith("text/event-stream")
                        first = next(response.iter_lines())
                        assert json.loads(first.removeprefix("data: "))["type"] == "snapshot"
        finally:
            server.should_exit = True
            thread.join(timeout=5)
        assert not thread.is_alive()


def test_api_rejects_bad_requests(api):
    client, _ = api
    conversation = client.post("/api/conversations", json={}).json()
    cid = conversation["id"]
    assert client.post("/api/conversations", content="{}", headers={"Content-Type": "text/plain"}).status_code == 415
    assert client.post("/api/conversations", json={"customer_id": "CUST-9999"}).status_code == 400
    assert client.post("/api/conversations/not-an-id/messages", json={"text": "x"}).status_code == 404
    assert client.post(f"/api/conversations/{cid}/messages", json={"text": ""}).status_code == 400
    assert client.post(f"/api/conversations/{cid}/messages", json={"text": "x" * 2001}).status_code == 400
    assert client.post(f"/api/conversations/{cid}/approvals", json={"interrupt_id": "x", "approved": "yes"}).status_code == 400
    assert client.post(f"/api/conversations/{cid}/approvals", json={"interrupt_id": "x", "approved": True}).status_code == 409
    assert client.post(f"/api/desk/conversations/{cid}/take", json={}).status_code == 400
    assert client.post(f"/api/desk/conversations/{cid}/messages", json={"advisor": "Lea", "text": "hi"}).status_code == 409
    assert client.post(f"/api/desk/conversations/{cid}/delete", json={"advisor": "Lea"}).status_code == 404
    assert client.post(f"/api/desk/conversations/{cid}/take", json={"advisor": "Lea"}).status_code == 200
    assert client.post(f"/api/desk/conversations/{cid}/messages", json={"advisor": "Lea", "text": "hi"}).status_code == 200
    assert client.post(f"/api/conversations/{cid}/leave", content="{}", headers={"Content-Type": "text/plain"}).status_code == 415
    assert client.post("/api/conversations/not-an-id/leave", json={}).status_code == 404
    assert client.post(f"/api/conversations/{cid}/leave", json={}).status_code == 200
    assert client.post(f"/api/conversations/{cid}/messages", json={"text": "x"}).status_code == 409


@pytest.mark.parametrize("page", ["/", "/chat", "/desk", "/chat.js", "/desk.js", "/common.js", "/centre.css", "/icon.svg"])
def test_pages_are_served_with_security_headers(api, page):
    client, _ = api
    response = client.get(page)
    assert response.status_code == 200
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("script", ["chat.js", "desk.js", "common.js"])
def test_conversation_text_is_never_parsed_as_html(script):
    source = (STATIC / script).read_text(encoding="utf-8")
    for unsafe in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert unsafe not in source
