"""Stage 12 checks and Stage 13 code guardrail, without a model (no Azure needed)."""

import pytest
from agent_framework import Content, Message

import evaluate
from solutions.stage_13_guardrails import SignedInCustomerOnly


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _Context:
    def __init__(self, arguments):
        self.arguments = arguments
        self.result = None


async def _run(middleware, arguments):
    called = []

    async def call_next():
        called.append(True)

    context = _Context(arguments)
    await middleware.process(context, call_next)
    return bool(called), context.result


@pytest.mark.anyio
async def test_guardrail_lets_tool_calls_for_the_signed_in_customer_through():
    called, result = await _run(SignedInCustomerOnly("CUST-1006"), {"customer_id": "cust-1006"})
    assert called and result is None


@pytest.mark.anyio
async def test_guardrail_blocks_tool_calls_about_another_customer():
    called, result = await _run(SignedInCustomerOnly("CUST-1006"), {"customer_id": "CUST-1001"})
    assert not called
    assert result["blocked"] is True


@pytest.mark.anyio
async def test_guardrail_ignores_tools_without_a_customer_id():
    called, _ = await _run(SignedInCustomerOnly("CUST-1006"), {"line_id": "LINE-2006"})
    assert called


async def _run_tool(middleware, arguments, returns):
    """Like _run, but the tool returns a result, parsed the way Agent Framework parses function results."""
    from agent_framework import FunctionTool

    called = []

    async def call_next():
        called.append(True)
        context.result = FunctionTool.parse_result(returns)

    context = _Context(arguments)
    await middleware.process(context, call_next)
    return bool(called), context.result


@pytest.mark.anyio
async def test_assist_guard_never_runs_a_tool_for_another_customer():
    from contact_centre.assist import CaseCustomerOnly

    called, result = await _run_tool(CaseCustomerOnly("CUST-1002"), {"customer_id": "CUST-1001"}, {"restarted": True})
    assert not called and result["blocked"] is True


@pytest.mark.anyio
async def test_assist_guard_withholds_results_about_another_customer():
    from contact_centre.assist import BLOCKED, CaseCustomerOnly

    guard = CaseCustomerOnly("CUST-1002")
    other = {"found": True, "customer": {"customer_id": "CUST-1001"}}
    called, result = await _run_tool(guard, {"email": "someone@example.com"}, other)
    assert called and result["blocked"] is True
    _, result = await _run_tool(guard, {"line_id": "LINE-2001"}, {"found": True, "line": {"customer_id": "CUST-1001"}})
    assert result["blocked"] is True

    mine = {"found": True, "line": {"line_id": "LINE-2002", "customer_id": "CUST-1002"}}
    _, result = await _run_tool(guard, {"line_id": "LINE-2002"}, mine)
    assert result is not BLOCKED and "LINE-2002" in result[0].text


def _conversation(*tool_names):
    calls = [Content.from_function_call(call_id=str(i), name=name, arguments="{}") for i, name in enumerate(tool_names)]
    return [Message("user", ["help"]), Message("assistant", calls)]


def test_tool_names_are_compared_without_the_toolbox_prefix():
    conversation = _conversation("network___identify_customer", "escalate_to_human")
    assert evaluate.tools_called(conversation) == ["identify_customer", "escalate_to_human"]


def test_tools_run_by_foundry_count_as_called():
    # A prompt agent's toolbox calls are run by Foundry and come back as MCP server tool calls.
    run_by_foundry = Content.from_mcp_server_tool_call(call_id="m1", tool_name="network___check_equipment")
    conversation = [Message("user", ["help"]), Message("assistant", [run_by_foundry])]
    assert evaluate.tools_called(conversation) == ["check_equipment"]


def test_dataset_cases_all_have_an_id_query_and_customer():
    cases = evaluate.load_cases()
    assert len({c["id"] for c in cases}) == len(cases)
    for case in cases:
        assert case["query"] and case["customer"].startswith("CUST-")
        assert evaluate.checks_for(case), case["id"]


def test_each_dataset_customer_and_runbook_exists():
    from knowledge_base import load_runbooks
    from lab_backend import backend

    runbooks = {r["id"] for r in load_runbooks()}
    for case in evaluate.load_cases():
        assert backend.identify_customer(customer_id=case["customer"])["found"], case["id"]
        assert set(case.get("expected_runbooks", [])) <= runbooks, case["id"]


def test_every_kind_of_expectation_adds_a_check():
    case = {
        "expected_tools": ["identify_customer"],
        "expected_decision": "handover",
        "expected_runbooks": ["RB-07"],
        "must_not_contain": ["+12025550101"],
    }
    assert len(evaluate.checks_for(case)) == 4
    assert evaluate.checks_for({}) == []
