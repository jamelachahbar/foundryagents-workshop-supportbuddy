# Instructor facilitation guide

This guide is for the trainer or an explicitly authorized environment owner, not a participant
task list. Use [setup.md](setup.md) for laptop prerequisites, Azure provisioning, deployments and
permissions. Use the [participant workshop](../docs/workshop.md) and
[walkthrough](../docs/walkthrough.md) during the session.

Participants should not create shared knowledge resources, deploy extra models, assign policies
or change the group's model/router configuration while following a demo. Announce the selected
route and budget, prepare a separate sandbox, and distinguish observing a demo from completing
an independent exercise. All data must remain synthetic.

For the optional network MCP identity demonstration, see the [secure MCP instructor guide](secure-mcp-demo.md).
Its role-enforcement path is experimental and requires additional live verification before a workshop.

## Stage 8: retrieval effort and output mode

This extension is optional and is not required for the participant Stage 8 checkpoint. Complete
the [model-access preparation](setup.md#optional-model-backed-knowledge-base-experiments) first:
Search needs its own managed identity and model-access role, and the portal needs a supported
chat deployment. Standard participants keep read access. No DevUI, contact centre or local network
MCP server is needed for this portal-only demonstration.

1. In the **Azure portal**, open the workshop's Azure AI Search service and **Knowledge bases**.
   Create a separate base called `lab-<alias>-runbooks-experiment`. Select the existing knowledge
   source `supportbuddy-runbooks-source`; do not recreate the source/index or edit the standard
   `supportbuddy-runbooks` base. The source name is not the knowledge base name.
2. Add a portal-supported **chat completion model**, using **System assigned identity** authentication.
   This is the Search service's identity, not your user or the Foundry project's identity. If the
   workshop agent model is not selectable, arrange a [supported model](https://learn.microsoft.com/azure/search/get-started-portal-agentic-retrieval#supported-llms)
   before the session rather than asking participants to create deployments.
3. Compare retrieval effort and output mode only on the experimental base. Prefer per-request
   playground overrides where offered. If a control is unavailable, use the documented retrieve API;
   do not modify the shared baseline to force a demonstration.

Compatibility for `2026-08-01-preview`:

| Retrieval effort | `extractiveData` | `answerSynthesis` |
| --- | --- | --- |
| `minimal` | Supported | Not supported |
| `low` | Supported | Supported |
| `medium` | Supported | Supported |
| `auto` | Supported | Supported |

Suggested comparisons:

| Retrieval effort | Output mode | Evidence to compare |
| --- | --- | --- |
| `minimal` | `extractiveData` | Direct retrieval baseline without LLM query planning. |
| `low` | `extractiveData` | LLM planning/source selection while returning grounding content. |
| `medium` | `extractiveData` | Deeper retrieval and any follow-up search. |
| `low` | `answerSynthesis` | A generated answer grounded in retrieved content. |
| `auto`, if offered | `extractiveData` | Adaptive retrieval with planning when needed. |

You can also compare `medium` or `auto` with `answerSynthesis`. Model-backed modes need an accessible
configured model. `medium` has regional restrictions; `auto` requires `2026-08-01-preview` and a model.
The portal may expose fewer options than the API. These are knowledge-base settings, not the agent's
`SUPPORTBUDDY_REASONING_EFFORT`; none creates embeddings or vector fields.

Start a fresh playground conversation per configuration and use the same query:

```text
Wi-Fi is slow upstairs, but a wired connection works. What procedure applies?
```

Compare runbook IDs, citations, response shape, elapsed time, planning/synthesis activity and token
usage where exposed. A generated answer is not proof of better retrieval; check the cited procedures.
Planning and synthesis add Azure usage and load to the selected deployment.

Leave participant settings and toolbox connections on the standard base. Delete only the named
experimental base if cleanup is agreed, not its shared source/index. The local `knowledge_base.py ask`
helper prints extractive runbook titles; do not use it to demonstrate synthesized output.

References: [portal workflow](https://learn.microsoft.com/azure/search/get-started-portal-agentic-retrieval),
[retrieval effort](https://learn.microsoft.com/azure/search/agentic-retrieval-how-to-set-retrieval-reasoning-effort),
[output modes](https://learn.microsoft.com/azure/search/agentic-retrieval-how-to-answer-synthesis).

## Stage 8: embeddings and hybrid retrieval

Prepare this separate sandbox only when the session should demonstrate vectorization as well as
the baseline MCP retrieval and citations. It requires ingestion setup and live Azure usage; adding
a model deployment or changing an environment variable alone does not enable it.

| Demonstration | What changes | Evidence to show |
| --- | --- | --- |
| Participant baseline | Nothing: text-only index, semantic ranking, minimal/extractive retrieval. | Runbooks/citations, with no embedding configuration in the index. |
| Planning/synthesis demo above | A separate knowledge base with a supported chat model. | Planning activity and synthesized answers when selected. |
| Vector/hybrid sandbox | New ingestion pipeline, chunked/vector index, embedding skill/vectorizer and separate knowledge source/base. | Successful ingestion, embedding usage where available, and comparable retrieval results. |

1. Copy the same synthetic runbook content into an approved supported data source, such as an
   isolated Azure Blob container. Follow the [integrated-vectorization quickstart](https://learn.microsoft.com/azure/search/search-get-started-portal-import-vectors)
   to create new named assets. Do not overwrite the standard index, source, base or toolbox connection.
2. Select a supported text embedding deployment in the import wizard. Reuse the baseline
   `text-embedding-3-small` deployment only if the integration, resource/region and quota support it;
   otherwise provision a separate deployment. Grant only the documented managed-identity permissions
   for that provider, source access and embedding invocation. Memory permissions do not automatically
   configure Search access.
3. Enable suitable chunking/vectorization. Verify a populated vector field, vector search profile,
   query-time vectorizer, and matching model/dimensions for indexing and querying. Check indexer
   completion and available embedding-request metrics/traces. A deployed model alone proves none of this.
4. Create a separate search-index knowledge source and base for that index. Configure a supported
   chat model if also demonstrating planning/synthesis. Use supported query controls and inspect
   index queries plus knowledge-base activity; semantic ranking alone is not proof of vector retrieval.
5. Compare the same questions and source content with the text baseline: applicable runbooks,
   citation accuracy, missed matches, latency and usage. Hold retrieval settings fixed when isolating
   vectorization, and hold the vector index fixed when comparing planning effort.

Show the configured embedding path, a successful ingestion/query and an evidence-backed comparison.
This is not a feature already implemented by `knowledge_base.py`. Leave participants on the standard
base unless an explicit migration exercise is planned. Remove only sandbox assets; do not reset
shared memory or delete shared deployments.

## Stage 12: portal evaluation preparation

Choose the route before class and follow the [setup preparation checklist](setup.md#evaluation-preparation-stage-12).
For Route A, upload [portal_quality.jsonl](../src/evals/portal_quality.jsonl) once, verify evaluation
permissions and the standard fixed judge deployment, and announce the common dataset version, agent/version, judge,
criteria and budget. Prepare a toolbox-only Stage 10 sandbox without local function declarations.
Give participants their own sandbox agent if they will edit independently; only you edit a shared demo.
Normal setup provisions `supportbuddy-judge` separately from the agent's router. For existing
environments, review saved `DEPLOY_EVALUATION_MODEL` and model/version overrides before deliberate
reprovisioning. Distribute the updated handout afterward; existing local `.env` and terminal settings
are not overwritten by deployment. Keep the judge model/version fixed during comparison exercises.

Participants follow [portal-evaluation.md](../docs/portal-evaluation.md). That guide covers dataset
selection, the input template, built-in criteria, submission and report interpretation. Do not require
them to create a custom evaluator, provision a judge or alter another participant's agent.

For the full-conversation-history demonstration, prepare authorized synthetic recorded conversations
or a correctly mapped conversation dataset. Local contact-centre history is not automatically stored
in Foundry. Record the scope, selected conversations/time range, agent version and compatible criteria.
Explain that the score can differ from an isolated-turn score and is not directly interchangeable.
If participants only watch, label their result observed rather than independently run.

## Stage 13: portal guardrail demonstration

Policy creation/assignment requires appropriate account-level permissions; the documented prerequisite
is **Foundry Account Owner** or higher. Perform writes yourself, not through an elevated participant
group. Use a non-production toolbox-only sandbox agent and synthetic cases. Do not modify the shared
`model-router` deployment, standard `supportbuddy-guardrail`, other participants' agents or toolbox default.

1. Open Foundry **Build > Guardrails**. Inspect the intended policy's controls, actions and assigned
   assets. Record which agent/model is covered; a policy's existence does not prove a test uses it.
2. If demonstrating creation, select **Create Guardrail**, retain required default protections and
   add the agreed control, such as **User prompt attack** on **User input** with **Annotate and block**.
   Add the control, choose **Next**, assign only the prepared sandbox agent, review, name and create it.
3. Use **Try in Playground**, or the sandbox's **Guardrails > Manage**, to verify the intended
   assignment. If the UI or test button is unavailable, check permissions and supported assets;
   do not disable protection or broaden participant permissions to force the exercise. Assignment
   changes affect the selected asset, so agree the scope before saving.
4. Have participants follow the [Route A probes and observation checkpoint](../docs/workshop.md#route-a-instructor-led-portal-guardrails)
   in fresh conversations. Keep the agent version/model/tools fixed. Coordinate any before/after
   comparison without removing required protections; an already-blocked baseline need not change.
5. Explain block versus annotation versus behavioral refusal versus error. Do not claim a user-input
   shield proves MCP tool-response filtering, or confuse an agent/model policy assignment with the
   separate toolbox RAI policy. Python customer-ownership enforcement belongs to Route B.
6. Restore only changed sandbox assignments after the exercise. Keep required protections and shared
   resources intact. If participants lack portal access, provide an observed demonstration or Route B;
   do not report an independent run that did not happen.

References: [portal guardrail procedure](https://learn.microsoft.com/azure/foundry/guardrails/how-to-create-guardrails),
[intervention points](https://learn.microsoft.com/azure/foundry/guardrails/intervention-points).

## Stage 13: three-layer live presentation

This is an optional trainer presentation, not an extra participant setup task or checkpoint.
First complete the code-first tasks in an instructor-owned working copy. From its `src/` folder:

```bash
uv run python guardrail_demo.py --agent main --live --color always
```

This incurs live model/tool usage and can update memory or synthetic state. It rejects action
approvals but does not undo other tool calls. No policies are created, modified or disabled.
Without `--live`, the existing demonstration remains offline.

| Layer | Demonstration | Evidence to point to |
| --- | --- | --- |
| Platform | Send the S2 jailbreak query to the code-first agent. | Actual content-filter exception type and metadata when a platform block is observed. |
| Code | Force same-customer and other-customer requests through the actual entry-point middleware with a scripted client. | Allowed dummy execution versus `blocked=true`, with the forbidden body never executed. |
| Instructions/model | Send the neighbour-permission attack to the code-first agent. | Live answer and attempted calls; refusal without calls is behavioral evidence, not proof of a filter/middleware block. |

The terminal shows input, recorded tools/results, reply or exception and an evidence summary. Live
messages are labelled LIVE; the offline conversation is SCRIPTED. Tool activity appears after each
response rather than streaming during execution. Blue marks input, cyan interactions, yellow
uncertainty/checks, red blocks and green verified code assertions or platform evidence.

A normal platform-run response means NOT DEMONSTRATED, not a simulated block. The third layer does
not isolate instructions from the model's built-in behavior. These probes do not prove the separate
toolbox Indirect Attack filter ran. Other Azure errors are errors, not evidence of protection.

## Optional workflow lab: instructor demo

Use this extension after Stage 9 to demonstrate explicit MAF workflow orchestration. It is separate
from the numbered participant stages and uses the existing Foundry model deployment, Stage 9 toolbox,
and runbook knowledge base. It creates no Azure resources and adds no dependencies. The workflow uses
three code-first MAF agents; it does not use the Stage 10 Foundry Prompt Agent.

### Before the session

1. Use an instructor-owned working copy with the optional workflow files present. Do not copy these
   files over participant `main.py` or instruction files.
2. Verify `src/.env` has the project endpoint, model deployment, knowledge-base settings, and toolbox
   configuration used by Stage 9. Sign in with `az login` and confirm the Stage 9 toolbox/runbooks work.
3. From `src/`, run the workflow-specific tests if you want an offline check:

   ```powershell
   uv run pytest -c pyproject.toml ../tests/test_workflow_lab.py -q
   ```

### Run the DevUI demonstration

1. From `src/`, start the workflow:

   ```powershell
   uv run python workflow_lab.py
   ```

2. Open the printed DevUI URL (default `http://localhost:8090`) and authenticate with the token
   printed in the terminal. If port 8090 is occupied, set `DEVUI_PORT` to an available port before
   launching.
3. In DevUI, select **Workflows** and open **supportbuddy-workflow**. Choose **Configure & Run**.
4. In the structured input form, enter:

   | Field | Value |
   | --- | --- |
   | `role` | `user` |
   | `contents` | `I'm CUST-1001, my internet is down` |
   | `author_name`, `message_id`, `additional_properties`, `raw_representation` | Leave blank |

   Here `role` is the chat-message speaker, not the name of an agent. Click **Run Workflow**.
5. Point out the executor events as they run. The graph exposes `intake`, `identify`, parallel
   `line` / `incidents` / `equipment` checks, `collect`, `diagnose`, `decide`, and, for answerable
   cases, `answer`. Runbook retrieval occurs inside `diagnose`, not as a separate executor.

### Suggested live scenarios

| Message in `contents` | Expected route | Teaching point |
| --- | --- | --- |
| `I'm CUST-1001, my internet is down` | `intake` + evidence collection + `diagnose` + `answer`; outage, RB-09 | Normal evidence fan-out and grounded answer; all three judgment agents run. |
| `I'm CUST-1002, my internet is down` | `intake` + `diagnose` + `decide`; approval pause; then `answer`; restart, RB-02 | Human approval gates the synthetic restart. Explain the effect before approving. |
| `I'm CUST-1003, my Wi-Fi is slow` | `intake` + evidence collection + `diagnose` + `answer`; Wi-Fi advice, RB-03 | Contrasts a local Wi-Fi issue with an access-line outage. |
| `I'm CUST-1004, my internet is unstable` | `intake` + evidence collection + `diagnose` + handover; line fault, RB-07 | At least two agents run; deterministic decision routes to a human instead of `answer`. |

All customers and backend effects are synthetic. Approving CUST-1002 writes a local synthetic restart
action record; CUST-1004 writes a synthetic handover record. Neither restarts real equipment nor
creates a real support case. For a read-only first demo, use CUST-1001 or CUST-1003.

### Agent count and source locations

The workflow has **three distinct code-first MAF agents**, each with its own instructions:

| Agent | Role | Construction |
| --- | --- | --- |
| `supportbuddy-workflow-intake` | Extract customer ID, symptom, language, and scope | `src/workflow_lab.py`, `build_workflow()` |
| `supportbuddy-workflow-diagnose` | Synthesize evidence into a typed verdict after deterministic runbook retrieval | `src/workflow_lab.py`, `DiagnoseExecutor` and `build_workflow()` |
| `supportbuddy-workflow-answer` | Write the customer-facing, cited response | `src/workflow_lab.py`, `build_workflow()` |

Their prompt files are `src/workflow_lab.intake.instructions.md`,
`src/workflow_lab.diagnose.instructions.md`, and `src/workflow_lab.answer.instructions.md`.
The executor graph, fan-out/fan-in, and routes are assembled in `build_workflow()` in
`src/workflow_lab.py`. `Identify`, `Line`, `Incidents`, `Equipment`, `Collect`, and `Decide` are
deterministic executors, not additional model agents. Most diagnosed customer cases invoke all three
agents; the line-fault case invokes two and hands over. Out-of-scope or unidentified requests may stop
after `intake`.

### Scope and troubleshooting

- This demo is DevUI-only. The contact-centre UI currently wraps agents or the Stage 10 Prompt Agent;
  it does not host this `Workflow` graph.
- DevUI presents a workflow runner rather than a persistent customer chat. Start a new run for each
  scenario; its workflow event view is where executor steps appear.
- If the workflow entity is missing, restart the process with `uv run python workflow_lab.py` and
  select **Workflows**. If execution fails during runbook retrieval, verify the Stage 9 knowledge-base
  settings and access; do not change shared Azure resources during the demo.
- Compare with the Stage 9 single-agent reference only when time permits:

  ```powershell
  uv run python workflow_lab.py --compare CUST-1002
  ```

  This runs both live paths and prompts for approval. Approve only after explaining the synthetic
   action, or decline at the prompt.
