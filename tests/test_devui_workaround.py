"""DevUI workaround: the replace-mode final text must not be streamed twice, and must not be lost."""

import pytest
from agent_framework import AgentContext, AgentResponse, AgentResponseUpdate, Content, ResponseStream

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
