# Optional lab: explicit workflow orchestration in MAF

Build a second, **independent** optional variant of SupportBuddy that showcases explicit graph orchestration with Microsoft Agent Framework workflows.

| | |
| --- | --- |
| Placement | Optional extension after Stage 9 (independent from numbered stages) |
| Duration | About 45-60 minutes |
| Azure changes | None |
| Dependency changes | None |
| Working files touched | `src/workflow_lab.py` and dedicated workflow instruction files |

## Why this lab exists

The numbered workshop path teaches one main support agent and its enterprise integrations. This optional lab teaches a different concern: **explicit control flow** in MAF (fan-out/fan-in, typed verdicts, pause/resume approvals, deterministic routing), while still using the same synthetic domain and Stage 9 live tools.

## Design summary

The workflow has explicit executors for data collection and decisioning, plus three judgment agents:

1. `intake`: structured request extraction.
2. `diagnose`: deterministic one-pass runbook retrieval, then model synthesis.
3. `answer`: customer-facing response with runbook citation.

Deterministic executors (`identify`, `line`, `incidents`, `equipment`, `collect`, `decide`) enforce rule gates and action safety.

```mermaid
flowchart TD
  U[User message] --> I[intake agent]
  I --> ID[identify customer]
  ID --> L[line status]
  ID --> N[known incidents]
  ID --> E[equipment check]
  L --> C[collect]
  N --> C
  E --> C
  C --> D[diagnose agent + runbook retrieve]
  D --> X{decide}
  X -->|outage| A[answer agent]
  X -->|wifi_advice| A
  X -->|line_fault| H[handover]
  X -->|restart| P[request_info pause]
  P --> X
```

## Runtime modes

Run the commands below from `src/`.

- Console mode:

```powershell
uv run python workflow_lab.py "I'm CUST-1001, my internet is down"
```

- Compare mode against Stage 9 baseline:

```powershell
uv run python workflow_lab.py --compare CUST-1002
```

- DevUI mode:

```powershell
uv run python workflow_lab.py
```

### Run a case in DevUI

1. Open the printed URL (default `http://localhost:8090`) and enter the token shown in the terminal.
2. Select **supportbuddy-workflow** from the **Workflows** list, then choose **Configure & Run**.
3. In **Configure Workflow Inputs**, use these values:

  | Field | Value |
  | --- | --- |
  | `role` | `user` |
  | `contents` | `I'm CUST-1001, my internet is down` |
  | `author_name` | Leave blank |
  | `message_id` | Leave blank |
  | `additional_properties` | Leave blank |
  | `raw_representation` | Leave blank |

  `role` is the chat-message speaker, not the name of an agent. Enter the customer sentence as plain
  text in `contents`, then click **Run Workflow**.
4. Watch the graph for executor progress. The parallel `line`, `incidents`, and `equipment` checks
  fan into `collect`, followed by `diagnose` and `decide`. An answerable case continues to `answer`.
5. Open **Events** to inspect workflow events. Open **Traces** to see spans for executor processing,
  runbook retrieval, and the model-backed agents. Agent spans are named `supportbuddy-workflow-intake`,
  `supportbuddy-workflow-diagnose`, and `supportbuddy-workflow-answer`.
6. To demonstrate the approval pause, start another run with `I'm CUST-1002, my internet is down`.
  Review the restart request and approve or decline it in DevUI. Approval records a simulated action
  in the synthetic backend; it does not restart real equipment.

The form runs one workflow input; it is not a persistent chat composer. Choose **Run Again** or start a
new run for another scenario. `CUST-1001` invokes all three model-backed agents. `CUST-1004` takes the
line-fault handover path after `intake` and `diagnose`, without invoking `answer`.

## Test prompt library

For every prompt below, put the text in `contents` in DevUI, with `role` set to `user`. In console mode,
pass the same text as the command argument. Start a fresh run for each case.

| Prompt | Expected route | Agents and effects |
| --- | --- | --- |
| `I'm CUST-1001, my internet is down` | Area outage, RB-09 | `intake` + `diagnose` + `answer`; no action or handover record. |
| `I'm CUST-1002, my internet is down` | Unreachable modem, RB-02 | `intake` + `diagnose`; pause for approval. Approve to record one synthetic restart and run `answer`; decline to record no restart and return manual steps. |
| `I'm CUST-1003, my internet is down` | Wi-Fi advice, RB-03 | `intake` + `diagnose` + `answer`; no action or handover record. |
| `I'm CUST-1004, my internet is down` | Line fault, RB-07 | `intake` + `diagnose`; create a synthetic handover instead of calling `answer`. |
| `internet down` | Ask for a customer identifier | `intake` only; no back-office checks. |
| `CUST-9999 internet down` | Ask for a valid customer identifier | `intake` only; unknown customer, no back-office checks. |
| `what is my bill?` | Out-of-scope response | `intake` only; no back-office checks. |

The first four prompts are the exact inputs used by the automated scenario-path test. The live
four-customer run also passed with this phrasing for each ID: `I am CUST-1001, my internet is not
working well.` Replace the ID to try the other three routes. The final three exercise intake stop
conditions. Restart and handover records are local synthetic effects, not real equipment actions or
support cases. For a read-only demonstration, use CUST-1001 or CUST-1003.

Malformed model output, an unsupported verdict, and a restart verdict that conflicts with an open
incident are additional **offline scripted tests**, not reliable prompts to type into DevUI. They are
covered by [`tests/test_workflow_lab.py`](../tests/test_workflow_lab.py#L208) using scripted clients.

### DevUI screenshots

The graph view shows the complete fan-out/fan-in path and the final answer executor:

![Completed SupportBuddy workflow graph in DevUI](assets/workflow-lab-graph.png)

The trace view shows executor spans, runbook retrieval inside diagnosis, and the separate diagnosis
and answer agent spans:

![SupportBuddy workflow graph and OTel executor and agent spans in DevUI](assets/workflow-lab-traces.png)

## Validation and gates

### Offline gate

- `tests/test_workflow_lab.py` passes with 25 tests.
- Coverage includes scenario paths, request_info pause/resume shapes, fallback handling, fan-out behavior, state isolation, and AG-UI factory isolation.

### Live gate (real model/router + live runbooks)

The four synthetic customers produced the expected route decisions and citations:

| Customer | Expected path | Observed decision | Runbook | Model calls |
| --- | --- | --- | --- | --- |
| CUST-1001 | Area outage | outage | RB-09 | intake 1, diagnose 1, answer 1 |
| CUST-1002 | Modem unreachable, restart flow | restart (after approval) | RB-02 | intake 1, diagnose 1, answer 1 |
| CUST-1003 | Wi-Fi congestion | wifi_advice | RB-03 | intake 1, diagnose 1, answer 1 |
| CUST-1004 | Line fault, handover | line_fault | RB-07 | intake 1, diagnose 1, answer 0 |

Observed synthetic side effects (isolated `LAB_STATE_DIR` run):

- restart actions: 1
- handovers: 1

## Compare-mode observation

For `CUST-1002`, compare mode showed:

- Workflow path: typed `restart` decision with approval pause/resume.
- Stage 9 baseline path: reply-first behavior without typed verdict output.

This is the core teaching point: explicit workflow orchestration makes branch behavior inspectable without inferring decision state from prose alone.

## Files in this optional lab

| File | Purpose |
| --- | --- |
| [`src/workflow_lab.py`](../src/workflow_lab.py#L366) | Builds the workflow graph and creates the three code-first MAF agents in `build_workflow()`. This is not the Stage 10 Foundry Prompt Agent. |
| [`src/workflow_lab.intake.instructions.md`](../src/workflow_lab.intake.instructions.md) | Instructions for the `intake` agent. |
| [`src/workflow_lab.diagnose.instructions.md`](../src/workflow_lab.diagnose.instructions.md) | Instructions for the `diagnose` agent; runbook retrieval is performed by its `DiagnoseExecutor` before the model call. |
| [`src/workflow_lab.answer.instructions.md`](../src/workflow_lab.answer.instructions.md) | Instructions for the `answer` agent. |
| [`src/lab_agent.py`](../src/lab_agent.py) | Shared Foundry chat-client construction, settings, authentication, and agent lifecycle. |
| [`src/lab_backend/backend.py`](../src/lab_backend/backend.py) | Synthetic customer, network diagnostics, restart, and handover operations. |
| [`src/tools/knowledge_tools.py`](../src/tools/knowledge_tools.py) | Stage 9 runbook knowledge-base MCP client used by `diagnose`. |
| [`tests/test_workflow_lab.py`](../tests/test_workflow_lab.py#L137) | Offline scenario paths, model-call counts, approval resume, early stops, and scripted failure cases. |

The three model-backed agents are created separately from role-specific instructions and configured
Foundry chat clients in [`build_workflow()`](../src/workflow_lab.py#L377). They are then wired as follows:

```python
intake_executor = AgentExecutor(agents["intake"], id="intake")
diagnose_executor = DiagnoseExecutor(agents["diagnose"], knowledge)
answer_executor = AgentExecutor(agents["answer"], id="answer")
```

The graph connections are immediately below the wiring in [`workflow_lab.py`](../src/workflow_lab.py#L398).
These are **three code-first MAF agents**, not three Foundry Prompt Agents.

| Agent | Code location | Instructions |
| --- | --- | --- |
| `intake` | [`AgentExecutor` wiring](../src/workflow_lab.py#L392); created from shared Foundry settings in the [agent loop](../src/workflow_lab.py#L377). | [`workflow_lab.intake.instructions.md`](../src/workflow_lab.intake.instructions.md) |
| `diagnose` | [`DiagnoseExecutor` class](../src/workflow_lab.py#L212), including the runbook call; [`graph wiring`](../src/workflow_lab.py#L393). | [`workflow_lab.diagnose.instructions.md`](../src/workflow_lab.diagnose.instructions.md) |
| `answer` | [`AgentExecutor` wiring](../src/workflow_lab.py#L394); created from shared Foundry settings in the [agent loop](../src/workflow_lab.py#L377). | [`workflow_lab.answer.instructions.md`](../src/workflow_lab.answer.instructions.md) |

The deterministic `Identify`, `Line`, `Incidents`, `Equipment`, `Collect`, and `Decide` executors are
defined in the same file and do not make model calls.

This lab is intentionally isolated from numbered-stage participant files.
