"""Offline guarantees for the independent optional workflow lab."""

import json
import re
import subprocess
import sys
import threading
from pathlib import Path

import httpx
import pytest
from agent_framework import BaseChatClient, ChatResponse, ChatResponseUpdate, Content, Message, ResponseStream
from agent_framework_ag_ui import AgentFrameworkWorkflow, add_agent_framework_fastapi_endpoint
from fastapi import FastAPI

from lab_backend import backend
from workflow_lab import ModelMeter, RestartApproval, RestartDecision, Verdict, build_workflow, run_case, verdict_allowed


@pytest.fixture
def anyio_backend():
    return "asyncio"


class ScriptedClient(BaseChatClient):
    def __init__(self, role, overrides=None):
        super().__init__()
        self.role = role
        self.overrides = overrides or {}
        self.calls = 0
        self.inputs = []

    def reply(self, messages):
        last = messages[-1].text
        self.inputs.append([message.text for message in messages])
        self.calls += 1
        if self.role in self.overrides:
            return self.overrides[self.role]
        if self.role == "intake":
            customer = re.search(r"CUST-\d+", last)
            return json.dumps({
                "customer_id": customer.group(0) if customer else None,
                "email": None, "phone": None, "symptom": "internet not working",
                "language": "en", "in_scope": "bill" not in last,
            })
        payload = json.loads(last)
        if self.role == "diagnose":
            evidence = payload["evidence"]
            if any(item["status"] == "open" for item in evidence["incidents"]):
                decision, runbook, title = "outage", "RB-09", "Known area incidents"
            elif evidence["line"]["resyncs_24h"] > 5:
                decision, runbook, title = "line_fault", "RB-07", "Intermittent connection drops"
            elif not evidence["equipment"][0]["reachable"]:
                decision, runbook, title = "restart", "RB-02", "Modem restart procedure"
            else:
                decision, runbook, title = "wifi_advice", "RB-03", "Wi-Fi interference"
            return json.dumps({"decision": decision, "runbook_id": runbook, "runbook_title": title, "reason": decision})
        verdict = payload["verdict"]
        return f"{verdict['decision']}: {json.dumps(payload['restart'])} [{verdict['runbook_id']} {verdict['runbook_title']}]"

    def _inner_get_response(self, *, messages, stream, options, **kwargs):
        text = self.reply(messages)
        if stream:
            async def updates():
                yield ChatResponseUpdate(role="assistant", contents=[Content.from_text(text)])
            return ResponseStream(updates(), finalizer=ChatResponse.from_updates)

        async def response():
            return ChatResponse(messages=[Message("assistant", [text])], response_id=f"scripted-{self.calls}")
        return response()


class Knowledge:
    def __init__(self):
        self.calls = []
        self.opened = 0
        self.closed = 0

    async def __aenter__(self):
        self.opened += 1
        return self

    async def __aexit__(self, *args):
        self.closed += 1

    async def call_tool(self, name, **kwargs):
        self.calls.append((name, kwargs))
        return [Content.from_text("\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((Path(__file__).parents[1] / "src" / "knowledge" / "runbooks").glob("*.md"))
        ))]


def build(overrides=None):
    clients = {role: ScriptedClient(role, overrides) for role in ("intake", "diagnose", "answer")}
    knowledge = Knowledge()
    return build_workflow(clients=clients, knowledge=knowledge), clients, knowledge


@pytest.mark.anyio
async def test_real_client_construction_reuses_lab_model_settings(monkeypatch):
    import workflow_lab
    from lab_agent import build_agent

    monkeypatch.setenv("AZURE_AI_PROJECT_ENDPOINT", "https://example.services.ai.azure.com/api/projects/lab")
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "existing-router")
    monkeypatch.setenv("WORKSHOP_RESOURCE_PREFIX", "lab-test")
    monkeypatch.setenv("SUPPORTBUDDY_REASONING_EFFORT", "medium")
    built = []

    def factory(*args, **kwargs):
        agent = build_agent(*args, **kwargs)
        built.append(agent)
        return agent

    monkeypatch.setattr(workflow_lab, "build_agent", factory)
    workflow = build_workflow(knowledge=Knowledge())
    async with workflow_lab.open_workflow(workflow):
        pass
    assert len(built) == 3
    for executor in workflow.get_executors_list():
        if hasattr(executor, "agent"):
            assert executor.agent.default_options["reasoning"] == {"effort": "medium"}


def final_text(result):
    return "\n".join(item if isinstance(item, str) else item.text for item in result.get_outputs())


@pytest.mark.anyio
@pytest.mark.parametrize("customer,decision,counts,runbook", [
    ("CUST-1001", "outage", [1, 1, 1], "RB-09"),
    ("CUST-1002", "restart", [1, 1, 1], "RB-02"),
    ("CUST-1003", "wifi_advice", [1, 1, 1], "RB-03"),
    ("CUST-1004", "line_fault", [1, 1, 0], "RB-07"),
])
async def test_scenario_paths_and_model_counts(customer, decision, counts, runbook):
    workflow, clients, knowledge = build()
    result = await workflow.run(f"I'm {customer}, my internet is down")
    requests = result.get_request_info_events()
    if decision == "restart":
        assert len(requests) == 1
        assert requests[0].data.customer_id == customer
        result = await workflow.run(responses={requests[0].request_id: {"approved": True}})
    else:
        assert not requests
    assert f"[{runbook} " in final_text(result)
    assert [client.calls for client in clients.values()] == counts
    assert len(backend.list_actions()) == (1 if decision == "restart" else 0)
    assert len(backend.list_handovers()) == (1 if decision == "line_fault" else 0)
    if decision == "line_fault":
        assert "HO-" in final_text(result)
        assert backend.list_handovers()[0]["customer_language"] == "en"
    assert len(knowledge.calls) == knowledge.opened == knowledge.closed == 1
    for executor in workflow.get_executors_list():
        if hasattr(executor, "agent"):
            meter = executor.agent.client
            assert isinstance(meter, ModelMeter)
            assert meter.calls == clients[executor.id].calls


@pytest.mark.anyio
@pytest.mark.parametrize("response", [False, {"approved": False}, {"approved": True}, True])
async def test_pause_resume_approval_shapes(response):
    workflow, clients, _ = build()
    result = await workflow.run("CUST-1002 internet down")
    assert not backend.list_actions()
    assert clients["answer"].calls == 0
    request = result.get_request_info_events()[0]
    result = await workflow.run(responses={request.request_id: response})
    approved = response if isinstance(response, bool) else response["approved"]
    assert len(backend.list_actions()) == int(approved)
    assert len(result.get_request_info_events()) == 0
    assert clients["answer"].calls == 1


def test_devui_renders_a_typed_approval_form():
    from agent_framework_devui._utils import extract_response_type_from_executor, generate_input_schema

    workflow, _, _ = build()
    decide = next(executor for executor in workflow.get_executors_list() if executor.id == "decide")
    response_type = extract_response_type_from_executor(decide, RestartApproval)
    assert response_type is RestartDecision
    assert generate_input_schema(response_type)["properties"]["approved"]["type"] == "boolean"


@pytest.mark.anyio
@pytest.mark.parametrize("prompt", ["CUST-9999 internet down", "internet down", "what is my bill?"])
async def test_intake_ends_before_back_office_checks(prompt, monkeypatch):
    for name in ("get_line_status", "get_known_incidents", "check_equipment"):
        monkeypatch.setattr(backend, name, lambda *args, **kwargs: pytest.fail("No diagnostics allowed"))
    workflow, clients, knowledge = build()
    result = await workflow.run(prompt)
    assert final_text(result)
    assert [client.calls for client in clients.values()] == [1, 0, 0]
    assert not knowledge.calls
    assert not backend.list_actions()


@pytest.mark.anyio
@pytest.mark.parametrize("role,invalid", [
    ("intake", "not JSON"),
    ("intake", '{"in_scope": "yes"}'),
    ("diagnose", "not JSON"),
    ("diagnose", '{"decision": "guess", "runbook_id": "RB-02", "runbook_title": "Restart", "reason": "guess"}'),
    ("diagnose", '{"decision": "unknown", "runbook_id": "", "runbook_title": "", "reason": "uncertain"}'),
])
async def test_invalid_agent_output_falls_back(role, invalid):
    workflow, clients, _ = build({role: invalid})
    result = await workflow.run("CUST-1002 internet down")
    assert "human advisor" in final_text(result)
    assert not result.get_request_info_events()
    assert clients["answer"].calls == 0
    assert not backend.list_actions()
    assert len(backend.list_handovers()) == int(role == "diagnose")


@pytest.mark.anyio
async def test_restart_verdict_is_refused_when_an_incident_is_open():
    bad_verdict = Verdict(decision="restart", runbook_id="RB-02", runbook_title="Modem restart procedure", reason="ping failed")
    workflow, clients, _ = build({"diagnose": bad_verdict.model_dump_json()})
    result = await workflow.run("CUST-1001 internet down")
    assert "human advisor" in final_text(result)
    assert not result.get_request_info_events()
    assert not backend.list_actions()
    assert clients["answer"].calls == 0


@pytest.mark.anyio
async def test_checks_run_in_parallel_before_collect(monkeypatch):
    barrier = threading.Barrier(3, timeout=10)
    started = []

    def wrap(name):
        original = getattr(backend, name)

        def check(*args, **kwargs):
            started.append(name)
            barrier.wait()
            return original(*args, **kwargs)
        return check

    for name in ("get_line_status", "get_known_incidents", "check_equipment"):
        monkeypatch.setattr(backend, name, wrap(name))
    workflow, clients, _ = build()
    await workflow.run("CUST-1003 slow Wi-Fi")
    assert len(started) == 3
    assert clients["diagnose"].calls == 1


@pytest.mark.anyio
async def test_repeated_streamed_runs_do_not_share_agent_history():
    workflow, clients, _ = build()
    for customer, expected in (("CUST-1001", "outage"), ("CUST-1003", "wifi_advice")):
        result = await workflow.run(f"{customer} internet down", stream=True).get_final_response()
        assert expected in final_text(result)
    assert "CUST-1001" not in " ".join(clients["intake"].inputs[-1])
    assert "INC-5001" not in " ".join(clients["diagnose"].inputs[-1])
    assert "INC-5001" not in " ".join(clients["answer"].inputs[-1])


@pytest.mark.anyio
async def test_console_runner_reports_observed_steps_and_model_calls():
    workflow, _, _ = build()
    lines = []
    summary = await run_case(workflow, "CUST-1003 internet down", emit=lines.append)
    assert summary["decision"] == "wifi_advice"
    assert summary["model_calls"] == 3
    assert len(summary["steps"]) == 9
    assert summary["runbooks"] == "RB-03"
    assert lines[-1].startswith("Summary: steps=9 model_calls=3 elapsed=")


@pytest.mark.anyio
async def test_workflow_factory_isolates_two_ag_ui_threads():
    app = FastAPI()
    built = []

    def factory(thread_id):
        workflow, clients, _ = build()
        built.append((thread_id, workflow, clients))
        return workflow

    add_agent_framework_fastapi_endpoint(
        app, AgentFrameworkWorkflow(workflow_factory=factory, name="workflow-test"), "/wf",
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for thread_id, customer, expected in (("one", "CUST-1001", "outage"), ("two", "CUST-1003", "wifi_advice")):
            response = await client.post("/wf", json={
                "threadId": thread_id, "runId": f"run-{thread_id}", "messages": [
                    {"id": f"msg-{thread_id}", "role": "user", "content": f"{customer} internet down"},
                ], "tools": [], "context": [], "state": {}, "forwardedProps": {},
            })
            assert response.status_code == 200
            assert expected in response.text
            assert "STEP_STARTED" in response.text
    assert len(built) == 2
    assert built[0][1] is not built[1][1]
    assert "CUST-1001" not in " ".join(built[1][2]["intake"].inputs[0])


def test_import_does_not_load_numbered_stages():
    source = str(Path(__file__).parents[1] / "src")
    result = subprocess.run([
        sys.executable, "-c",
        f"import sys; sys.path.insert(0, {source!r}); import workflow_lab; "
        "assert 'main' not in sys.modules; assert not any(name.startswith('solutions.stage_') for name in sys.modules)",
    ], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


def test_open_incident_refuses_restart():
    customer = backend.identify_customer(customer_id="CUST-1001")["customer"]
    evidence = {
        "line": backend.get_line_status(customer["line_ids"][0])["line"],
        "incidents": backend.get_known_incidents(postcode=customer["postcode"])["incidents"],
        "equipment": backend.check_equipment(customer["customer_id"])["equipment"],
    }
    verdict = Verdict(decision="restart", runbook_id="RB-02", runbook_title="Restart", reason="Modem unreachable")

    assert not verdict_allowed(verdict, evidence)