"""Use the real Foundry/AG-UI adapters with a scripted Responses HTTP service."""

import json

import httpx
import pytest
from agent_framework import tool
from agent_framework.foundry import FoundryAgent
from azure.ai.projects.aio import AIProjectClient
from azure.identity.aio import AzureCliCredential
from fastapi.testclient import TestClient
from openai import AsyncOpenAI

from contact_centre.centre import ContactCentre
from contact_centre.server import create_app

PATH = "/ag-ui/supportbuddy/guest"


def _stream(response_id, output, conversation_id):
    response = {
        "id": response_id, "object": "response", "created_at": 1,
        "model": "model-router", "status": "completed", "output": output,
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        "conversation": {"id": conversation_id} if conversation_id else None,
    }
    events = [{"type": "response.created", "response": {**response, "output": [], "status": "in_progress"}}]
    for index, item in enumerate(output):
        events.append({"type": "response.output_item.added", "output_index": index, "item": item})
        if item["type"] == "function_call":
            events.append({
                "type": "response.function_call_arguments.delta", "output_index": index,
                "item_id": item["id"], "delta": item["arguments"],
            })
            events.append({
                "type": "response.function_call_arguments.done", "output_index": index,
                "item_id": item["id"], "arguments": item["arguments"],
            })
        if item["type"] == "message":
            events.append({
                "type": "response.output_text.delta", "output_index": index, "content_index": 0,
                "item_id": item["id"], "delta": item["content"][0]["text"], "logprobs": [],
            })
        events.append({"type": "response.output_item.done", "output_index": index, "item": item})
    events.append({"type": "response.completed", "response": response})
    return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, text="".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events
    ))


def _events(response):
    response.raise_for_status()
    return [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]


@pytest.mark.parametrize("approved", [True, False])
def test_remote_mcp_history_is_not_replayed_as_local_functions_after_approval(monkeypatch, approved):
    requests = []
    actions = []
    conversation_ids = []

    @tool(approval_mode="always_require")
    def restart_modem(customer_id: str) -> str:
        """Simulate one approved modem restart."""
        actions.append(customer_id)
        return json.dumps({"customer_id": customer_id, "restarted": True})

    def provider(request):
        body = json.loads(request.content)
        if request.url.path.endswith("/conversations"):
            conversation_id = f"conv_test_{len(conversation_ids) + 1}"
            conversation_ids.append(conversation_id)
            return httpx.Response(200, json={
                "id": conversation_id, "object": "conversation", "created_at": 1, "metadata": {},
            })
        assert request.url.path.endswith("/responses")
        requests.append(body)
        for item in body["input"]:
            if item.get("type") == "function_call" and "." in item["name"]:
                return httpx.Response(400, json={"error": {
                    "message": "Invalid input[1].name: expected ^[a-zA-Z0-9_-]+$",
                    "type": "invalid_request_error", "param": "input[1].name", "code": "invalid_value",
                }})
        if len(requests) == 1:
            return _stream("resp_diagnostics", [
                {
                    "id": "mcp_identify", "type": "mcp_call", "name": "network.identify_customer",
                    "server_label": "supportbuddy_tools", "arguments": '{"customer_id":"CUST-1002"}',
                    "output": '{"found":true,"customer":{"customer_id":"CUST-1002"}}', "status": "completed",
                },
                {
                    "id": "fc_restart", "type": "function_call", "call_id": "call_restart",
                    "name": "restart_modem", "arguments": '{"customer_id":"CUST-1002"}', "status": "completed",
                },
            ], body.get("conversation"))
        return _stream(f"resp_answer_{len(requests)}", [{
            "id": f"msg_answer_{len(requests)}", "type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": "Please wait three minutes and test again." if approved
                         else "The restart was rejected; it did not run.", "annotations": []}],
        }], body.get("conversation"))

    openai = AsyncOpenAI(
        api_key="synthetic-test-value", base_url="https://foundry.example/openai/v1/",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(provider)),
    )
    monkeypatch.setattr(AIProjectClient, "get_openai_client", lambda self, **kwargs: openai)
    agent = FoundryAgent(
        project_endpoint="https://foundry.example/api/projects/test", agent_name="test-prompt",
        credential=AzureCliCredential(), tools=[restart_modem], default_options={"store": True},
    )
    centre = ContactCentre(lambda path, body: None, signs_in=False, agent_label="test")
    other_path = "/ag-ui/supportbuddy/other"
    app = create_app(centre, {PATH: agent, other_path: agent})
    with TestClient(app) as client:
        events = _events(client.post(PATH, json={
            "threadId": "thread-one", "runId": "first-run",
            "messages": [{"id": "user-1", "role": "user", "content": "CUST-1002: please restart my modem."}],
        }))
        assert not any(event["type"] == "RUN_ERROR" for event in events)
        assert actions == []
        finished = next(event for event in events if event["type"] == "RUN_FINISHED")
        (interrupt,) = finished["outcome"]["interrupts"]
        history = next(event["messages"] for event in events if event["type"] == "MESSAGES_SNAPSHOT")
        assert any(
            call["function"]["name"] == "network.identify_customer"
            for message in history for call in message.get("toolCalls", [])
        )
        resumed = _events(client.post(PATH, json={
            "threadId": "thread-one", "runId": "resume-run", "messages": history,
            "resume": [{"interruptId": interrupt["id"], "status": "resolved", "payload": {"approved": approved}}],
        }))
        assert actions == (["CUST-1002"] if approved else [])
        assert not any(event["type"] == "RUN_ERROR" for event in resumed), resumed
        assert any(event["type"] == "TEXT_MESSAGE_CONTENT" for event in resumed)
        assert "conv_test_" not in json.dumps([*events, *resumed])
        assert len(requests) == 2
        assert requests[0]["conversation"] == requests[1]["conversation"]
        assert len(conversation_ids) == 1
        assert all(item.get("type") != "function_call" for item in requests[1]["input"])
        assert any(item.get("type") == "function_call_output" for item in requests[1]["input"])
        followup_history = next(event["messages"] for event in resumed if event["type"] == "MESSAGES_SNAPSHOT")
        followup = _events(client.post(PATH, json={
            "threadId": "thread-one", "runId": "followup-run",
            "messages": [*followup_history, {"id": "user-2", "role": "user", "content": "Thank you."}],
        }))
        assert not any(event["type"] == "RUN_ERROR" for event in followup), followup
        assert len(conversation_ids) == 1
        assert requests[-1]["conversation"] == requests[0]["conversation"]
        assert len(requests[-1]["input"]) == 1
        assert requests[-1]["input"][0]["role"] == "user"
        assert actions == (["CUST-1002"] if approved else [])
        separate = _events(client.post(PATH, json={
            "threadId": "thread-two", "runId": "separate-run",
            "messages": [{"id": "user-new", "role": "user", "content": "Hello."}],
        }))
        assert not any(event["type"] == "RUN_ERROR" for event in separate), separate
        assert len(conversation_ids) == 2
        assert requests[-1]["conversation"] != requests[0]["conversation"]
        other_route = _events(client.post(other_path, json={
            "threadId": "thread-one", "runId": "other-route-run",
            "messages": [{"id": "user-other", "role": "user", "content": "Hello."}],
        }))
        assert not any(event["type"] == "RUN_ERROR" for event in other_route), other_route
        assert len(conversation_ids) == 3
        assert requests[-1]["conversation"] not in [request["conversation"] for request in requests[:-1]]
