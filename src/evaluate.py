"""Evaluate SupportBuddy on a fixed set of scenarios (Stage 12).

Run from the src folder (the agent module defaults to your own main.py):
    uv run python evaluate.py                                   # live agent run; local rule-based scoring
    uv run python evaluate.py solutions.stage_9_toolbox         # evaluate a reference stage
    uv run python evaluate.py --repeat 3                        # run every case 3 times: is it consistent?
    uv run python evaluate.py --foundry                         # add Foundry quality evaluators
    uv run python evaluate.py --foundry --foundry-safety        # also score harmful content
    uv run python evaluate.py --prompt-agent                    # evaluate your Foundry prompt agent (Stage 10)

Two kinds of evaluators, using the Agent Framework evaluation protocol:
  - LocalEvaluator with @evaluator checks written in Python. They test what you can state as a rule:
    which tools were called, whether the agent handed over, which runbook it cited, what it must never say.
  - FoundryEvals runs Foundry's built-in, model-judged evaluators (task adherence, tool call accuracy,
    relevance, intent resolution) on quality cases in your Foundry project. Additional dataset
    evaluations score groundedness against recorded tool results and completeness against reference
    answers. FoundryEvals with --foundry-safety separately scores hate/unfairness, sexual content, violence and self-harm
    on unblocked responses from both categories. Results also appear in the portal under Evaluations.

The dataset is src/evals/dataset.jsonl: "quality" cases (the scenarios) and "safety" cases (attacks).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent_framework import AgentResponse, Content, ConversationSplit, LocalEvaluator, Message, evaluate_agent, evaluator
from agent_framework.foundry import FoundryChatClient, FoundryEvals
from agent_framework_openai import OpenAIContentFilterException
from azure.identity import AzureCliCredential
from azure.identity.aio import AzureCliCredential as AsyncAzureCliCredential
from azure.ai.projects.aio import AIProjectClient
from openai import AsyncOpenAI
from openai.types.eval_create_response import EvalCreateResponse

from contact_centre.centre import tool_name
from lab_agent import load_settings, open_agent, stage_agent, stage_signs_in

DATASET = Path(__file__).parent / "evals" / "dataset.jsonl"
HANDOVER_TOOL = "escalate_to_human"


def quality_conversation_split(
    instructions_by_query: dict[str, str],
) -> Callable[[list[Message]], tuple[list[Message], list[Message]]]:
    def split(conversation: list[Message]) -> tuple[list[Message], list[Message]]:
        query_messages, response_messages = ConversationSplit.FULL(conversation)
        query = " ".join(message.text for message in query_messages if message.role == "user")
        instructions = instructions_by_query[query]
        return [Message("system", [instructions]), *query_messages], response_messages

    return split


def grounding_context(case: dict[str, Any], response: AgentResponse) -> str:
    sources = [case["context"]] if case.get("context") else []
    for message in response.messages:
        for content in message.contents:
            if content.type == "function_result":
                result = content.result
            elif content.type == "mcp_server_tool_result":
                result = content.output
            else:
                continue
            if result is not None:
                if isinstance(result, list) and all(isinstance(part, Content) for part in result):
                    result = [part.to_dict() for part in result]
                sources.append(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False))
    return "\n\n".join(sources)


async def score_evidence(
    settings: dict[str, str], records: list[tuple[dict[str, Any], AgentResponse]], eval_name: str
) -> None:
    rows = [
        {"id": case["id"], "query": case["query"], "response": response.text,
         "ground_truth": case["ground_truth"], "context": grounding_context(case, response)}
        for case, response in records
    ]
    async with AsyncAzureCliCredential() as credential:
        async with AIProjectClient(endpoint=settings["AZURE_AI_PROJECT_ENDPOINT"], credential=credential) as project:
            async with project.get_openai_client() as client:
                await score_dataset(client, settings, rows, "response_completeness", "ground_truth", eval_name)
                grounded = [row for row in rows if row["context"].strip()]
                for row in rows:
                    if not row["context"].strip():
                        print(f"Groundedness NOT EVALUATED: {row['id']} has no recorded tool results or dataset context.")
                if grounded:
                    await score_dataset(client, settings, grounded, "groundedness", "context", eval_name)


async def score_dataset(
    client: AsyncOpenAI, settings: dict[str, str], rows: list[dict[str, str]],
    metric: str, source_field: str, eval_name: str,
) -> None:
    # The pinned FoundryEvals SDK omits completeness's ground_truth mapping.
    # Use the public REST client so both evidence evaluators receive their required inputs.
    evaluation = await client.post(
        "/evals", cast_to=EvalCreateResponse,
        body={
            "name": f"{eval_name}-{metric}",
            "data_source_config": {
                "type": "custom", "include_sample_schema": True,
                "item_schema": {
                    "type": "object", "properties": {key: {"type": "string"} for key in rows[0]},
                    "required": ["query", "response", source_field],
                },
            },
            "testing_criteria": [{
                "type": "azure_ai_evaluator", "name": metric, "evaluator_name": f"builtin.{metric}",
                "initialization_parameters": {"deployment_name": settings["AZURE_AI_MODEL_DEPLOYMENT_NAME"]},
                "data_mapping": {
                    "query": "{{item.query}}", "response": "{{item.response}}",
                    source_field: "{{item." + source_field + "}}",
                },
            }],
        },
    )
    run = await client.evals.runs.create(
        eval_id=evaluation.id, name=f"{metric} Run",
        data_source={"type": "jsonl", "source": {
            "type": "file_content", "content": [{"item": row} for row in rows],
        }},
    )
    print(f"Foundry {metric}: submitted {len(rows)} items; eval={evaluation.id}, run={run.id}")
    deadline = asyncio.get_running_loop().time() + 180
    while True:
        result = await client.evals.runs.retrieve(run.id, eval_id=evaluation.id)
        if result.report_url and result.status in {"completed", "failed", "canceled"}:
            print(f"Report: {result.report_url}")
        if result.status == "completed":
            counts = result.result_counts
            if counts.errored or counts.total != len(rows):
                raise RuntimeError(f"Foundry {metric}: incomplete scoring: {counts}")
            print(f"Foundry {metric}: {counts.passed}/{counts.total} items passed; {counts.failed} failed")
            return
        if result.status in {"failed", "canceled"}:
            raise RuntimeError(f"Foundry {metric}: {result.status}: {result.error}")
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError(f"Foundry {metric} timed out; inspect eval={evaluation.id}, run={run.id}")
        await asyncio.sleep(5)


def load_cases(path: Path = DATASET) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def tools_called(conversation: list[Message]) -> list[str]:
    """Tool names the agent called, without triple-underscore or dotted toolbox prefixes.

    Function calls are run by your code; MCP calls of a prompt agent are run by Foundry. Both count.
    """
    names = []
    for message in conversation:
        for content in message.contents:
            if content.type == "function_call":
                names.append(content.name)
            elif content.type == "mcp_server_tool_call":
                names.append(content.tool_name)
    return [tool_name(name) for name in names]


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


def agent_factory(module_name: str, prompt_agent: bool) -> Callable[[dict[str, Any]], Any]:
    """How to build the agent under test for one case. Stages with a signed-in customer get the case's customer."""
    if prompt_agent:
        from prompt_agent import connect_prompt_agent

        return lambda case: connect_prompt_agent()
    if stage_signs_in(module_name):
        return lambda case: stage_agent(module_name, case["customer"])
    return lambda case: stage_agent(module_name)


async def run_case(
    make_agent: Callable[[dict[str, Any]], Any], case: dict[str, Any], repeat: int
) -> tuple[Any, list[AgentResponse], Any, int]:
    agent = make_agent(case)
    responses = []
    blocked_runs = 0
    async with open_agent(agent):
        for _ in range(repeat):
            try:
                responses.append(await _run_rejecting_approvals(agent, case))
            except OpenAIContentFilterException as blocked:
                blocked_runs += 1
                print(f"         ({case['id']}: blocked by a guardrail)")
                responses.append(AgentResponse(
                    messages=[Message("assistant", [f"[blocked by guardrail] {blocked}"])],
                    additional_properties={"guardrail_blocked": True},
                ))
    result = (
        await evaluate_agent(
            agent=agent,
            queries=[case["query"]] * repeat,
            responses=responses,
            evaluators=LocalEvaluator(*checks_for(case)),
            eval_name=case["id"],
        )
    )[0]
    return agent, responses, result, blocked_runs


async def main_async(
    module_name: str, repeat: int, use_foundry: bool, only: list[str] | None = None, prompt_agent: bool = False,
    use_foundry_safety: bool = False,
) -> int:
    if repeat < 1:
        raise ValueError("repeat must be at least 1.")
    available_cases = load_cases()
    if only:
        unknown = sorted(set(only) - {case["id"] for case in available_cases})
        if unknown:
            raise ValueError(f"Unknown case ID(s): {', '.join(unknown)}. Use IDs from evals/dataset.jsonl.")
    cases = [case for case in available_cases if not only or case["id"] in only]
    if not cases:
        raise ValueError("No evaluation cases selected. Add cases to evals/dataset.jsonl.")
    for case in cases:
        if case.get("category") not in {"quality", "safety"}:
            raise ValueError(f"Case {case['id']!r} needs category 'quality' or 'safety'.")
        if use_foundry and case["category"] == "quality":
            reference = case.get("ground_truth")
            if not isinstance(reference, str) or not reference.strip():
                raise ValueError(f"Case {case['id']!r} needs non-empty ground_truth for response completeness.")
    settings = load_settings()
    make_agent = agent_factory(module_name, prompt_agent)
    label = "prompt_agent" if prompt_agent else module_name
    quality_queries: list[str] = []
    quality_responses: list[AgentResponse] = []
    quality_agent: Any = None
    quality_case_count = 0
    quality_instructions: dict[str, str] = {}
    safety_summaries: list[str] = []
    quality_records: list[tuple[dict[str, Any], AgentResponse]] = []
    safety_queries: list[str] = []
    safety_responses: list[AgentResponse] = []
    safety_agent: Any = None
    failures = 0

    print(f"Evaluating {label} on {len(cases)} cases x {repeat} run(s)\n")
    for case in cases:
        agent, responses, result, blocked_runs = await run_case(make_agent, case, repeat)
        if use_foundry_safety:
            safety_agent = agent
            for response in responses:
                if not response.additional_properties.get("guardrail_blocked"):
                    safety_queries.append(case["query"])
                    safety_responses.append(response)
        if case["category"] == "quality":
            if use_foundry:
                instructions = agent.additional_properties.get("evaluation_instructions") or agent.default_options.get(
                    "instructions"
                )
                if not isinstance(instructions, str) or not instructions.strip():
                    raise ValueError(f"Case {case['id']!r}: actual agent instructions are required for quality judging.")
                quality_instructions[case["query"]] = instructions
            quality_agent = agent
            quality_case_count += 1
            quality_queries += [case["query"]] * repeat
            quality_responses += responses
            quality_records += [(case, response) for response in responses]
        else:
            forbidden_scores = [
                score for item in result.items for score in item.scores if score.name == "no_forbidden_content"
            ]
            forbidden_summary = (
                f"{sum(score.passed for score in forbidden_scores)}/{len(forbidden_scores)} forbidden-content checks passed"
                if forbidden_scores else "no forbidden-content check configured"
            )
            safety_summaries.append(
                f"  {case['id']}: {result.passed}/{result.total} runs passed; "
                f"{blocked_runs}/{len(responses)} blocked; {forbidden_summary}"
            )
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
    if safety_summaries:
        print(f"\nSafety cases (local checks only): {len(safety_summaries)} case(s)")
        print("\n".join(safety_summaries))

    if use_foundry and not quality_queries:
        print("\nFoundry quality evaluation skipped: no quality cases selected.")
    if use_foundry and quality_queries:
        print(
            f"\nRunning Foundry evaluators (model-judged) on {quality_case_count} quality case(s) "
            f"x {repeat} run(s) = {len(quality_responses)} items. Safety cases are excluded."
        )
        judge = FoundryChatClient(
            project_endpoint=settings["AZURE_AI_PROJECT_ENDPOINT"],
            model=settings["AZURE_AI_MODEL_DEPLOYMENT_NAME"],
            credential=AzureCliCredential(),
        )
        foundry = FoundryEvals(
            client=judge,
            timeout=600,
            evaluators=[FoundryEvals.TASK_ADHERENCE, FoundryEvals.TOOL_CALL_ACCURACY, FoundryEvals.RELEVANCE,
                        FoundryEvals.INTENT_RESOLUTION],
        )
        for result in await evaluate_agent(
            agent=quality_agent,
            queries=quality_queries,
            responses=quality_responses,
            evaluators=foundry,
            conversation_split=quality_conversation_split(quality_instructions),
            eval_name=f"{settings['WORKSHOP_RESOURCE_PREFIX']}-supportbuddy-{label.split('.')[-1]}",
        ):
            print(f"Foundry: {result.status}, {result.passed}/{result.total} items passed")
            if result.status != "completed":
                raise RuntimeError(
                    f"Foundry quality evaluation {result.status}: eval={result.eval_id}, run={result.run_id}. "
                    "Inspect the existing run before retrying; this is not a quality pass."
                )
            for name, counts in (result.per_evaluator or {}).items():
                print(f"  {name}: {counts}")
            if result.report_url:
                print(f"Report: {result.report_url}")
        await score_evidence(settings, quality_records, f"{settings['WORKSHOP_RESOURCE_PREFIX']}-supportbuddy-{label}")
    if use_foundry_safety:
        print(f"\nFoundry safety: {len(safety_responses)} unblocked responses from both categories; "
              f"{len(cases) * repeat - len(safety_responses)} content-filter blocks excluded.")
        print("Coverage: hate/unfairness, sexual content, violence, self-harm. "
              "Not a jailbreak, leakage, protected-material or code-vulnerability assessment.")
        if safety_responses:
            judge = FoundryChatClient(
                project_endpoint=settings["AZURE_AI_PROJECT_ENDPOINT"],
                model=settings["AZURE_AI_MODEL_DEPLOYMENT_NAME"], credential=AzureCliCredential(),
            )
            foundry = FoundryEvals(client=judge, timeout=600, evaluators=[
                FoundryEvals.HATE_UNFAIRNESS, FoundryEvals.SEXUAL, FoundryEvals.VIOLENCE, FoundryEvals.SELF_HARM,
            ])
            for result in await evaluate_agent(
                agent=safety_agent, queries=safety_queries, responses=safety_responses, evaluators=foundry,
                eval_name=f"{settings['WORKSHOP_RESOURCE_PREFIX']}-supportbuddy-{label}-safety",
            ):
                print(f"Foundry safety: {result.status}, {result.passed}/{result.total} items passed")
                if result.status != "completed":
                    raise RuntimeError(
                        f"Foundry safety evaluation {result.status}: eval={result.eval_id}, run={result.run_id}. "
                        "Inspect the existing run before retrying; this is not a safety pass."
                    )
                for name, counts in (result.per_evaluator or {}).items():
                    print(f"  {name}: {counts}")
                if result.report_url:
                    print(f"Report: {result.report_url}")
        else:
            print("Foundry safety NOT EVALUATED: no unblocked responses selected.")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the SupportBuddy agent.")
    parser.add_argument("module", nargs="?", default="main", help="Agent module, e.g. main or solutions.stage_9_toolbox.")
    parser.add_argument("--repeat", type=int, default=1, help="Runs per case, to measure consistency.")
    parser.add_argument("--foundry", action="store_true", help="Also run Foundry's AI-assisted evaluators on quality cases.")
    parser.add_argument("--foundry-safety", action="store_true", help="Also score harmful content on all unblocked responses.")
    parser.add_argument("--case", action="append", help="Only run this case ID (repeatable).")
    parser.add_argument("--prompt-agent", action="store_true", help="Evaluate your Foundry prompt agent (Stage 10).")
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be at least 1")
    sys.stdout.reconfigure(encoding="utf-8")
    return asyncio.run(main_async(args.module, args.repeat, args.foundry, args.case, args.prompt_agent, args.foundry_safety))


if __name__ == "__main__":
    sys.exit(main())
