# Optional lab: secure the network MCP server behind the toolbox

**Require a Microsoft Entra token for the shared MCP server, then decide per agent who may call it.**

| | |
| --- | --- |
| Placement | Pattern A after Stage 9 (instructor demo). Pattern B after Stage 14 (needs a hosted agent). Pattern C is discussion only. |
| Duration | A: about 15 minutes. B: about 30 minutes, plus waiting time for role changes (see [Before you start](#before-you-start)). |
| Who runs it | The instructor, or the customer's Azure admin. Participants only run `toolbox.py` and watch the results. |
| Azure changes | One app registration in Entra ID; Container Apps authentication on `supportbuddy-network-mcp`; the toolbox connection's authentication type. All reversible with `secure_mcp.py disable`. |
| Code changes | None in the numbered stages. The server's role check (`src/mcp_server/access.py`) is off unless `NETWORK_MCP_REQUIRED_ROLE` is set. |
| Deliverable | The MCP endpoint refuses anonymous calls (`401`), refuses callers without the role (`403`), and still works through the toolbox. |

The lab default stays as it is: the shared server has **no authentication**, because it only serves
synthetic, read-only data. This page shows what you would do for a real tool.

## How authentication works behind the toolbox

There are two separate hops, and each one is checked by someone else:

```text
agent ──(1) Entra token for ai.azure.com + Foundry role──▶ Foundry toolbox ──(2) token from the connection──▶ MCP server
```

1. **Agent → toolbox.** Foundry checks the caller's token and its role on the project
   (for example *Foundry Agent Consumer*). You already have this from Stage 9.
2. **Toolbox → MCP server.** The toolbox gets a token using the **connection's** authentication type and
   sends it to the server. *"Authentication is a property of the connection, not code in your agent"*
   ([toolbox overview](https://learn.microsoft.com/azure/foundry/agents/concepts/toolbox-overview)).
   The agent never sees that token.

This lab secures hop 2. Container Apps built-in authentication checks the token (signature, issuer,
audience) **before** the request reaches the container. The server can then check *what* the caller may do.

## Which pattern, and why

| Pattern | Who the MCP server sees | Connection auth type | Official guidance | Use in this lab |
| --- | --- | --- | --- | --- |
| **A. Project identity** | The Foundry project's managed identity, the same for every agent | `ProjectManagedIdentity` | For *"service-to-service calls with no user context"* ([MCP authentication](https://learn.microsoft.com/azure/foundry/agents/how-to/mcp-authentication)) | Step A: first step, one command |
| **B. Agent identity + app role** | Each published or hosted agent, with its own identity | `agentic-identity` | For *"per-agent audit and least privilege"* (same page); *"After you publish an agent, the agent gets a unique agent identity"* ([agent identity](https://learn.microsoft.com/azure/foundry/agents/concepts/agent-identity)) | Step B: **recommended** for shared service tools |
| **C. On behalf of the user** | The signed-in customer or advisor | `AgentOnBehalfOfUser` (preview) | When the tool must apply **the user's** permissions | Discussion only (see [Pattern C](#pattern-c-on-behalf-of-the-user-discussion)) |

Why B is the recommendation for SupportBuddy's network tools: the data belongs to the company, not to
the customer, so the question is *"which agent may call this?"*, not *"which user is this?"*. With A,
every agent in the project gets the same access. With B, you give one role to the agents that need it,
and the server logs which agent called.

Rules from the [MCP security best practices](https://modelcontextprotocol.io/specification/2025-06-18/basic/security_best_practices)
that this lab follows:

- The server must not accept tokens that were not issued for it: the **audience** is checked (`api://<app ID>`).
- Every request is checked; no session is used for authentication (the server is stateless).
- The server decides what the caller may do (the **app role**), and logs the caller's IDs, never a token.
- Keep permissions small: one read-only role, `Network.Read`.

## Before you start

- Run all commands from the `instructor` folder, after `azd up` and `setup_lab.py`.
- You need **Owner or Contributor** on the resource group, and the right to **create app registrations**
  in Entra ID (or an admin who does it with you).
- Each command shows what it changes. Run `status` first, read it, then run `enable`.
- Do this **before** the session if you can. A managed identity can keep a token issued before a role
  change for up to 24 hours ([managed identity best practices](https://learn.microsoft.com/entra/identity/managed-identities-azure-resources/managed-identity-best-practice-recommendations)),
  so a role you just gave may not work straight away.
- Re-running `azd provision` or `setup_lab.py` resets the toolbox connection to no authentication.
  Run `secure_mcp.py enable` (with the same options) again afterwards.

```bash
cd instructor
uv run --project ../src python secure_mcp.py status
```

## Step A: only the Foundry project may call the server (after Stage 9)

```bash
uv run --project ../src python secure_mcp.py enable
```

What it does, in order (the script prints each step):

1. Calls the endpoint with no token: `200`, anyone can call it today.
2. Finds the project's managed identity.
3. Creates (or reuses) the app registration `supportbuddy-network-mcp` with the ID `api://<app ID>`.
4. Turns on Container Apps authentication: Entra token required, audience `api://<app ID>`,
   allowed client application = the project identity only.
5. Changes the toolbox connection to `ProjectManagedIdentity` with that audience.
6. Calls the endpoint with no token again: now `401`.
7. Lists the toolbox's tools through Foundry (`toolbox.py show`).

**Check it worked**

- [ ] `curl -i -X POST <NETWORK_MCP_URL>` returns `401`.
- [ ] A participant runs `uv run python toolbox.py show` and still sees the `network___` tools.
- [ ] A participant's Stage 9 agent still diagnoses `CUST-1001` (Wi-Fi) normally.
- [ ] A participant using `NETWORK_TOOLS=local` is not affected (their server runs on the laptop).

**Talk about it:** every agent in the project now has the same access. Is that what you want for a
tool that can, one day, change something on a customer's line?

If `toolbox.py show` lists no `network___` tools, run `uv run python toolbox.py create` in `src`. If it
still fails, try `secure_mcp.py enable --token-version 2` (see [What is not verified yet](#what-is-not-verified-yet)).

## Step B: per-agent access with an app role (after Stage 14)

This step needs a **hosted agent** (Stage 14) or a published agent: only those have their own agent
identity. Before publishing, *"all agents in your Foundry project share the same agent identity"*
([agent identity](https://learn.microsoft.com/azure/foundry/agents/concepts/agent-identity)).
Agents running on the laptop always reach the server through the project identity.

### B1. Require the role (instructor)

```bash
uv run --project ../src python secure_mcp.py enable --roles
```

Compared with Step A, this:

- adds the app role **`Network.Read`** (for applications, not users) to the app registration;
- sets *role assignment required*, so Entra only gives tokens for this server to callers that have a role;
- gives the role to the **project identity**, so the existing toolbox keeps working;
- removes the *allowed client application* list (the role replaces it);
- sets `NETWORK_MCP_REQUIRED_ROLE=Network.Read` on the Container App. The server
  (`src/mcp_server/access.py`) reads the caller's claims from the `X-MS-CLIENT-PRINCIPAL` header that
  Container Apps adds after checking the token, and returns `403` when the role is missing.

### B2. Give one agent the role (instructor, with a participant)

Ask the participant for their hosted agent's identity object ID (Stage 14, step 6), or find it yourself:

```bash
azd ai agent show lab-<alias>-supportbuddy-hosted     # look for the instance identity principal ID
```

```bash
uv run --project ../src python secure_mcp.py grant-agent --principal-id <agent identity object ID>
```

This gives that identity `Network.Read`, then creates the connection `supportbuddy-network-mcp-agent`
with **agent identity** authentication and the same audience. Use `--no-connection` to only give the role.

### B3. Point the toolbox at the agent connection (participant)

```bash
cd src
uv run python toolbox.py create --network-connection supportbuddy-network-mcp-agent
```

Then test the hosted agent in the Foundry playground with `CUST-1001`. In the server's logs
(Container App → **Log stream**), you should see `allowed: app=<id> object=<agent identity> role=Network.Read`.

### B4. Take it away again

```bash
cd instructor
uv run --project ../src python secure_mcp.py revoke-agent --principal-id <agent identity object ID>
```

After the agent's cached token expires, its network calls return `403` and the log shows
`refused: ... lacks role Network.Read`. Give it back with `grant-agent`.

When you are done, go back to the shared connection: `uv run python toolbox.py create` in `src`.
Your laptop agents need that one, because only a hosted or published agent has an agent identity.

**Check it worked**

- [ ] No token: `401`. A token for another audience: `401`.
- [ ] The project identity (Stage 9 toolbox) still works: it has the role.
- [ ] The hosted agent works after `grant-agent`, and gets `403` after `revoke-agent`.
- [ ] The log has IDs only, never a token.

## How an instructor gives an agent identity a role

An agent identity is a service principal in Entra ID. You give it rights the same way as any app, but
there are **two different kinds of role**, and they are given in different places:

| Kind | What it controls | Where you give it | In this lab |
| --- | --- | --- | --- |
| **Azure role** (Azure RBAC) | Access to an Azure resource: the Foundry project, Search, Storage... | On the resource: **Access control (IAM)**, or `az role assignment create` | **Foundry User** on the project, so the agent can call models and the toolbox (Stages 10 and 14) |
| **App role** (Entra ID) | Access to *your own* API, which checks the role in the token | On the API's service principal in Entra ID, with Microsoft Graph | **Network.Read** on the MCP server (Step B) |

### 1. Find the identity's object ID

| Agent | How |
| --- | --- |
| Hosted agent (Stage 14) | `azd ai agent show <agent name>`: the instance identity principal ID |
| Published agent (prompt or hosted) | The agent application's `defaultInstanceIdentity`, or the identity shown on the published agent in the portal |
| Unpublished prompt agent (Stage 10) | Unpublished agents share the project's agent identity: one ID for all of them |
| Code-first agents on the laptop (Stages 1-13) | No agent identity: they use the participant's own `az login` |

Important: publishing creates a **new** identity, and *"permissions don't transfer automatically"*
([publish an agent](https://learn.microsoft.com/azure/foundry/agents/how-to/publish-agent)). Give the
roles again after you publish, or tool calls that worked in development fail with 401/403.

### 2. Give an Azure role (for example Foundry User on the project)

In the portal: open the Foundry project > **Access control (IAM)** > **Add role assignment** >
**Foundry User** > **Assign access to: User, group, or service principal** > **Select members** >
paste the object ID. Or with the CLI:

```bash
az role assignment create \
  --assignee-object-id <agent identity object ID> \
  --assignee-principal-type ServicePrincipal \
  --role "Foundry User" \
  --scope <FOUNDRY_PROJECT_ID>
```

`--assignee-principal-type ServicePrincipal` avoids a lookup in Entra ID that can fail for new identities.
For the hosted agent, `azd deploy` should already have done this: check **Role assignments** first.

### 3. Give an app role (for example Network.Read on the MCP server)

The Entra admin center can only assign app roles to users and groups, not to service principals, so you
use Microsoft Graph. The script does it for you:

```bash
cd instructor
uv run --project ../src python secure_mcp.py grant-agent --principal-id <agent identity object ID> --no-connection
```

What it runs, if you want to do it by hand:

```bash
MCP_SP=$(az ad sp list --display-name supportbuddy-network-mcp --query "[0].id" -o tsv)
ROLE_ID=$(az ad sp show --id $MCP_SP --query "appRoles[?value=='Network.Read'].id | [0]" -o tsv)
az rest --method POST \
  --url "https://graph.microsoft.com/v1.0/servicePrincipals/$MCP_SP/appRoleAssignedTo" \
  --body "{\"principalId\": \"<agent identity object ID>\", \"resourceId\": \"$MCP_SP\", \"appRoleId\": \"$ROLE_ID\"}"
```

Check it: `az rest --url "https://graph.microsoft.com/v1.0/servicePrincipals/$MCP_SP/appRoleAssignedTo"`,
or `secure_mcp.py status` (it lists who has `Network.Read`). You need the **Application Administrator**
or **Cloud Application Administrator** role in Entra ID (or ownership of the app), or an admin who does
it with you.

### 4. Wait, then test

New roles reach the agent only with a **new token**. A managed identity can keep its token for up to
24 hours, so give roles before the session when you can, and test with the server's log
(`allowed: ... role=Network.Read`) rather than by guessing.

## Pattern C: on behalf of the user (discussion)

Use it when the tool must apply **the user's own permissions**. In SupportBuddy, that is the advisor desk:
an advisor may only see customers from their own team, and the audit must name the advisor.

- The connection uses `AgentOnBehalfOfUser` (preview). Foundry exchanges the user's token for a token for
  the MCP server, so the server sees the user and the agent together.
- It needs consent in the tenant, a published agent, and a signed-in user in the app. A direct toolbox
  client such as our laptop agents cannot use it
  ([MCP authentication](https://learn.microsoft.com/azure/foundry/agents/how-to/mcp-authentication)).
- The server then checks the user's roles or groups, not the agent's.

It is not automated here: it needs preview features and tenant consent that a two-day lab in a customer
tenant should not depend on. Use it as a design question: *for each tool, whose permissions should apply?*

| Tool | Whose permissions? | Pattern |
| --- | --- | --- |
| `get_known_incidents` (public outage list) | The company's | A or B |
| `check_equipment`, `get_line_status` | The company's, per agent | B |
| A future "reset the router" action | The company's, plus a human approval (Stage 8) | B + approval |
| Advisor reads another team's customers | The advisor's | C |

## What is not verified yet

These points come from the documentation or from the tool's design; check them in your tenant before you
rely on them in front of a customer:

- **Token version and issuer.** Managed identities get v1 tokens by default (issuer
  `https://sts.windows.net/<tenant>/`), so the script uses that. If Foundry sends v2 tokens, use
  `--token-version 2`.
- **Roles in the principal header.** The server reads `roles` from `X-MS-CLIENT-PRINCIPAL`. If your
  tokens put roles under another claim name, the check returns `403`: look at the log line.
- **Which identity the toolbox presents** for an `agentic-identity` connection when the hosted agent
  calls the toolbox. If the log shows the project identity, the per-agent step does not apply in your setup.
- **Where to find the agent identity's object ID** in the portal can change between releases. The
  `azd ai agent show` field name comes from community write-ups: check the output in your tenant.
- **Agent identities in Microsoft Graph.** Agent identities are a newer kind of service principal. Check
  that `appRoleAssignedTo` accepts them in your tenant before the session (run `grant-agent` once).
- **`azd ai connection create` flags.** If that command fails, create the connection in the portal
  (project → **Connected resources**), with agent identity authentication and audience `api://<app ID>`.
- **Refresh delay.** The toolbox may keep a token for a while after you change a connection or a role.

## Clean up

```bash
cd instructor
uv run --project ../src python secure_mcp.py disable
```

This turns off Container Apps authentication, removes the role setting, and puts the toolbox connection
back to no authentication. The app registration `supportbuddy-network-mcp`, its role and its assignments
stay: delete them in Entra ID (**App registrations**) when the lab is over. Delete the connection
`supportbuddy-network-mcp-agent` in the Foundry portal if you created it.

## Going further (production, not in this lab)

- Put an API gateway (Azure API Management) in front of several MCP servers to check tokens, limit
  calls and log in one place.
- Use private networking so the MCP server is not on the internet at all.
- Give write tools their own role (for example `Network.Write`) and keep human approval for them.

Sources: [Foundry toolbox](https://learn.microsoft.com/azure/foundry/agents/concepts/toolbox-overview),
[MCP authentication in Foundry](https://learn.microsoft.com/azure/foundry/agents/how-to/mcp-authentication),
[agent identity](https://learn.microsoft.com/azure/foundry/agents/concepts/agent-identity),
[publish an agent](https://learn.microsoft.com/azure/foundry/agents/how-to/publish-agent),
[Container Apps with Entra ID](https://learn.microsoft.com/azure/container-apps/authentication-entra),
[app roles](https://learn.microsoft.com/entra/identity-platform/howto-add-app-roles-in-apps),
[MCP security best practices](https://modelcontextprotocol.io/specification/2025-06-18/basic/security_best_practices).
