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