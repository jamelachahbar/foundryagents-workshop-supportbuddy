"""DevUI compatibility: preserve remote tools and render final text only once."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from agent_framework import AgentContext, AgentResponse, AgentResponseUpdate, Content, ResponseStream
from agent_framework_devui import DevServer
from agent_framework_devui._mapper import MessageMapper
from agent_framework_devui.models import AgentFrameworkRequest

from devui_branding import SupportBuddyDevServer, register_foundry_renderers
from lab_agent import HideFinalTextReplay


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _provider_updates() -> list[AgentResponseUpdate]:
    # Same shape the Responses client emits: deltas, then the full text with merge_mode="replace".
    return [
        AgentResponseUpdate(role="assistant", contents=[Content.from_text("Hi")]),
        AgentResponseUpdate(role="assistant", contents=[Content.from_text(" there")]),
        AgentResponseUpdate(role="assistant", contents=[Content.from_text("Hi there", merge_mode="replace")]),
    ]


async def _stream(updates: list[AgentResponseUpdate]):
    for update in updates:
        yield update


async def _hooks() -> list:
    context = AgentContext(agent=object(), messages=[], stream=True)

    async def call_next() -> None:
        return None

    await HideFinalTextReplay().process(context, call_next)
    return context.stream_update_transforms


@pytest.mark.anyio
async def test_streamed_text_equals_final_text():
    stream = ResponseStream(_stream(_provider_updates()), finalizer=AgentResponse.from_updates)
    for hook in await _hooks():
        stream.with_transform_hook(hook)

    streamed = "".join([update.text async for update in stream])
    final = await stream.get_final_response()

    assert streamed == "Hi there"
    assert final.text == "Hi there"


@pytest.mark.anyio
async def test_replace_text_without_prior_deltas_is_kept():
    (hook,) = await _hooks()
    update = AgentResponseUpdate(role="assistant", contents=[Content.from_text("Only text", merge_mode="replace")])
    assert hook(update) is None


@pytest.mark.anyio
@pytest.mark.parametrize("arguments", [{"customer_id": "CUST-1003"}, '{"customer_id":"CUST-1003"}'])
async def test_foundry_tools_render_as_correlated_calls_and_results_without_mutating_content(arguments):
    mapper = MessageMapper()
    register_foundry_renderers(mapper)
    request = AgentFrameworkRequest(input="Wi-Fi is slow", model="prompt-agent", stream=True)
    call = Content.from_mcp_server_tool_call(
        call_id="remote-1",
        tool_name="network___identify_customer",
        server_name="supportbuddy_tools",
        arguments=arguments,
    )
    payload = '{"found":true,"customer":{"customer_id":"CUST-1003"}}'
    result = Content.from_mcp_server_tool_result(
        call_id="remote-1", output=[Content.from_text(payload)]
    )
    events = []
    for content in [call, result, Content.from_text("Your line is healthy.")]:
        events.extend(await mapper.convert_event(
            AgentResponseUpdate(role="assistant", contents=[content]), request
        ))

    calls = [e.item for e in events if e.type == "response.output_item.added" and e.item.type == "function_call"]
    assert len(calls) == 1
    assert calls[0].call_id == "remote-1"
    assert calls[0].name == "network___identify_customer"
    deltas = [e for e in events if e.type == "response.function_call_arguments.delta"]
    assert len(deltas) == 1 and deltas[0].item_id == "remote-1"
    assert json.loads(deltas[0].delta) == {"customer_id": "CUST-1003"}
    results = [e for e in events if e.type == "response.function_result.complete"]
    assert len(results) == 1
    assert results[0].call_id == "remote-1"
    assert results[0].output == payload
    assert results[0].status == "completed"
    assert "".join(e.delta for e in events if e.type == "response.output_text.delta") == "Your line is healthy."
    assert not any(e.type == "error" for e in events)
    assert call.type == "mcp_server_tool_call" and call.arguments == arguments
    assert result.type == "mcp_server_tool_result" and result.output[0].text == payload


@pytest.mark.anyio
async def test_foundry_renderers_leave_local_tools_and_approvals_unchanged():
    mapper = MessageMapper()
    handlers = dict(mapper.content_mappers)
    register_foundry_renderers(mapper)
    assert all(mapper.content_mappers[name] == handler for name, handler in handlers.items())
    registered = dict(mapper.content_mappers)
    register_foundry_renderers(mapper)
    assert mapper.content_mappers == registered

    request = AgentFrameworkRequest(input="Restart", model="prompt-agent", stream=True)
    approval = Content.from_function_approval_request(
        id="approval-1",
        function_call=Content.from_function_call(
            call_id="local-1", name="restart_modem", arguments={"customer_id": "CUST-1002"}
        ),
    )
    events = await mapper.convert_event(
        AgentResponseUpdate(role="assistant", contents=[approval]), request
    )
    assert any(e.get("type") == "response.function_approval.requested" for e in events if isinstance(e, dict))


@pytest.mark.anyio
async def test_server_installs_renderers_without_replacing_mapper_state(monkeypatch):
    mapper = MessageMapper()
    executor = SimpleNamespace(message_mapper=mapper)

    async def ensure_executor(self):
        return executor

    monkeypatch.setattr(DevServer, "_ensure_executor", ensure_executor)
    server = SupportBuddyDevServer(auth_token="offline-test-token")
    assert await server._ensure_executor() is executor
    assert executor.message_mapper is mapper
    assert "mcp_server_tool_call" in mapper.content_mappers
    assert "mcp_server_tool_result" in mapper.content_mappers


@pytest.mark.parametrize("devui", [False, True])
def test_prompt_agent_replay_workaround_is_only_enabled_for_devui(monkeypatch, devui):
    import prompt_agent

    project = Mock()
    project.agents.get.return_value.versions.latest = SimpleNamespace(
        name="lab-test-prompt", version="5", definition={"tools": []}
    )
    client = Mock()
    client.return_value.__enter__ = Mock(return_value=project)
    client.return_value.__exit__ = Mock(return_value=False)
    factory = Mock()
    monkeypatch.setattr(prompt_agent, "load_settings", lambda: {"AZURE_AI_PROJECT_ENDPOINT": "https://example.test"})
    monkeypatch.setattr(prompt_agent, "prompt_agent_name", lambda: "lab-test-prompt")
    monkeypatch.setattr(prompt_agent, "AIProjectClient", client)
    monkeypatch.setattr(prompt_agent, "FoundryAgent", factory)
    prompt_agent.connect_prompt_agent(devui=devui)
    middleware = factory.call_args.kwargs["middleware"]
    assert (len(middleware) == 1 and isinstance(middleware[0], HideFinalTextReplay)) if devui else middleware is None
