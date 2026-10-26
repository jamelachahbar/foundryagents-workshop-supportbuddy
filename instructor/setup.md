# Instructor setup

Shared resources the participants use from Stage 8 onwards. Create them **once** per workshop, in the
Foundry project the participants use. Everything here is synthetic training data.

You need: Owner (or equivalent) on the resource group, **Foundry Project Manager** on the project,
**Search Service Contributor** and **Search Index Data Contributor** on the search service, the Azure CLI,
`azd` with `azd ext install microsoft.foundry`, and the lab's `src/.env` filled in.

## 1. Model deployments

- A chat model (for example `gpt-5.4-mini`) for the agents and the Foundry evaluators. The lab asks for a
  medium reasoning effort; with a model that does not support reasoning, participants set
  `SUPPORTBUDDY_REASONING_EFFORT=off` in `.env`.
- An embedding model (for example `text-embedding-3-small`) for Foundry Memory.

## 2. Foundry IQ knowledge base (Stage 8)

An Azure AI Search service (Basic or above, semantic ranker enabled). Then, from `src`:

```bash
uv run python knowledge_base.py create
uv run python knowledge_base.py ask "modem not responding, line up"
```

Grant participants **Search Index Data Reader** on the search service (Stage 8 calls it with their
identity), and grant the **Foundry project's managed identity** the same role (Stage 9 onwards, through
the toolbox).

## 3. Network MCP server on Azure Container Apps (Stage 9)

Foundry has to reach the MCP server, so one shared copy runs on Azure. It serves fictional, read-only
data and has no authentication. From `src`:

```bash
az acr build -r <registry> -t supportbuddy-network-mcp:1 -f mcp_server/Dockerfile .
az containerapp create -g <rg> -n supportbuddy-network-mcp --environment <container-apps-env> \
  --image <registry>.azurecr.io/supportbuddy-network-mcp:1 \
  --registry-server <registry>.azurecr.io --registry-identity <pull-identity-id> --user-assigned <pull-identity-id> \
  --ingress external --target-port 8000 --min-replicas 1 --max-replicas 2 --cpu 0.25 --memory 0.5Gi
```

In a policy-restricted subscription, add the tags your policies require. For a production MCP server,
put it behind Azure API Management with Microsoft Entra ID authentication instead.

## 4. Project connections used by the toolbox (Stage 9)

```bash
azd ai project set <project endpoint>

azd ai connection create supportbuddy-network-mcp --kind remote-tool \
  --target "https://<container-app-fqdn>/mcp" --auth-type none

azd ai connection create supportbuddy-runbooks-kb --kind remote-tool \
  --target "https://<search>.search.windows.net/knowledgebases/supportbuddy-runbooks/mcp?api-version=2026-08-01-preview" \
  --auth-type project-managed-identity --audience "https://search.azure.com/"
```

`toolbox.py` refers to these two connection names.

## 5. Foundry Memory store (Stage 10)

```bash
uv run python memory_store.py create
```

## 6. Guardrail (Stage 12)

`guardrail.json` is the default policy plus **Indirect Attack** detection.

```bash
az rest --method put \
  --url "https://management.azure.com/<foundry account resource ID>/raiPolicies/supportbuddy-guardrail?api-version=2025-06-01" \
  --body @instructor/guardrail.json
```

Give participants the policy's full resource ID: `<foundry account resource ID>/raiPolicies/supportbuddy-guardrail`.

## 7. Participant roles

| Role | Scope | For |
| --- | --- | --- |
| Foundry User | Foundry project | Stages 1-13 |
| Search Index Data Reader | Search service | Stage 8 |
| Foundry Project Manager | Foundry project | Stage 14 (deploy a hosted agent) |

## Clean up

Each participant creates, in the shared project: a toolbox `post-<alias>-supportbuddy-tools`, a prompt
agent `post-<alias>-supportbuddy-prompt`, a hosted agent `post-<alias>-supportbuddy-hosted`, memories in
scopes `post-<alias>_CUST-*`, and evaluation runs. Delete them, or delete the project, after the workshop.
