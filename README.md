<!-- markdownlint-disable MD033 -->
<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.png">
    <img alt="SupportBuddy - Connectivity Agent Lab" src="docs/assets/logo.png" width="640">
  </picture>
</p>
<!-- markdownlint-enable MD033 -->

# Build an enterprise connectivity-support agent with Microsoft Foundry

Hands-on lab and workshop. You build one agent, **SupportBuddy**, with
[Microsoft Agent Framework](https://learn.microsoft.com/agent-framework/) and
[Microsoft Foundry](https://learn.microsoft.com/azure/foundry/), and grow it stage by stage from a
chatbot into an enterprise support agent that collects evidence from several capabilities, uses MCP,
and hands the case to a human when it cannot resolve it.

**Start here: [docs/workshop.md](docs/workshop.md)**

Optional Copilot skills and references: [.github/skills/README.md](.github/skills/README.md).

Visual architecture guide: [AG-UI in SupportBuddy](docs/ag-ui.md) - streaming, approvals and human handover.

Optional extension after Stage 9: [live evidence-panel lab and working solution](docs/optional-ag-ui-lab.md)
(`--evidence-panel`), driven by AG-UI events. Run `uv run python -m contact_centre ag_ui_lab --evidence-panel`
from `src/`; its dedicated instructions leave your current stage untouched.

## The use case, and what is synthetic

The scenario is one that every connectivity provider knows: a customer contacts support and says
*"I have a problem with my internet connection."* The agent should progressively help with the
investigation:

```text
Customer reports a connectivity problem
  -> identify the customer and relevant context
  -> retrieve information from back-office systems
  -> run a technical diagnostic (for example, ping the customer's equipment)
  -> correlate the evidence
  -> give a useful answer or diagnosis when possible
  -> hand over to a human when the agent cannot resolve the request
```

The lab does not use or describe any real company's production systems, APIs, customer data, network
platforms, incident or ticketing tools, equipment types or support processes. Everything the agent touches
is synthetic, so the same lab works for any organisation.

| Layer | Provided by |
| --- | --- |
| The use-case pattern | A common connectivity-support scenario |
| Customer, service, equipment, incident and handover systems | **This lab: synthetic, fictional, for training only** |
| Agent platform (models, agent registry, portal) | Microsoft Foundry |
| Development model (agent, tools, MCP client) | Microsoft Agent Framework (Python) |
| Enterprise integration pattern | Model Context Protocol (MCP) |

Every tool name (`identify_customer`, `get_line_status`, `check_equipment`, `get_known_incidents`,
`escalate_to_human`, `get_handover_status`, `restart_modem`), every customer (`CUST-1001` ...), line, device (`MockBox`), place (`Mock-Ville`),
incident and handover reference (`HO-9001` ...), and the contact centre, is a **lab construct**. None of them is a real API,
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
| 10 | Prompt agent | Foundry-managed orchestration: compare the default tools and optional application-executed functions |
| 11 | Foundry Memory + signed-in customer | remember preferences and past problems, outside the runtime |
| 12 | Evaluation: rule-based checks + Foundry evaluators | measure decisions, tool use, citations and safety; [improve prompts from results](docs/evaluation-improvement.md) |
| 13 | Guardrails: Foundry guardrail + code middleware | layered defence against jailbreaks and poisoned tool data |
| **Part 3** | **Run your code in Foundry** | |
| 14 | Hosted agent | the same Agent Framework code, deployed and run by Foundry |

## The agents you build

| Agent | Foundry agent type | Runs in | Stages |
| --- | --- | --- | --- |
| `lab-<alias>-supportbuddy` | Code-first: your Agent Framework code calls the model in Foundry | Your Python process | 1-9, 11-13 |
| `lab-<alias>-supportbuddy-assist` | Code-first, helps the human advisor (never the customer) | The contact centre on your laptop | 7 onwards |
| `lab-<alias>-supportbuddy-prompt` | Prompt agent: model + instructions + tools; optional functions execute in the client application | Foundry Agent Service runs the orchestration | 10 |
| `lab-<alias>-supportbuddy-hosted` | Hosted agent: your code, deployed to Foundry | Foundry Agent Service | 14 |

## Repository map

```text
docs/workshop.md                  participant guide (start here)
docs/walkthrough.md               fast click-through of the whole lab with screenshots (dry run, catch-up)
instructor/setup.md               lab environment setup: `azd up` in any tenant, or by hand (+ guardrail.json)
instructor/facilitation.md        instructor-only stage demos, sandbox preparation and cleanup
instructor/azure.yaml, infra/     the lab environment as code (Bicep) and its post-provision script (setup_lab.py)
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
src/safety_calibrate.py           optional fixed-sample scorer calibration, not an agent evaluation
src/prompt_agent.py               SupportBuddy as a Foundry prompt agent (Stage 10)
src/hosted_agent.py               SupportBuddy as a Foundry hosted agent (Stage 14)
src/lab_backend/                  synthetic back-office + data/scenarios.json (fictional data)
src/contact_centre/               customer chat, operator desk and advisor assist agent, over AG-UI (Stage 7)
src/mcp_server/                   synthetic MCP server (Stage 6) and its Dockerfile (shared copy, Stage 9)
src/solutions/                    runnable stage references, including the Stage 12 improved prompt (10 and 14 use the scripts above)
src/preflight.py                  checks your environment before the workshop
tests/                            offline tests (no Azure needed)
```

## Quick start

```bash
cd src
uv sync
cp .env.template .env      # then edit: project endpoint, model deployment, your lab-<alias> prefix
az login
uv run python preflight.py
uv run python main.py      # opens DevUI
```

**Connect to DevUI (once per browser)**

1. In Codespaces, open port **8090** from the **Ports** tab. Keep it **Private**. On a laptop, use the
  browser window opened by the command.
2. In VS Code, press **Ctrl+P** (**Cmd+P** on macOS), type `src/.devui_token`, and press **Enter**.
   This local file is created when you first start DevUI. Copy its single token value, without spaces
   or line breaks. Leave the file unchanged.
3. Paste it into DevUI's **Enter Authentication Token** field and click **Connect**.

The token is also printed below **When the browser asks for an authentication token, paste:** in the
terminal where you ran `uv run python main.py`. If you explicitly set `DEVUI_AUTH_TOKEN`, use that value
instead of the file.

The browser remembers the token. An initial `401 Unauthorized` is normal before connecting. This token
is separate from your Azure sign-in; keep it private and do not paste it into chat or shared documents.
Complete these steps alongside preflight before day 1.

Run the offline tests with `uv run pytest` from `src`.

No lab environment yet? `cd instructor; azd up` creates all of it in your own subscription; see
[instructor/setup.md](instructor/setup.md).
