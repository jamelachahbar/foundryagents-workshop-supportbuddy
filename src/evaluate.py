"""Evaluate SupportBuddy on a fixed set of scenarios (Stage 11).

Run from the src folder (the agent module defaults to your own main.py):
    uv run python evaluate.py                                   # local checks: fast, deterministic, free
    uv run python evaluate.py solutions.stage_9_toolbox         # evaluate a reference stage
    uv run python evaluate.py --repeat 3                        # run every case 3 times: is it consistent?
    uv run python evaluate.py --foundry                         # add Foundry's AI-assisted evaluators

Two kinds of evaluators, both from Agent Framework:
  - LocalEvaluator with @evaluator checks written in Python. They test what you can state as a rule:
    which tools were called, whether the agent handed over, which runbook it cited, what it must never say.
  - FoundryEvals runs Foundry's built-in, model-judged evaluators (task adherence, tool call accuracy,
    relevance, coherence) in your Foundry project. The results also appear in the portal under Evaluations.

The dataset is src/evals/dataset.jsonl: "quality" cases (the scenarios) and "safety" cases (attacks).
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sys
from pathlib import Path
from typing import Any

from agent_framework import AgentResponse, LocalEvaluator, Message, evaluate_agent, evaluator
from agent_framework.foundry import FoundryChatClient, FoundryEvals
from agent_framework_openai import OpenAIContentFilterException
from azure.identity import AzureCliCredential

from lab_agent import build_agent, load_settings, open_agent

DATASET = Path(__file__).parent / "evals" / "dataset.jsonl"
HANDOVER_TOOL = "escalate_to_human"


def load_cases(path: Path = DATASET) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def tools_called(conversation: list[Message]) -> list[str]:
    """Tool names the agent called, without the toolbox prefix ("network___check_equipment" -> "check_equipment")."""
    return [
        content.name.split("___")[-1]
        for message in conversation
        for content in message.contents
        if content.type == "function_call"
    ]


def checks_for(case: dict[str, Any]) -> list[Any]:
    """The rule-based checks that apply to one case."""
    checks = []

    if expected := case.get("expected_tools"):

        @evaluator(name="collected_evidence")
        def collected_evidence(conversation: list[Message]) -> dict[str, Any]:
            missing = sorted(set(expected) - set(tools_called(conversation)))
            return {"passed": not missing, "reason": f"missing: {missing}" if missing else "all expected tools called"}

        checks.append(collected_evidence)

    if decision := case.get("expected_decision"):

        @evaluator(name="right_decision")
        def right_decision(conversation: list[Message]) -> dict[str, Any]:
            actual = "handover" if HANDOVER_TOOL in tools_called(conversation) else "answer"
            return {"passed": actual == decision, "reason": f"expected {decision}, got {actual}"}

        checks.append(right_decision)

    if runbooks := case.get("expected_runbooks"):

        @evaluator(name="cites_runbook")
        def cites_runbook(response: str) -> dict[str, Any]:
            cited = [rb for rb in runbooks if rb in response]
            return {"passed": bool(cited), "reason": f"cited {cited}" if cited else f"none of {runbooks} cited"}

        checks.append(cites_runbook)

    if forbidden := case.get("must_not_contain"):

        @evaluator(name="no_forbidden_content")
        def no_forbidden_content(response: str) -> dict[str, Any]:
            found = [text for text in forbidden if text.lower() in response.lower()]
            return {"passed": not found, "reason": f"found {found}" if found else "nothing forbidden"}

        checks.append(no_forbidden_content)

    return checks


async def _run_rejecting_approvals(agent: Any, case: dict[str, Any]) -> AgentResponse:
    """Run one case. Nobody is there to approve actions, so every approval request is rejected."""
    session = agent.create_session()
    response = await agent.run(case["query"], session=session)
    messages = list(response.messages)
    while response.user_input_requests:
        rejections = [request.to_function_approval_response(False) for request in response.user_input_requests]
        response = await agent.run(Message("user", rejections), session=session)
        messages += response.messages
    return AgentResponse(messages=messages)


def agent_for(module: Any, case: dict[str, Any]) -> Any:
    """Build the agent under test. Stages with a signed-in customer get the case's customer."""
    if not hasattr(module, "context_providers"):
        return build_agent(module.__file__, module.tools())
    customer = case["customer"]
    middleware = module.middleware(customer) if hasattr(module, "middleware") else ()
    return build_agent(module.__file__, module.tools(), module.context_providers(customer), middleware=middleware)


async def run_case(module: Any, case: dict[str, Any], repeat: int) -> tuple[Any, list[AgentResponse], Any]:
    agent = agent_for(module, case)
    responses = []
    async with open_agent(agent):
        for _ in range(repeat):
            try:
                responses.append(await _run_rejecting_approvals(agent, case))
            except OpenAIContentFilterException as blocked:
                # A guardrail (Stage 12) blocked the request before the model answered. Record it as the reply.
                print(f"         ({case['id']}: blocked by a guardrail)")
                responses.append(AgentResponse(messages=[Message("assistant", [f"[blocked by guardrail] {blocked}"])]))
    result = (
        await evaluate_agent(
            agent=agent,
            queries=[case["query"]] * repeat,
            responses=responses,
            evaluators=LocalEvaluator(*checks_for(case)),
            eval_name=case["id"],
        )
    )[0]
    return agent, responses, result


async def main_async(module_name: str, repeat: int, use_foundry: bool, only: list[str] | None = None) -> int:
    settings = load_settings()
    module = importlib.import_module(module_name)
    cases = [c for c in load_cases() if not only or c["id"] in only]
    all_queries, all_responses, last_agent, failures = [], [], None, 0

    print(f"Evaluating {module_name} on {len(cases)} cases x {repeat} run(s)\n")
    for case in cases:
        last_agent, responses, result = await run_case(module, case, repeat)
        all_queries += [case["query"]] * repeat
        all_responses += responses
        status = "PASS" if result.all_passed else "FAIL"
        failures += 0 if result.all_passed else 1
        print(f"[{status}] {case['id']:<22} {result.passed}/{result.total} runs passed")
        for item, response in zip(result.items, responses, strict=False):
            failed = [score for score in item.scores if not score.passed]
            for score in failed:
                reason = (score.sample or {}).get("reason") or "failed"
                print(f"         - {score.name}: {reason}")
            if failed:
                print(f"           tools: {tools_called(response.messages)}")
                print(f"           reply: {response.text[:240].replace(chr(10), ' ')}")

    print(f"\n{len(cases) - failures}/{len(cases)} cases passed every run.")

    if use_foundry:
        print("\nRunning Foundry evaluators (model-judged). This takes a few minutes...")
        judge = FoundryChatClient(
            project_endpoint=settings["AZURE_AI_PROJECT_ENDPOINT"],
            model=settings["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
            credential=AzureCliCredential(),
        )
        foundry = FoundryEvals(
            client=judge,
            evaluators=[FoundryEvals.TASK_ADHERENCE, FoundryEvals.TOOL_CALL_ACCURACY, FoundryEvals.RELEVANCE],
        )
        for result in await evaluate_agent(
            agent=last_agent,
            queries=all_queries,
            responses=all_responses,
            evaluators=foundry,
            eval_name=f"{settings['WORKSHOP_RESOURCE_PREFIX']}-supportbuddy-{module_name.split('.')[-1]}",
        ):
            print(f"Foundry: {result.status}, {result.passed}/{result.total} items passed")
            for name, counts in (result.per_evaluator or {}).items():
                print(f"  {name}: {counts}")
            if result.report_url:
                print(f"Report: {result.report_url}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the SupportBuddy agent.")
    parser.add_argument("module", nargs="?", default="main", help="Agent module, e.g. main or solutions.stage_9_toolbox.")
    parser.add_argument("--repeat", type=int, default=1, help="Runs per case, to measure consistency.")
    parser.add_argument("--foundry", action="store_true", help="Also run Foundry's AI-assisted evaluators.")
    parser.add_argument("--case", action="append", help="Only run this case ID (repeatable).")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    return asyncio.run(main_async(args.module, args.repeat, args.foundry, args.case))


if __name__ == "__main__":
    sys.exit(main())
