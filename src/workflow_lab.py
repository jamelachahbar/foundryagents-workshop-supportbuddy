"""Optional SupportBuddy workflow: model judgement inside explicit code boundaries."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib
import json
import logging
import os
import re
import sys
import time
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from agent_framework import (
    Agent,
    AgentExecutor,
    AgentExecutorRequest,
    AgentExecutorResponse,
    Executor,
    InMemoryCheckpointStorage,
    Message,
    Workflow,
    WorkflowBuilder,
    WorkflowContext,
    handler,
    response_handler,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from opentelemetry import trace

from devui_branding import serve_branded
from lab_agent import DEVUI_TOKEN_FILE, _devui_token, build_agent, knowledge_settings, load_instructions, load_settings, open_agent
from lab_backend import backend
from lab_telemetry import configure_local_tracing
from runtime_settings import app_url
from tools.knowledge_tools import runbook_knowledge_base


class Intake(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    customer_id: str | None
    email: str | None
    phone: str | None
    symptom: str
    language: Literal["en", "fr"]
    in_scope: bool


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    decision: Literal["outage", "restart", "wifi_advice", "line_fault", "unknown"]
    runbook_id: str
    runbook_title: str
    reason: str


class Case(BaseModel):
    customer_id: str
    symptom: str
    language: Literal["en", "fr"]
    customer: dict[str, Any]
    evidence: dict[str, Any] = Field(default_factory=dict)
    verdict: Verdict | None = None
    runbooks: str = ""
    restart: dict[str, Any] | None = None


class RestartApproval(BaseModel):
    question: str
    customer_id: str
    equipment_id: str


class RestartDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    approved: bool

    @model_validator(mode="before")
    @classmethod
    def accept_boolean(cls, value: Any) -> Any:
        return {"approved": value} if isinstance(value, bool) else value


def verdict_allowed(verdict: Verdict, evidence: dict[str, Any]) -> bool:
    """Reject diagnoses that contradict the independently collected evidence."""
    line = evidence.get("line", {})
    modems = [item for item in evidence.get("equipment", []) if item.get("type") == "modem"]
    open_incident = any(item.get("status") == "open" for item in evidence.get("incidents", []))
    line_fault = line.get("resyncs_24h", 0) > 5 or line.get("errors_24h", 0) > 100
    if verdict.decision == "outage":
        return open_incident and verdict.runbook_id in ("RB-09", "RB-04")
    if verdict.decision == "restart":
        return (
            not open_incident and not line_fault and line.get("sync") == "up"
            and bool(modems) and modems[0].get("reachable") is False
            and verdict.runbook_id == "RB-02"
        )
    if verdict.decision == "line_fault":
        return not open_incident and line_fault and verdict.runbook_id == "RB-07"
    if verdict.decision == "wifi_advice":
        return (
            not open_incident and not line_fault and line.get("sync") == "up"
            and bool(modems) and modems[0].get("reachable") is True
            and bool(modems[0].get("wifi")) and verdict.runbook_id in ("RB-03", "RB-06", "RB-11")
        )
    return False


@dataclass
class Evidence:
    kind: str
    data: dict[str, Any]


class FreshAgent(Agent):
    """The workflow owns the case; an agent never carries chat history to another run."""

    def run(self, messages: Any = None, *, session: Any = None, **kwargs: Any) -> Any:
        if isinstance(messages, list):
            messages = [message for message in messages if getattr(message, "role", None) == "user"][-1:]
        return super().run(messages, session=self.create_session(), **kwargs)


class ModelMeter:
    def __init__(self, client: Any) -> None:
        self.client = client
        self.calls = 0

    def get_response(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        return self.client.get_response(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.client, name)


class Identify(Executor):
    @handler
    async def run(self, reply: AgentExecutorResponse, ctx: WorkflowContext[Case, str]) -> None:
        ctx.set_state("case", None)
        ctx.set_state("decision", "unknown")
        try:
            intake = Intake.model_validate_json(reply.agent_response.text)
        except ValidationError:
            await ctx.yield_output(scope_message("en"))
            return
        if not intake.in_scope:
            await ctx.yield_output(scope_message(intake.language))
            return
        if not any((intake.customer_id, intake.email, intake.phone)):
            await ctx.yield_output(identifier_question(intake.language))
            return
        found = await asyncio.to_thread(
            backend.identify_customer, customer_id=intake.customer_id, email=intake.email, phone=intake.phone,
        )
        if not found.get("found"):
            await ctx.yield_output(identifier_question(intake.language))
            return
        case = Case(
            customer_id=found["customer"]["customer_id"], customer=found["customer"],
            symptom=intake.symptom, language=intake.language,
        )
        ctx.set_state("case", case.model_dump())
        await ctx.send_message(case)


class Line(Executor):
    @handler
    async def run(self, case: Case, ctx: WorkflowContext[Evidence]) -> None:
        line_ids = case.customer.get("line_ids", [])
        result = await asyncio.to_thread(backend.get_line_status, line_ids[0]) if line_ids else {}
        await ctx.send_message(Evidence("line", result))


class Incidents(Executor):
    @handler
    async def run(self, case: Case, ctx: WorkflowContext[Evidence]) -> None:
        result = await asyncio.to_thread(backend.get_known_incidents, postcode=case.customer.get("postcode"))
        await ctx.send_message(Evidence("incidents", result))


class Equipment(Executor):
    @handler
    async def run(self, case: Case, ctx: WorkflowContext[Evidence]) -> None:
        result = await asyncio.to_thread(backend.check_equipment, case.customer_id)
        await ctx.send_message(Evidence("equipment", result))


class Collect(Executor):
    @handler
    async def run(self, results: list[Evidence], ctx: WorkflowContext[AgentExecutorRequest]) -> None:
        by_kind = {item.kind: item.data for item in results}
        case = Case.model_validate(ctx.get_state("case"))
        case.evidence = {
            "line": by_kind["line"].get("line", {}),
            "incidents": by_kind["incidents"].get("incidents", []),
            "equipment": by_kind["equipment"].get("equipment", []),
        }
        ctx.set_state("case", case.model_dump())
        await ctx.send_message(agent_request({"evidence": case.evidence}))


class DiagnoseExecutor(AgentExecutor):
    """Retrieve once in code before the single diagnosis model request."""

    def __init__(self, agent: Agent, knowledge: Any) -> None:
        super().__init__(agent, id="diagnose")
        self.knowledge = knowledge

    @handler
    async def run(
        self, request: AgentExecutorRequest, ctx: WorkflowContext[AgentExecutorResponse],
    ) -> None:
        case = Case.model_validate(ctx.get_state("case"))
        async with self.knowledge:
            result = await self.knowledge.call_tool(
                "knowledge_base_retrieve",
                query_variants=[
                    "open area incident outage restore time versus unreachable modem restart",
                    "line up Wi-Fi interference weak signal band placement",
                    "unstable line high resyncs errors human escalation",
                ],
            )
        case.runbooks = retrieval_text(result)
        ctx.set_state("case", case.model_dump())
        await super().run(agent_request({"evidence": case.evidence, "runbooks": case.runbooks}), ctx)


class Decide(Executor):
    def __init__(self, *, id: str) -> None:
        super().__init__(id=id)
        self.last_decision = "unknown"

    @handler
    async def run(
        self, reply: AgentExecutorResponse, ctx: WorkflowContext[AgentExecutorRequest, str],
    ) -> None:
        self.last_decision = "unknown"
        case = Case.model_validate(ctx.get_state("case"))
        try:
            verdict = Verdict.model_validate_json(reply.agent_response.text)
        except ValidationError:
            await self.fallback(case, ctx)
            return
        if not verdict_allowed(verdict, case.evidence) or verdict.runbook_id not in case.runbooks:
            await self.fallback(case, ctx)
            return
        case.verdict = verdict
        self.last_decision = verdict.decision
        ctx.set_state("case", case.model_dump())
        ctx.set_state("decision", verdict.decision)
        if verdict.decision == "line_fault":
            handover = await self.handover(case, verdict.reason)
            await ctx.yield_output(handover_message(case.language, handover["handover_id"], verdict))
        elif verdict.decision == "restart":
            modem = next(item for item in case.evidence["equipment"] if item.get("type") == "modem")
            question = (
                "Restart your modem now? Your connection will drop for about 3 minutes."
                if case.language == "en" else
                "Redemarrer votre modem maintenant ? La connexion sera coupee environ 3 minutes."
            )
            await ctx.request_info(
                RestartApproval(question=question, customer_id=case.customer_id, equipment_id=modem["equipment_id"]),
                RestartDecision | bool,
            )
        else:
            await ctx.send_message(answer_request(case))

    async def approved(
        self, request: RestartApproval, response: RestartDecision,
        ctx: WorkflowContext[AgentExecutorRequest, str],
    ) -> None:
        case = Case.model_validate(ctx.get_state("case"))
        if (
            case.customer_id != request.customer_id or case.verdict is None
            or case.verdict.decision != "restart" or not verdict_allowed(case.verdict, case.evidence)
        ):
            await self.fallback(case, ctx)
            return
        if case.restart is None:
            case.restart = (
                await asyncio.to_thread(backend.restart_modem, case.customer_id)
                if response.approved else {"restart_sent": False, "declined": True}
            )
            ctx.set_state("case", case.model_dump())
        await ctx.send_message(answer_request(case))

    @response_handler
    async def receive_approval(
        self, request: RestartApproval, response: RestartDecision | bool,
        ctx: WorkflowContext[AgentExecutorRequest, str],
    ) -> None:
        await self.approved(request, RestartDecision.model_validate(response), ctx)

    async def handover(self, case: Case, reason: str) -> dict[str, Any]:
        return await asyncio.to_thread(
            backend.escalate_to_human,
            reported_problem=case.symptom,
            capabilities_checked=["get_line_status", "get_known_incidents", "check_equipment"],
            evidence=[json.dumps(case.evidence, ensure_ascii=False)],
            unresolved_issue=reason,
            recommended_next_investigation="Review the collected evidence and run a line test.",
            customer_id=case.customer_id,
            customer_language=case.language,
        )

    async def fallback(self, case: Case, ctx: WorkflowContext[AgentExecutorRequest, str]) -> None:
        self.last_decision = "unknown"
        ctx.set_state("decision", "unknown")
        handover = await self.handover(case, "The diagnosis was invalid or unsupported by the evidence.")
        await ctx.yield_output(f"{scope_message(case.language)} {handover_message(case.language, handover['handover_id'])}")


def identifier_question(language: str) -> str:
    return (
        "Please provide a valid customer ID, email address or phone number so I can check your connection."
        if language == "en" else
        "Veuillez donner un identifiant client, une adresse e-mail ou un numero de telephone valide."
    )


def scope_message(language: str) -> str:
    return (
        "I handle connection problems. I cannot safely diagnose this request; a human advisor can help."
        if language == "en" else
        "Je traite les problemes de connexion. Je ne peux pas diagnostiquer cette demande ; un conseiller peut aider."
    )


def handover_message(language: str, handover_id: str, verdict: Verdict | None = None) -> str:
    text = (
        f"I have handed your case to a human advisor: {handover_id}."
        if language == "en" else f"Votre dossier a ete transmis a un conseiller : {handover_id}."
    )
    return f"{text} [{verdict.runbook_id} {verdict.runbook_title}]" if verdict else text


def retrieval_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, Sequence):
        return "\n".join(getattr(item, "text", "") or "" for item in result)
    return json.dumps(result, ensure_ascii=False, default=str)


def agent_request(payload: dict[str, Any]) -> AgentExecutorRequest:
    return AgentExecutorRequest(messages=[Message("user", [json.dumps(payload, ensure_ascii=False)])])


def answer_request(case: Case) -> AgentExecutorRequest:
    return agent_request({
        "language": case.language, "verdict": case.verdict.model_dump() if case.verdict else None,
        "evidence": case.evidence, "restart": case.restart, "runbooks": case.runbooks,
    })


def build_workflow(
    *, clients: dict[str, Any] | None = None, knowledge: Any = None,
    checkpoints: InMemoryCheckpointStorage | None = None,
) -> Workflow:
    """Build one graph per conversation, with injectable clients and knowledge for offline tests."""
    if knowledge is None:
        settings = knowledge_settings()
        knowledge = runbook_knowledge_base(
            settings["AZURE_AI_SEARCH_ENDPOINT"], settings["FOUNDRY_KNOWLEDGE_BASE_NAME"],
        )
    agents = {}
    for role, schema in (("intake", Intake), ("diagnose", Verdict), ("answer", None)):
        path = str(Path(__file__).with_name(f"workflow_lab.{role}.instructions.md"))
        if clients is None:
            template = build_agent(path, name=f"supportbuddy-workflow-{role}")
            agent = FreshAgent(
                client=ModelMeter(template.client), name=template.name, instructions=load_instructions(path),
                default_options={**template.default_options, **({"response_format": schema} if schema else {})},
                middleware=template.middleware,
            )
        else:
            agent = FreshAgent(
                client=ModelMeter(clients[role]), name=f"supportbuddy-workflow-{role}", instructions=load_instructions(path),
                default_options={"response_format": schema} if schema else None,
            )
        agents[role] = agent
    intake_executor = AgentExecutor(agents["intake"], id="intake")
    diagnose_executor = DiagnoseExecutor(agents["diagnose"], knowledge)
    answer_executor = AgentExecutor(agents["answer"], id="answer")
    identify, line, incidents, equipment = Identify(id="identify"), Line(id="line"), Incidents(id="incidents"), Equipment(id="equipment")
    collect, decide = Collect(id="collect"), Decide(id="decide")
    return (
        WorkflowBuilder(
            name="supportbuddy-workflow", start_executor=intake_executor,
            checkpoint_storage=checkpoints or InMemoryCheckpointStorage(), output_from=[identify, decide, answer_executor],
        )
        .add_edge(intake_executor, identify)
        .add_fan_out_edges(identify, [line, incidents, equipment])
        .add_fan_in_edges([line, incidents, equipment], collect)
        .add_edge(collect, diagnose_executor)
        .add_edge(diagnose_executor, decide)
        .add_edge(decide, answer_executor)
        .build()
    )


@contextlib.asynccontextmanager
async def open_workflow(workflow: Workflow) -> AsyncIterator[Workflow]:
    """Use the same agent/MCP lifecycle as the Stage 8 reference."""
    async with contextlib.AsyncExitStack() as stack:
        for executor in workflow.get_executors_list():
            if isinstance(executor, AgentExecutor):
                await stack.enter_async_context(open_agent(executor.agent))
        yield workflow


def model_counts(workflow: Workflow) -> dict[str, int]:
    return {
        executor.id: executor.agent.client.calls
        for executor in workflow.get_executors_list() if isinstance(executor, AgentExecutor)
    }


def ask_restart(request: RestartApproval) -> bool:
    try:
        answer = input(f"  [approval needed] {request.question} [y/N] ")
    except EOFError:
        answer = ""
    return answer.strip().lower() in ("y", "yes")


async def run_case(
    workflow: Workflow, prompt: str, *, approve: Callable[[RestartApproval], bool] = ask_restart,
    emit: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Run and resume one case, reporting observed steps rather than parsing model prose."""
    before = model_counts(workflow)
    steps = []
    outputs = []
    started = time.perf_counter()
    pending: dict[str, Any] | None = None
    tracer = trace.get_tracer("supportbuddy.workflow")
    with tracer.start_as_current_span("supportbuddy.workflow.run"):
        while True:
            stream = workflow.run(
                prompt if pending is None else None, responses=pending, stream=True,
            )
            async for event in stream:
                if event.type == "executor_invoked":
                    steps.append(event.executor_id)
                    emit(f"  [step] {event.executor_id}")
                elif event.type in ("failed", "executor_failed"):
                    raise RuntimeError(f"Workflow step failed: {event.executor_id or 'run'}")
            result = await stream.get_final_response()
            outputs.extend(result.get_outputs())
            requests = result.get_request_info_events()
            if not requests:
                break
            pending = {request.request_id: {"approved": approve(request.data)} for request in requests}
    text = "".join(item if isinstance(item, str) else getattr(item, "text", "") for item in outputs)
    counts = {role: count - before[role] for role, count in model_counts(workflow).items()}
    decide = next(executor for executor in workflow.get_executors_list() if isinstance(executor, Decide))
    decision = decide.last_decision if counts.get("diagnose") else "intake_stop"
    elapsed = time.perf_counter() - started
    emit(f"\nSupportBuddy: {text}")
    emit(f"Summary: steps={len(steps)} model_calls={sum(counts.values())} elapsed={elapsed:.2f}s")
    return {
        "answer": text, "decision": decision, "runbooks": ",".join(dict.fromkeys(re.findall(r"\[(RB-\d+)", text))),
        "model_calls": sum(counts.values()), "model_calls_by_agent": counts, "steps": steps, "elapsed": elapsed,
    }


async def compare(customer_id: str) -> None:
    """Compare live reference runs without importing participant working files."""
    if customer_id not in {"CUST-1001", "CUST-1002", "CUST-1003", "CUST-1004"}:
        raise ValueError("Choose a synthetic customer from CUST-1001 through CUST-1004.")
    prompt = f"I'm {customer_id}, my internet is not working well."
    workflow = build_workflow()
    async with open_workflow(workflow):
        workflow_result = await run_case(workflow, prompt, emit=lambda text: None)
    stage = importlib.import_module("solutions.stage_9_toolbox")
    agent = build_agent(stage.__file__, stage.tools())
    meter = ModelMeter(agent.client)
    agent.client = meter
    tool_names = []
    started = time.perf_counter()
    async with open_agent(agent):
        session = agent.create_session()
        response = await agent.run(prompt, session=session)
        while True:
            for message in response.messages:
                tool_names.extend(
                    content.name or content.tool_name for content in message.contents
                    if content.type in ("function_call", "mcp_server_tool_call")
                )
            if not response.user_input_requests:
                break
            decisions = []
            for request in response.user_input_requests:
                approved = ask_restart(RestartApproval(
                    question="Stage 9: approve the requested restart?", customer_id=customer_id, equipment_id="",
                ))
                decisions.append(request.to_function_approval_response(approved))
            response = await agent.run(Message("user", decisions), session=session)
    elapsed = time.perf_counter() - started
    if any("escalate_to_human" in name for name in tool_names):
        observed = "handover (tool)"
    elif any("restart_modem" in name for name in tool_names):
        observed = "restart (tool)"
    else:
        observed = "read reply"
    citations = ",".join(dict.fromkeys(re.findall(r"\[(RB-\d+)", response.text)))
    print(f"Prompt: {prompt}\nWorkflow: {workflow_result['answer']}\nStage 9: {response.text}\n")
    print("Run      Decision            Runbook       Model calls  Steps/tools  Seconds")
    print(f"Workflow {workflow_result['decision']:<19} {workflow_result['runbooks']:<13} "
          f"{workflow_result['model_calls']:<12} {len(workflow_result['steps']):<12} {workflow_result['elapsed']:.2f}")
    print(f"Stage 9  {observed:<19} {citations:<13} {meter.calls:<12} {len(tool_names):<12} {elapsed:.2f}")
    print("Stage 9 has no typed verdict: non-action decisions must be read from its reply.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Optional SupportBuddy workflow lab (synthetic data only).")
    parser.add_argument("prompt", nargs="?", help="Run one console case instead of DevUI.")
    parser.add_argument("--compare", metavar="CUSTOMER_ID", help="Compare with the Stage 9 reference agent.")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    load_settings()
    configure_local_tracing()
    if args.compare:
        asyncio.run(compare(args.compare))
    elif args.prompt:
        async def answer_once() -> None:
            workflow = build_workflow()
            async with open_workflow(workflow):
                await run_case(workflow, args.prompt)
        asyncio.run(answer_once())
    else:
        workflow = build_workflow()
        port = int(os.getenv("DEVUI_PORT", "8090"))
        token = _devui_token()
        print(f"\nDevUI: {app_url(port)}\nAuthentication token: {DEVUI_TOKEN_FILE} (not printed).")
        print("Use one customer message per run. Start a new run for another case.")
        logging.basicConfig(level=logging.INFO, format="%(message)s")
        serve_branded(workflow, port, token)


if __name__ == "__main__":
    main()