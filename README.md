<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
    <img alt="SupportBuddy - Connectivity Agent Lab" src="docs/assets/logo.svg" width="640">
  </picture>
</p>

# Build an enterprise connectivity-support agent with Microsoft Foundry

Hands-on lab for the POST Luxembourg workshop. You build one agent, **SupportBuddy**, with
[Microsoft Agent Framework](https://learn.microsoft.com/agent-framework/) and
[Microsoft Foundry](https://learn.microsoft.com/azure/foundry/), and grow it stage by stage from a
chatbot into an enterprise support agent that collects evidence from several capabilities, uses MCP,
and hands the case to a human when it cannot resolve it.

**Start here: [docs/workshop.md](docs/workshop.md)**

## The use case, and what is synthetic

During the workshop scoping call on 12 October 2026, POST described this example: a customer contacts
support and says *"I have a problem with my internet connection."* The agent should progressively
help with the investigation:

```text
Customer reports a connectivity problem
  -> identify the customer and relevant context
  -> retrieve information from back-office systems
  -> run a technical diagnostic (for example, ping the customer's equipment)
  -> correlate the evidence
  -> give a useful answer or diagnosis when possible
  -> hand over to a human when the agent cannot resolve the request
```

POST has **not** supplied its production systems, APIs, customer data, network platforms, incident or
ticketing tools, equipment types or support processes. This lab does not describe them and does not
connect to them.

| Layer | Provided by |
| --- | --- |
| The use-case pattern | POST (scoping call, 12 October 2026) |
| Customer, service, equipment, incident and handover systems | **This lab: synthetic, fictional, for training only** |
| Agent platform (models, agent registry, portal) | Microsoft Foundry |
| Development model (agent, tools, MCP client) | Microsoft Agent Framework (Python) |
| Enterprise integration pattern | Model Context Protocol (MCP) |

Every tool name (`identify_customer`, `get_line_status`, `check_equipment`, `get_known_incidents`,
`escalate_to_human`, `get_handover_status`, `restart_modem`), every customer (`CUST-1001` ...), line, device (`MockBox`), place (`Mock-Ville`),
incident and handover reference (`HO-9001` ...), and the contact centre, is a **lab construct**. None of them is a POST API,
system or record.

## Stages

| Stage | You add | Concept |
| --- | --- | --- |
| **Part 1** | **Build the agent** (code-first, on your laptop) | |
| 1 | Agent only | request understood, no system access |
| 2 | `identify_customer` | request -> customer |
| 3 | `get_line_status` | request -> customer -> service -> service information |
| 4 | `check_equipment` (simulated ping) | ... -> equipment -> technical check |
| 5 | `get_known_incidents` + correlation rules | evidence from independent capabilities -> conclusion |
| 6 | Diagnostics moved behind an MCP server | Python function -> enterprise capability -> MCP server -> agent |
| 7 | `escalate_to_human`, `restart_modem` with approval, the contact centre | answer, act after a person approves, or hand the live conversation to a human advisor |
| **Part 2** | **Make it enterprise-ready with Foundry** | |
| 8 | Foundry IQ knowledge base, consumed over MCP | tools say what is wrong, runbooks say what to do - cited `[RB-xx]` |
| 9 | Foundry Toolbox | one governed, versioned MCP endpoint; credentials in project connections |
| 10 | Prompt agent | Foundry's recommended starting point: the same agent with no code - how far does it get? |
| 11 | Foundry Memory + signed-in customer | remember preferences and past problems, outside the runtime |
| 12 | Evaluation: rule-based checks + Foundry evaluators | measure decisions, tool use, citations and safety |
| 13 | Guardrails: Foundry guardrail + code middleware | layered defence against jailbreaks and poisoned tool data |
| **Part 3** | **Run your code in Foundry** | |
| 14 | Hosted agent | the same Agent Framework code, deployed and run by Foundry |

## Repository map

```text
docs/workshop.md                  participant guide (start here)
instructor/setup.md               shared resources the instructor creates once (+ guardrail.json)
src/main.py                       your agent - you extend it at each stage
src/main.instructions.md          your agent's instructions
src/lab_agent.py                  shared plumbing: settings, Foundry connection, DevUI / console runner
src/devui_branding.py             DevUI start page with the SupportBuddy name and icon (docs/assets/icon.svg)
src/tools/support_tools.py        agent tools (lab constructs) over the synthetic back-office
src/tools/knowledge_tools.py      the Foundry IQ knowledge base as an MCP tool (Stage 8)
src/knowledge/runbooks/           12 synthetic troubleshooting runbooks (RB-01 ... RB-12)
src/knowledge_base.py             create / query the Foundry IQ knowledge base (Stage 8)
src/toolbox.py                    create / inspect your Foundry Toolbox (Stages 9, 13)
src/memory_store.py               create / inspect / forget the Foundry Memory store (Stage 11)
src/evaluate.py, src/evals/       evaluation dataset and runner (Stage 12)
src/prompt_agent.py               SupportBuddy as a Foundry prompt agent (Stage 10)
src/hosted_agent.py               SupportBuddy as a Foundry hosted agent (Stage 14)
src/lab_backend/                  synthetic back-office + data/scenarios.json (fictional data)
src/contact_centre/               customer chat, operator desk and advisor assist agent, over AG-UI (Stage 7)
src/mcp_server/                   synthetic MCP server (Stage 6) and its Dockerfile (shared copy, Stage 9)
src/solutions/                    complete, runnable reference for every stage
src/preflight.py                  checks your environment before the workshop
tests/                            offline tests (no Azure needed)
```

## Quick start

```bash
cd src
uv sync
cp .env.template .env      # then edit: project endpoint, model deployment, your post-<alias> prefix
az login
uv run python preflight.py
uv run python main.py      # opens DevUI
```

Run the offline tests with `uv run pytest` from `src`.
