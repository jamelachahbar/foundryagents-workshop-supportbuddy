"""Opt-in Azure Monitor tracing for the synthetic workshop."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from agent_framework.observability import configure_otel_providers
from azure.ai.projects import AIProjectClient
from azure.identity import AzureCliCredential
from azure.monitor.opentelemetry.exporter import AzureMonitorLogExporter, AzureMonitorTraceExporter
from opentelemetry import trace
from opentelemetry.trace import Span, StatusCode

logger = logging.getLogger(__name__)
_configured = False
_capture_content = False
tracer = trace.get_tracer("supportbuddy.contact_centre")


def _flag(name: str) -> bool:
    value = os.getenv(name, "false").strip().lower()
    if value not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false.")
    return value == "true"


def configure_tracing(project: AIProjectClient, *, service_name: str = "supportbuddy-prompt-client") -> None:
    """Resolve the project's linked resource without storing a connection string locally."""
    global _configured, _capture_content
    if not _flag("SUPPORTBUDDY_TRACING") or _configured:
        return
    capture = _flag("SUPPORTBUDDY_TRACE_CONTENT")
    connection_string = project.telemetry.get_application_insights_connection_string()
    credential = AzureCliCredential()
    configure_otel_providers(
        service_name=service_name,
        resource_attributes={"deployment.environment.name": "workshop"},
        exporters=[
            AzureMonitorTraceExporter(
                connection_string=connection_string, credential=credential, disable_offline_storage=True,
            ),
            AzureMonitorLogExporter(
                connection_string=connection_string, credential=credential, disable_offline_storage=True,
            ),
        ],
        enable_sensitive_data=capture,
        enable_message_events=capture,
        enable_console_exporters=False,
    )
    _capture_content = capture
    _configured = True
    logger.warning("SupportBuddy tracing enabled (%s); synthetic message/tool content capture: %s.", service_name, capture)


def configure_local_tracing() -> None:
    """Export local code-first runs to the project's existing monitoring connection."""
    if not _flag("SUPPORTBUDDY_TRACING") or _configured:
        return
    from lab_agent import load_settings

    settings = load_settings()
    with AzureCliCredential() as credential, AIProjectClient(
        endpoint=settings["AZURE_AI_PROJECT_ENDPOINT"], credential=credential,
    ) as project:
        configure_tracing(project, service_name="supportbuddy-local")


def record_event(span: Span, event: dict[str, Any]) -> None:
    """Keep protocol metadata; never export snapshots containing opaque reasoning or tokens."""
    kind = event.get("type")
    if kind in {"TOOL_CALL_START", "TOOL_CALL_END", "TOOL_CALL_ARGS", "TOOL_CALL_RESULT"}:
        attributes = {
            "tool.call.id": str(event.get("toolCallId", "")),
            "tool.name": str(event.get("toolCallName", "")),
        }
        if _capture_content:
            for key in ("delta", "content"):
                if key in event:
                    attributes[f"tool.{key}"] = json.dumps(event[key], ensure_ascii=False)
        span.add_event(str(kind), attributes)
    elif kind == "RUN_FINISHED":
        for interrupt in (event.get("outcome") or {}).get("interrupts") or []:
            call = ((interrupt.get("metadata") or {}).get("agent_framework") or {}).get("function_call") or {}
            attributes = {"approval.interrupt.id": str(interrupt["id"]), "tool.name": str(call.get("name", ""))}
            if _capture_content:
                attributes["tool.arguments"] = json.dumps(call.get("arguments") or {})
            span.add_event("approval.requested", attributes)
    elif kind == "RUN_ERROR":
        span.set_status(StatusCode.ERROR)
        span.add_event("agent.error")


def record_approval(conversation_id: str, approval: dict[str, Any], approved: bool) -> None:
    with tracer.start_as_current_span(
        "supportbuddy.approval",
        attributes={
            "gen_ai.conversation.id": conversation_id,
            "supportbuddy.approval.batch": approval["approval_batch"],
            "approval.interrupt.id": approval["interrupt_id"],
            "approval.approved": approved,
            "tool.name": approval["tool"],
        },
    ):
        pass


def record_input(span: Span, body: dict[str, Any]) -> None:
    if _capture_content:
        # Capture messages, not the full provider history (which can contain encrypted reasoning).
        for message in reversed(body.get("messages", [])):
            if message.get("role") == "user":
                span.add_event("customer.message", {"supportbuddy.message.content": str(message.get("content", ""))})
                break
    for decision in body.get("resume", []):
        span.add_event("approval.resume", {
            "approval.interrupt.id": decision["interruptId"],
            "approval.approved": decision["payload"]["approved"],
        })
