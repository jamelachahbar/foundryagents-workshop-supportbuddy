# Lab environment setup

Everything the participants use that they do not build themselves: the Foundry project and models, the
runbook knowledge base, the network MCP server, the toolbox connections, the memory store and the
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

## Option A: one command (recommended)

### What you need

- An Azure subscription where you are **Owner**, or **Contributor** plus **User Access Administrator**
  (the setup creates role assignments).
- Model quota in the region you pick: `gpt-5.4-mini` and `text-embedding-3-small`, **Global Standard**.
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
   network MCP server app, Log Analytics, and the role assignments.
2. **Finishes the setup** (`setup_lab.py`): the two toolbox connections, the knowledge base with the
   runbooks, and the memory store. It waits while new role assignments propagate.
3. **Deploys** the network MCP server: builds `src/mcp_server` in the registry and runs it.

At the end it prints the values to give the participants: the lines for their `src/.env`, the guardrail
ID (Stage 13) and the project resource ID (Stage 14). Print them again at any time with
`uv run --project ../src python setup_lab.py` (it is safe to run again).

### Optional settings

Set these with `azd env set <NAME> <value>` before `azd up`.

| Setting | Default | Use it when |
| --- | --- | --- |
| `PARTICIPANTS_GROUP_ID` | none | You have a Microsoft Entra group with all participants. The group gets their roles (see below). |
| `AZURE_SEARCH_LOCATION` | same as `AZURE_LOCATION` | Azure AI Search has no capacity in your region (`InsufficientResourcesAvailable`; seen in swedencentral, francecentral works). |
| `EXTERNAL_NETWORK_MCP_URL` | none | Container Apps is not possible in this subscription. See [If Container Apps is not possible](#if-container-apps-is-not-possible). |
| `AZURE_RESOURCE_TAGS` | none | Your policies require tags. Format: `name=value;name=value`, for example `SecurityControl=Ignore;owner=jane`. |
| `CHAT_MODEL_NAME`, `CHAT_MODEL_VERSION`, `CHAT_MODEL_SKU` | `gpt-5.4-mini`, `2026-03-17`, `GlobalStandard` | Another model or deployment type (for example `DataZoneStandard` to keep data in the EU). |
| `CHAT_MODEL_CAPACITY` | `100` | Thousands of tokens per minute, shared by everyone. Count about 10 per participant. |

With a model that does not support reasoning, participants set `SUPPORTBUDDY_REASONING_EFFORT=off` in `.env`.

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

- A chat model (for example `gpt-5.4-mini`) for the agents and the Foundry evaluators.
- An embedding model (for example `text-embedding-3-small`) for Foundry Memory.

### 2. Foundry IQ knowledge base (Stage 8)

An Azure AI Search service (Basic or above, semantic ranker enabled). Then, from `src`:

```bash
uv run python knowledge_base.py create
uv run python knowledge_base.py ask "modem not responding, line up"
```

Grant participants **Search Index Data Reader** on the search service (Stage 8 calls it with their
identity), and grant the **Foundry project's managed identity** the same role (Stage 9 onwards, through
the toolbox).

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

### 5. Foundry Memory store (Stage 11)

```bash
uv run python memory_store.py create
```

Foundry Memory calls the embedding and chat deployments with managed identities: grant **Cognitive Services
OpenAI User** on the Foundry resource to both the **project's** and the **Foundry resource's** managed identity.
Without it, memory searches and updates fail with `401` and the agent silently remembers nothing.

### 6. Guardrail (Stage 13)

`guardrail.json` is the default policy plus **Indirect Attack** detection.

```bash
az rest --method put \
  --url "https://management.azure.com/<foundry account resource ID>/raiPolicies/supportbuddy-guardrail?api-version=2025-06-01" \
  --body @instructor/guardrail.json
```

Give participants the policy's full resource ID: `<foundry account resource ID>/raiPolicies/supportbuddy-guardrail`.

### 7. Participant roles and clean up

Assign the roles in the table under Option A. Each participant creates, in the shared project: a toolbox
`post-<alias>-supportbuddy-tools`, a prompt agent `post-<alias>-supportbuddy-prompt`, a hosted agent
`post-<alias>-supportbuddy-hosted`, memories in scopes `post-<alias>_CUST-*`, and evaluation runs.
Delete them, or delete the project, after the workshop.
