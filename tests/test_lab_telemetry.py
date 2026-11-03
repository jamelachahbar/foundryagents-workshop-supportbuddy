"""Tracing is opt-in and does not change approvals or protocol payloads."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import lab_telemetry as telemetry
from contact_centre.centre import ContactCentre, http_runner
from contact_centre.server import create_app


@pytest.fixture
def spans(monkeypatch):
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    monkeypatch.setattr(telemetry, "tracer", tracer)
    monkeypatch.setattr(telemetry, "_configured", False)
    monkeypatch.setattr(telemetry, "_capture_content", False)
    monkeypatch.delenv("SUPPORTBUDDY_TRACING", raising=False)
    monkeypatch.delenv("SUPPORTBUDDY_TRACE_CONTENT", raising=False)
    yield tracer, exporter
    provider.shutdown()


def test_disabled_tracing_does_not_read_project_connection(spans):
    project = Mock()
    telemetry.configure_tracing(project)
    project.telemetry.get_application_insights_connection_string.assert_not_called()


@pytest.mark.parametrize("capture", [False, True])
def test_setup_once_and_content_requires_explicit_opt_in(monkeypatch, spans, capture):
    monkeypatch.setenv("SUPPORTBUDDY_TRACING", "true")
    if capture:
        monkeypatch.setenv("SUPPORTBUDDY_TRACE_CONTENT", "true")
    project = Mock()
    project.telemetry.get_application_insights_connection_string.return_value = "not-a-real-connection"
    setup = Mock()
    trace_exporter, log_exporter = Mock(), Mock()
    monkeypatch.setattr(telemetry, "configure_otel_providers", setup)
    monkeypatch.setattr(telemetry, "AzureMonitorTraceExporter", trace_exporter)
    monkeypatch.setattr(telemetry, "AzureMonitorLogExporter", log_exporter)
    telemetry.configure_tracing(project)
    telemetry.configure_tracing(project)
    setup.assert_called_once()
    assert setup.call_args.kwargs["service_name"] == "supportbuddy-prompt-client"
    assert setup.call_args.kwargs["enable_sensitive_data"] is capture
    assert setup.call_args.kwargs["enable_message_events"] is capture
    assert telemetry._capture_content is capture
    assert trace_exporter.call_args.kwargs["disable_offline_storage"] is True
    project.telemetry.get_application_insights_connection_string.assert_called_once()


def test_missing_connection_fails_startup(monkeypatch, spans):
    monkeypatch.setenv("SUPPORTBUDDY_TRACING", "true")
    project = Mock()
    project.telemetry.get_application_insights_connection_string.side_effect = ValueError("No connection")
    with pytest.raises(ValueError, match="No connection"):
        telemetry.configure_tracing(project)
    assert not telemetry._configured


def test_invalid_capture_flag_fails_explicitly(monkeypatch, spans):
    monkeypatch.setenv("SUPPORTBUDDY_TRACING", "true")
    monkeypatch.setenv("SUPPORTBUDDY_TRACE_CONTENT", "maybe")
    with pytest.raises(ValueError, match="must be true or false"):
        telemetry.configure_tracing(Mock())


def test_disabled_local_tracing_does_not_create_project_client(monkeypatch, spans):
    client, credential = Mock(), Mock()
    monkeypatch.setattr(telemetry, "AIProjectClient", client)
    monkeypatch.setattr(telemetry, "AzureCliCredential", credential)
    telemetry.configure_local_tracing()
    client.assert_not_called()
    credential.assert_not_called()


def test_local_tracing_uses_linked_project_and_configures_once(monkeypatch, spans):
    import lab_agent

    monkeypatch.setenv("SUPPORTBUDDY_TRACING", "true")
    endpoint = "https://example.services.ai.azure.com/api/projects/lab"
    monkeypatch.setattr(lab_agent, "load_settings", lambda: {"AZURE_AI_PROJECT_ENDPOINT": endpoint})
    client, credential = MagicMock(), MagicMock()
    monkeypatch.setattr(telemetry, "AIProjectClient", client)
    monkeypatch.setattr(telemetry, "AzureCliCredential", credential)

    def configured(project, *, service_name):
        assert project is client.return_value.__enter__.return_value
        assert service_name == "supportbuddy-local"
        telemetry._configured = True

    setup = Mock(side_effect=configured)
    monkeypatch.setattr(telemetry, "configure_tracing", setup)
    telemetry.configure_local_tracing()
    telemetry.configure_local_tracing()
    client.assert_called_once_with(endpoint=endpoint, credential=credential.return_value.__enter__.return_value)
    setup.assert_called_once()
    client.return_value.__exit__.assert_called_once()
    credential.return_value.__exit__.assert_called_once()


def test_local_tracing_connection_failure_is_not_swallowed(monkeypatch, spans):
    import lab_agent

    monkeypatch.setenv("SUPPORTBUDDY_TRACING", "true")
    monkeypatch.setattr(lab_agent, "load_settings", lambda: {"AZURE_AI_PROJECT_ENDPOINT": "https://example.test"})
    client = MagicMock()
    client.return_value.__enter__.side_effect = ValueError("No monitoring connection")
    monkeypatch.setattr(telemetry, "AIProjectClient", client)
    monkeypatch.setattr(telemetry, "AzureCliCredential", MagicMock())
    with pytest.raises(ValueError, match="No monitoring connection"):
        telemetry.configure_local_tracing()
    assert not telemetry._configured


def test_devui_initializes_local_tracing_before_serving(monkeypatch):
    import lab_agent

    calls = Mock()
    monkeypatch.setattr(lab_agent, "configure_local_tracing", calls.tracing)
    monkeypatch.setattr(lab_agent, "_parse_args", lambda: SimpleNamespace(prompt=None))
    monkeypatch.setattr(lab_agent, "_devui_token", lambda: "offline-test-token")
    monkeypatch.setattr(lab_agent, "serve_branded", calls.serve)
    monkeypatch.setattr(lab_agent, "_async_providers", lambda _: [])
    lab_agent.run(Mock())
    assert [call[0] for call in calls.mock_calls] == ["tracing", "serve"]


@pytest.mark.parametrize("prompt_agent", [False, True])
def test_contact_centre_initializes_tracing_for_local_modules_only(monkeypatch, prompt_agent):
    import lab_agent
    import prompt_agent as published
    from contact_centre import assist, server

    monkeypatch.setattr(sys, "argv", ["contact_centre", *(["--prompt-agent"] if prompt_agent else ["hosted_agent"])])
    monkeypatch.setattr(assist, "build_assist_agent", lambda _: Mock())
    monkeypatch.setattr(lab_agent, "stage_signs_in", lambda _: False)
    monkeypatch.setattr(lab_agent, "stage_agent", lambda _: Mock())
    monkeypatch.setattr(published, "connect_prompt_agent", lambda: Mock())
    monkeypatch.setattr(published, "prompt_agent_name", lambda: "lab-test-supportbuddy-prompt")
    monkeypatch.setattr(server, "create_app", lambda *args, **kwargs: Mock())
    calls = Mock()
    monkeypatch.setattr(server, "configure_local_tracing", calls.tracing)
    monkeypatch.setattr(server.uvicorn, "run", calls.serve)
    server.main()
    assert [call[0] for call in calls.mock_calls] == (["serve"] if prompt_agent else ["tracing", "serve"])


def test_devui_keeps_azure_exporter_and_payload_opt_out():
    script = """
import asyncio
import os
from unittest.mock import Mock, patch
from agent_framework.observability import OBSERVABILITY_SETTINGS
from opentelemetry import trace
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
import lab_telemetry
from devui_branding import SupportBuddyDevServer

os.environ["SUPPORTBUDDY_TRACING"] = "true"
os.environ["SUPPORTBUDDY_TRACE_CONTENT"] = "false"
exporter = InMemorySpanExporter()
project = Mock()
project.telemetry.get_application_insights_connection_string.return_value = "unused"
with patch.object(lab_telemetry, "AzureMonitorTraceExporter", return_value=exporter), \\
     patch.object(lab_telemetry, "AzureMonitorLogExporter", return_value=InMemoryLogRecordExporter()):
    lab_telemetry.configure_tracing(project, service_name="supportbuddy-local")
provider = trace.get_tracer_provider()
server = SupportBuddyDevServer(auth_token="offline-test-token")
asyncio.run(server._ensure_executor())
assert trace.get_tracer_provider() is provider
assert not OBSERVABILITY_SETTINGS.SENSITIVE_DATA_ENABLED
with trace.get_tracer("test").start_as_current_span("local-ui-test"):
    pass
provider.force_flush()
(span,) = exporter.get_finished_spans()
assert span.name == "local-ui-test"
assert span.resource.attributes["service.name"] == "supportbuddy-local"
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1] / "src",
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("capture", [False, True])
def test_protocol_capture_excludes_snapshots_and_records_approvals(monkeypatch, spans, capture):
    tracer, exporter = spans
    monkeypatch.setattr(telemetry, "_capture_content", capture)
    with tracer.start_as_current_span("turn") as span:
        telemetry.record_input(span, {
            "messages": [
                {"role": "user", "content": "synthetic customer message"},
                {"role": "assistant", "content": "opaque reasoning excluded"},
            ],
            "resume": [{"interruptId": "approval-1", "payload": {"approved": False}}],
        })
        telemetry.record_event(span, {
            "type": "TOOL_CALL_RESULT", "toolCallId": "tool-1", "content": '{"customer_id":"CUST-1002"}',
        })
        telemetry.record_event(span, {"type": "MESSAGES_SNAPSHOT", "messages": ["encrypted_content: private"]})
        telemetry.record_event(span, {"type": "RUN_FINISHED", "outcome": {"interrupts": [{
            "id": "approval-1", "metadata": {"agent_framework": {
                "function_call": {"name": "restart_modem", "arguments": {"customer_id": "CUST-1002"}},
            }},
        }]}})
        telemetry.record_event(span, {"type": "RUN_ERROR", "message": "sensitive error details"})
    (finished,) = exporter.get_finished_spans()
    events = {event.name: dict(event.attributes) for event in finished.events}
    encoded = json.dumps(events)
    assert ("synthetic customer message" in encoded) is capture
    if capture:
        assert events["customer.message"]["supportbuddy.message.content"] == "synthetic customer message"
    assert ("CUST-1002" in encoded) is capture
    assert "opaque reasoning" not in encoded and "encrypted_content" not in encoded
    assert "sensitive error details" not in encoded
    assert events["approval.resume"]["approval.approved"] is False
    assert events["approval.requested"]["approval.interrupt.id"] == "approval-1"
    assert finished.status.status_code == trace.StatusCode.ERROR


def test_decision_correlates_to_original_batch(spans):
    _, exporter = spans
    telemetry.record_approval("conversation-1", {
        "approval_batch": "run-1", "interrupt_id": "approval-1", "tool": "restart_modem",
    }, False)
    (span,) = exporter.get_finished_spans()
    assert span.attributes["gen_ai.conversation.id"] == "conversation-1"
    assert span.attributes["supportbuddy.approval.batch"] == "run-1"
    assert span.attributes["approval.approved"] is False


def test_runner_propagates_context_without_changing_request_or_events(monkeypatch, spans):
    from contact_centre import centre

    tracer, exporter = spans
    monkeypatch.setattr(centre, "tracer", tracer)
    events = [{"type": "TOOL_CALL_START", "toolCallId": "call-1", "toolCallName": "identify_customer"},
              {"type": "RUN_FINISHED"}]
    body = {"threadId": "thread-1", "runId": "run-1", "messages": []}
    seen = []

    def handle(request):
        assert json.loads(request.content) == body
        seen.append(request.headers["traceparent"])
        return httpx.Response(200, text="".join(f"data: {json.dumps(e)}\n\n" for e in events))

    client = httpx.AsyncClient
    monkeypatch.setattr(centre.httpx, "AsyncClient", lambda **kwargs: client(
        **kwargs, transport=httpx.MockTransport(handle),
    ))

    async def consume():
        return [event async for event in http_runner("http://test")("/ag-ui/supportbuddy/CUST-1002", body)]

    assert asyncio.run(consume()) == events
    (span,) = exporter.get_finished_spans()
    assert seen[0].split("-")[1] == f"{span.context.trace_id:032x}"
    assert span.attributes["supportbuddy.run.id"] == "run-1"
    assert span.attributes["gen_ai.conversation.id"] == "thread-1"
    assert span.attributes["supportbuddy.agent.route"] == "/ag-ui/supportbuddy"
    assert "CUST-1002" not in str(span.attributes)


def test_runner_span_survives_generator_finalization_in_another_context(monkeypatch, spans):
    from contact_centre import centre

    tracer, exporter = spans
    monkeypatch.setattr(centre, "tracer", tracer)
    client = httpx.AsyncClient
    monkeypatch.setattr(centre.httpx, "AsyncClient", lambda **kwargs: client(
        **kwargs, transport=httpx.MockTransport(lambda request: httpx.Response(
            200, text='data: {"type":"RUN_FINISHED"}\n\n',
        )),
    ))

    async def consume_then_close():
        events = http_runner("http://test")("/ag-ui/test", {"threadId": "thread", "runId": "run"})
        await anext(events)
        # Async-generator finalizers may run in a different task/context.
        await asyncio.create_task(events.aclose())

    asyncio.run(consume_then_close())
    assert len(exporter.get_finished_spans()) == 1


def test_ag_ui_server_extracts_propagated_trace_context():
    app = create_app(ContactCentre(Mock(), signs_in=False, agent_label="test"), {})

    @app.get("/ag-ui/test")
    async def current_trace():
        return {"trace_id": f"{trace.get_current_span().get_span_context().trace_id:032x}"}

    with TestClient(app) as client:
        response = client.get("/ag-ui/test", headers={
            "traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
        })
    assert response.json()["trace_id"] == "0123456789abcdef0123456789abcdef"


def test_named_prompt_agent_is_pinned_without_republishing(monkeypatch, spans):
    import prompt_agent

    project = Mock()
    project.agents.get.return_value.versions.latest = SimpleNamespace(
        name="router-test", version="1", definition={"tools": []},
    )
    manager = Mock()
    manager.__enter__ = Mock(return_value=project)
    manager.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(prompt_agent, "AIProjectClient", Mock(return_value=manager))
    monkeypatch.setattr(prompt_agent, "load_settings", lambda: {"AZURE_AI_PROJECT_ENDPOINT": "https://example.test"})
    constructor = Mock()
    monkeypatch.setattr(prompt_agent, "FoundryAgent", constructor)
    prompt_agent.connect_prompt_agent(name="router-test")
    project.agents.get.assert_called_once_with(agent_name="router-test")
    assert constructor.call_args.kwargs["agent_version"] == "1"
    project.agents.create_version.assert_not_called()
