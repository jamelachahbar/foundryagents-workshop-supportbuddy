# Lab environment setup

Everything the participants use that they do not build themselves: the Foundry project and models, the
runbook knowledge base, the network MCP server, the toolbox connections, Application Insights, the memory store and the
guardrail. Everything here is synthetic training data.

## Who sets it up?

The same setup works in **any tenant and subscription**. Nothing has to exist beforehand: no registry, no
Container Apps environment, no identity.

| Situation | Who runs it | Where |
| --- | --- | --- |
| The instructor brings the environment | Instructor | Instructor's tenant; participants are invited as guests, or use accounts in that tenant |
| The customer hosts the environment | An organizer at the customer | Customer's tenant, one shared environment for the whole group |
| Every participant on their own | Each participant | Their own subscription (for example a Visual Studio subscription); they are the only user |

One shared environment per group is the normal case. A personal environment costs more (an Azure AI
Search Basic service each) and takes about 15 minutes per person, so do it **before** the workshop day.

## Customers on Azure DevOps (no GitHub)

The lab does not require participant GitHub accounts, Codespaces or Copilot. Azure sign-in and the
Foundry permissions described below are still required.

1. In the customer's Azure DevOps project, select **Repos > Import repository** and enter
   `https://github.com/jamelachahbar/foundryagents-workshop-supportbuddy.git`.
2. Give participants the **Azure DevOps clone URL**, or distribute a ZIP of the repository through
   the customer's usual file-sharing channel.
3. On Windows, participants extract or clone the repository and run the
   [one-command Windows setup](../docs/workshop.md#windows-one-command-setup).

Use local laptops, **Microsoft Dev Box**, or an Azure VM. Codespaces requires GitHub accounts;
it is not needed here. If Docker is allowed, the included `.devcontainer` also works locally with
VS Code Dev Containers. **Copilot is optional**; `.github/skills` is only an extra for licence holders.

**Restricted networks:** ask IT to allow the tool downloads or pre-install uv, Python 3.12,
Azure CLI, azd and `microsoft.foundry`. The Windows script uses winget for missing CLI tools;
uv downloads Python when needed. Test on a customer machine before the workshop.
The [azd installer](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd)
uses `https://aka.ms/install-azd.ps1`, currently redirecting to
`https://azuresdkartifacts.z5.web.core.windows.net/azd/standalone/installer/install-azd.ps1`.
The [extension registry](https://learn.microsoft.com/azure/developer/azure-developer-cli/extensions/overview)
is `https://aka.ms/azd/extensions/registry`, redirecting to
`https://raw.githubusercontent.com/Azure/azure-dev/refs/heads/main/cli/azd/extensions/registry.json`;
extension downloads use `github.com` and can redirect to `objects.githubusercontent.com` or
`release-assets.githubusercontent.com`. GitHub download access does not require participant accounts.

If PyPI is blocked, IT must provide an **Azure Artifacts feed with a PyPI upstream** serving the
lab's pinned dependencies. Set its URL locally before running setup:

```powershell
$env:UV_INDEX_URL = '<customer Python simple-index URL>'
.\setup-windows.ps1
```

Use the customer's approved feed authentication. Do not commit feed URLs or credentials.

## Reusable scenario time

Every lab run replays the same synthetic snapshot: **18 November 2026, 10:00 UTC**
(`2026-11-18T10:00:00Z`). This fictional dataset date is independent of the customer and
workshop schedule. It works before or after that calendar date, for single-day or multi-day
sessions, without edits. This is intentional, not stale incident data:

- `INC-5001` started at 05:10 UTC and is open; its 14:00 UTC restore estimate is still ahead
  of the simulated current time, so the outage case answers without a handover.
- `INC-5002` was resolved at 07:25 UTC; it does not explain the conflicting-evidence case.
- Customer phrases such as "today", "this morning" and "yesterday" use the simulated date.
- Actual handover/action timestamps and real waiting periods, including the 3-minute restart wait,
  are unchanged. This is not a frozen operating-system clock.

Before the workshop:

1. Distribute this updated source checkout to participants. `lab_agent.load_instructions` injects
   **Synthetic scenario time** into all code-first stages, the contact-centre agents, evaluations
   and the hosted agent; reference solutions need no edits.
2. Restart any running local agents and contact centres, and start **New Conversation** for each
   independent scenario. No incident-date edits or system-clock changes are needed for any session.
3. If a Stage 10 prompt agent already exists, republish it from the updated source with
   `uv run python prompt_agent.py create` from `src/` (add `--with-functions` only for that experiment).
   Publication creates a new version and replaces its instructions; preserve any intentional portal
   edits separately and reapply them without removing **Synthetic scenario time**.
4. If a Stage 14 hosted agent is already deployed, redeploy its updated application code using the
   Stage 14 procedure. A local source edit does not update an existing deployment.
5. Dry-run the outage, conflicting-evidence and modem scenarios. Check that the outage reports
   **18 November 2026, 14:00 UTC**, does not treat the estimate as overdue, and does not suggest a restart.
   The conflicting-evidence case must still hand over; modem actions must still require approval.

The backend fixtures and runbooks are unchanged: no knowledge-base reindex, shared network MCP
redeployment, toolbox recreation or Azure resource changes are required for this clock update.
Existing memory stores are not reset; use fresh test conversations and avoid carrying over misleading
date claims from earlier dry runs.

## Option A: one command (recommended)

### What you need

- An Azure subscription where you are **Owner**, or **Contributor** plus **User Access Administrator**
  (the setup creates role assignments).
- Model quota in the region you pick: `model-router` (version `2025-11-18`) and
  `text-embedding-3-small`, **Global Standard** by default. The router can also use **Data Zone Standard**
  where supported; check the quota for that SKU before switching.
  Check it in the Foundry portal (Quota) or with `az cognitiveservices usage list -l <region>`.
- On your machine: [Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd)
  (`azd`), [uv](https://docs.astral.sh/uv/) and the Azure CLI. Docker is **not** needed: the MCP server image
  is built in Azure Container Registry.

### Run it

```bash
cd instructor
az login                       # add --tenant <tenant-id> for another tenant
azd auth login                 # same account and tenant
azd env new supportbuddy-lab   # any name; it also names the resource group rg-<name>
azd env set AZURE_LOCATION swedencentral

# Optional settings (see the table below), then:
azd up
```

`azd up` asks for the subscription, then:

1. **Provisions** (`infra/`, Bicep): a Foundry resource and project, the chat and embedding deployments,
   the lab guardrail, Azure AI Search, a container registry, a Container Apps environment with the
   network MCP server app, Log Analytics, workspace-based Application Insights, and the role assignments.
   The monitoring resources are also provisioned when the network MCP server is external or disabled.
2. **Finishes the setup** (`setup_lab.py`): the toolbox connections, the shared Application Insights
   project connection, the knowledge base with the
   runbooks, and the memory store. It waits while new role assignments propagate.
3. **Deploys** the network MCP server: builds `src/mcp_server` in the registry and runs it.

At the end it prints the values to give the participants: the lines for their `src/.env`, the guardrail
ID (Stage 13) and the project resource ID (Stage 14). Print them again at any time with
`uv run --project ../src python setup_lab.py` (it is safe to run again).

Application Insights is instructor-provisioned once per environment, not created by participants.
The handout enables prompt-agent client tracing with `SUPPORTBUDDY_TRACING=true` and leaves chat/tool
content capture off with `SUPPORTBUDDY_TRACE_CONTENT=false`. Participants need no connection string:
the client resolves the shared project connection using `az login`. The setup hook transfers the
connection value directly from Azure to Foundry without printing it or writing it into the handout.
Resource IDs, not credentials, are exported to the azd environment.

Logs use 30-day retention; ingestion and storage incur Azure Monitor charges. Everyone granted the
shared monitoring roles can inspect the group's traces: use synthetic lab data only. Opt into payload
capture only for a controlled debugging exercise, and restart clients after changing tracing settings.

### Optional settings

Set these with `azd env set <NAME> <value>` before `azd up`.

| Setting | Default | Use it when |
| --- | --- | --- |
| `PARTICIPANTS_GROUP_ID` | none | You have a Microsoft Entra group with all participants. The group gets their roles (see below). |
| `AZURE_SEARCH_LOCATION` | same as `AZURE_LOCATION` | Azure AI Search has no capacity in your region (`InsufficientResourcesAvailable`; seen in swedencentral, francecentral works). |
| `EXTERNAL_NETWORK_MCP_URL` | none | Container Apps is not possible in this subscription. See [If Container Apps is not possible](#if-container-apps-is-not-possible). |
| `AZURE_RESOURCE_TAGS` | none | Your policies require tags. Format: `name=value;name=value`, for example `SecurityControl=Ignore;owner=jane`. |
| `CHAT_MODEL_NAME`, `CHAT_MODEL_VERSION` | `model-router`, `2025-11-18` | Use another supported model/version. When changing models, also choose an appropriate deployment name. |
| `CHAT_MODEL_DEPLOYMENT_NAME` | `model-router` | Name exported as `AZURE_AI_MODEL_DEPLOYMENT_NAME` in the participant handout. |
| `CHAT_MODEL_SKU` | `GlobalStandard` | `DataZoneStandard` for routing within the deployment's data zone (EU for an EU region), where supported and quota is available. |
| `CHAT_MODEL_ROUTING_MODE` | `balanced` | `quality` or `cost`. This is a shared deployment setting, not a participant prompt/reasoning setting; ignored for non-router models. |
| `CHAT_MODEL_CAPACITY` | `100` | Thousands of tokens per minute, shared by everyone. Count about 10 per participant. |

With a model that does not support reasoning, participants set `SUPPORTBUDDY_REASONING_EFFORT=off` in `.env`.

The default matches the workshop's existing `model-router` deployment: model version `2025-11-18`,
**Global Standard**, **balanced** routing and no restricted model subset. Fresh environments use the
capacity above; changing these source defaults does not reprovision an existing environment or replace
its saved azd settings. Existing deployments and local participant configuration remain unchanged until
the instructor deliberately updates them.

The template uses the `2025-10-01-preview` deployment API for the router's `routing.mode` setting.
It deliberately omits `routing.models`: Azure then routes across all available models in that router
version, rather than a hard-coded subset that can become stale. Underlying models do not need separate
deployments, **except Claude**: Anthropic models require matching deployments in the same Foundry account
and SKU before you explicitly select them. This lab does not provision Claude or accept extra provider
terms on your behalf. To limit the pool, configure a supported subset in Foundry and manage that change
in your deployment template; do not put routing policy in participant instructions.

For example, set `CHAT_MODEL_ROUTING_MODE` to `quality` or `cost` with `azd env set`, then apply with
`azd provision`. Allow up to five minutes for routing changes to take effect. Check saved model/version,
deployment-name and SKU overrides before reprovisioning an older environment. See
[Model router deployment settings](https://learn.microsoft.com/azure/foundry/openai/how-to/model-router#optional-customize-deployment-settings).

### If Container Apps is not possible

The network MCP server is the only part Foundry must reach over the internet: from Stage 9 the
toolbox, the prompt agent (Stage 10) and the hosted agent (Stage 14) run in Foundry and cannot call a
laptop. If a policy blocks Container Apps or container registries, pick one:

| Choice | Setting | Then | What participants notice |
| --- | --- | --- | --- |
| Use a copy that already runs elsewhere (for example the instructor's) | `azd env set EXTERNAL_NETWORK_MCP_URL https://<app>.azurecontainerapps.io/mcp` | `azd provision` (not `azd up`: there is nothing to deploy) | Nothing: every stage works. The server holds fictional data only and needs no credentials. |
| No network MCP server on Azure | `azd env set EXTERNAL_NETWORK_MCP_URL none` | `azd provision` | Participants add `NETWORK_TOOLS=local` to `.env` (the handout says so). Their toolbox holds the runbooks only and the network tools stay on their laptop, as in Stage 6. Stages 10 and 14 run without network tools: the agent can still cite runbooks but cannot diagnose the line. |

A dev tunnel to each laptop also works technically, but it opens the laptop to the internet and
customer networks usually block it.

### Participant roles

With `PARTICIPANTS_GROUP_ID` set, the group gets these automatically. Otherwise assign them yourself (or
add people to the group later; nothing has to be redeployed).

| Role | Scope | For |
| --- | --- | --- |
| Foundry User | Foundry resource | Stages 1-13 |
| Search Index Data Reader | Search service | Stage 8 |
| Foundry Project Manager | Foundry resource | Stage 14 (deploy a hosted agent) |
| Monitoring Metrics Publisher | Application Insights | Send traces with the signed-in identity (Stage 10) |
| Monitoring Reader | Application Insights | View the shared monitoring resource and traces |
| Log Analytics Reader | Log Analytics workspace | Query the shared lab's logs |

Guests from another tenant need to be invited into the tenant first, and sign in with
`az login --tenant <tenant-id>`. The person who ran `azd up` gets all the roles above.

### Clean up

```bash
cd instructor
azd down --purge
```

This deletes the resource group with everything participants created in it (toolboxes, prompt and
hosted agents, memories, evaluation runs) and purges the Foundry resource so its name and quota are free
again.

## Option B: by hand, in an existing Foundry project

Use this when the customer already has a Foundry project to use. You need: Owner (or equivalent) on the
resource group, **Foundry Project Manager** on the project, **Search Service Contributor** and **Search
Index Data Contributor** on the search service, the Azure CLI, `azd` with `azd ext install microsoft.foundry`,
and the lab's `src/.env` filled in.

### 1. Model deployments

- A `model-router` deployment (version `2025-11-18`, Global Standard or Data Zone Standard) for the agents
  and the Foundry evaluators. Use **balanced** routing and the unrestricted available model pool by
  default; **quality** and **cost** are optional shared deployment profiles. Use the existing deployment
  name in `AZURE_AI_MODEL_DEPLOYMENT_NAME` rather than creating a duplicate.
- An embedding model (for example `text-embedding-3-small`) for Foundry Memory.

### 2. Foundry IQ knowledge base (Stage 8)

An Azure AI Search service (Basic or above, semantic ranker enabled). The instructor runs this
**once per shared environment**, from `src` (personal-environment owners do the same for their own lab):

```bash
uv run python knowledge_base.py create
uv run python knowledge_base.py ask "modem not responding, line up"
```

Give all participants the same `AZURE_AI_SEARCH_ENDPOINT` and
`FOUNDRY_KNOWLEDGE_BASE_NAME` (default `supportbuddy-runbooks`). The knowledge base, its knowledge source
and its index are shared; `WORKSHOP_RESOURCE_PREFIX` does not scope them to a participant.
Participants run only `knowledge_base.py ask` to verify access, not `create`.
Rerunning `create` updates existing shared resources and uploads the runbooks again; its output
distinguishes **Already exists; updating...** from **Not found; creating...**.

Grant participants **Search Index Data Reader** on the search service (Stage 8 calls it with their
identity), and grant the **Foundry project's managed identity** the same role (Stage 9 onwards, through
the toolbox).

#### Optional: model-backed knowledge-base experiments

The [Stage 8 optional portal exercise](../docs/workshop.md#optional-portal-experiment-retrieval-effort-and-output-mode)
compares retrieval effort and output modes without changing the shared baseline.
The Bicep setup enables a **system-assigned managed identity on Search** and grants that identity
**Cognitive Services User** on the Foundry account. This prepares model access only: it does not attach
a model to `supportbuddy-runbooks`, change its `minimal` / `extractiveData` defaults, or create an
experimental knowledge base. Merely enabling the identity and role does not invoke a model.

Existing deployments need the updated infrastructure provisioned before this exercise works; a source
edit alone does not update Azure. For a manually managed environment, the administrator must enable
Search's system-assigned identity and assign **Cognitive Services User** on the Foundry resource that
hosts the selected model. This is separate from the project's identity reading Search.
Allow time for role propagation, and check model endpoint, deployment, regional availability and network
access if authentication or model calls still fail.

Before the session:

1. Confirm a chat model supported by the portal's knowledge-base model selector is deployed.
   The workshop's default agent model might not be selectable; consult the
   [portal's supported-model list](https://learn.microsoft.com/azure/search/get-started-portal-agentic-retrieval#supported-llms)
   before arranging any additional deployment, quota or cost approval.
2. Keep the standard participant role at **Search Index Data Reader**. Run configuration changes as
   an instructor with **Search Service Contributor** and read access to Search, plus access to select
   the Foundry deployment. Do not grant the whole participant group write access just for this exercise.
3. Use a separate base named `lab-<alias>-runbooks-experiment`, referencing the existing
   `supportbuddy-runbooks-source`. Do not create another index, re-upload runbooks or change the toolbox
   connection. The experiment shares the same synthetic documents, not separate private content.
4. Confirm the sandbox can call its model before demonstrating different settings. Extra query planning
   and answer synthesis consume model tokens and shared deployment capacity.
5. Afterward, remove only the experimental base if no longer needed. Keep the shared source, index,
   baseline base and toolbox connection.

See Microsoft's [identity and role prerequisites](https://learn.microsoft.com/azure/search/search-get-started-agentic-retrieval#configure-access).

### 3. Network MCP server on Azure Container Apps (Stage 9)

Foundry has to reach the MCP server, so one shared copy runs on Azure. It serves fictional, read-only
data and has no authentication. You need a container registry, a Container Apps environment and a
managed identity with **AcrPull** on the registry (Option A creates all three). From `src`:

```bash
az acr build -r <registry> -t supportbuddy-network-mcp:1 -f mcp_server/Dockerfile .
az containerapp create -g <rg> -n supportbuddy-network-mcp --environment <container-apps-env> \
  --image <registry>.azurecr.io/supportbuddy-network-mcp:1 \
  --registry-server <registry>.azurecr.io --registry-identity <pull-identity-id> --user-assigned <pull-identity-id> \
  --ingress external --target-port 8000 --min-replicas 1 --max-replicas 2 --cpu 0.25 --memory 0.5Gi
```

In a policy-restricted subscription, add the tags your policies require. For a production MCP server,
put it behind Azure API Management with Microsoft Entra ID authentication instead.

### 4. Project connections used by the toolbox (Stage 9)

```bash
azd ai project set <project endpoint>

azd ai connection create supportbuddy-network-mcp --kind remote-tool \
  --target "https://<container-app-fqdn>/mcp" --auth-type none

azd ai connection create supportbuddy-runbooks-kb --kind remote-tool \
  --target "https://<search>.search.windows.net/knowledgebases/supportbuddy-runbooks/mcp?api-version=2026-08-01-preview" \
  --auth-type project-managed-identity --audience "https://search.azure.com/"
```

`toolbox.py` refers to these two connection names.

#### Shared Application Insights (Stage 10)

The instructor also creates a workspace-based Application Insights resource in the lab resource group,
reusing its Log Analytics workspace. Keep both even if the network MCP server runs elsewhere.
Under the Foundry project's **Observability > Traces** settings, connect that Application Insights
resource. Assign the monitoring roles in the table above to the instructor and participants.
Give participants `SUPPORTBUDDY_TRACING=true` and `SUPPORTBUDDY_TRACE_CONTENT=false` for their `src/.env`;
do not distribute the connection string.

With Option A these steps are automatic, including the `supportbuddy-appinsights` project connection.
For an existing environment, the setup hook requires `APPLICATIONINSIGHTS_RESOURCE_ID` alongside the
other required azd values and refuses to report success if tracing cannot be connected.
See the [Stage 10 tracing exercise](../docs/workshop.md#optional-trace-the-prompt-agent-and-approval-flow)
for a fresh-conversation verification and troubleshooting queries.

### 5. Foundry Memory store (Stage 11)

```bash
uv run python memory_store.py create
```

Foundry Memory calls the embedding and chat deployments with managed identities: grant **Cognitive Services
OpenAI User** on the Foundry resource to both the **project's** and the **Foundry resource's** managed identity.
Without it, memory searches and updates fail with `401` and the agent silently remembers nothing.

### Evaluation preparation (Stage 12)

Reserve Azure usage for live agent runs and additional Foundry quality judges. Participants keep
their Stage 11 identity/memory and Stage 9 toolbox; evaluation does not provision new agent capabilities.

- Use the [Stage 12 task list](../docs/workshop.md#stage-12-evaluation): baseline, one new quality case
  with `ground_truth`, published-definition comparison, then a measured prompt improvement.
- Worked references are `src/solutions/stage_12_extra_case.jsonl` and
  `src/solutions/stage_12_eval_improvement.*`; see the [improvement guide](../docs/evaluation-improvement.md).
- Additional harmful-content scoring and fixed-sample scorer calibration are optional. A red unsafe
  calibration control is expected detection, not a SupportBuddy failure.
- Record outstanding safety findings for Stage 13; do not mistake missing/skipped scores for passes.
  New sessions do not reset memory or synthetic handovers, so account for state during comparisons.

### 6. Guardrail (Stage 13)

`guardrail.json` is the default policy plus **Indirect Attack** detection.

```bash
az rest --method put \
  --url "https://management.azure.com<foundry-account-resource-id>/raiPolicies/supportbuddy-guardrail?api-version=2025-06-01" \
  --body @instructor/guardrail.json
```

Replace `<foundry-account-resource-id>` with the account ID including its leading `/`.
Give participants the policy's full resource ID: `<foundry-account-resource-id>/raiPolicies/supportbuddy-guardrail`.
Participants attach this existing policy to their toolbox and merge middleware into their own agent;
they should not overwrite the prompt improvements they measured in Stage 12.
The [Stage 13 checkpoint](../docs/workshop.md#stage-13-guardrails) requires an offline proof of their
actual middleware wiring, the live safety cases and a full regression. A safe model reply alone
does not identify the layer that prevented unsafe behavior.

### 7. Participant roles and clean up

Assign the roles in the table under Option A. Each participant creates, in the shared project: a toolbox
`lab-<alias>-supportbuddy-tools`, a prompt agent `lab-<alias>-supportbuddy-prompt`, a hosted agent
`lab-<alias>-supportbuddy-hosted`, memories in scopes `lab-<alias>_CUST-*`, and evaluation runs.
Delete them, or delete the project, after the workshop.
