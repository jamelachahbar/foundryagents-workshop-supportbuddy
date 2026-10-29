---
name: supportbuddy-lab-troubleshoot
description: 'Diagnose errors while running the SupportBuddy Microsoft Foundry workshop. Use when a participant reports: an error or traceback, DevUI Authentication Required or 401, invalid_host in Codespaces, port in use, connection refused to 127.0.0.1:8000, 401/403/404/429 from Foundry, toolbox or knowledge base not found, az login or azd problems, the agent ignores my changes, or uv sync failures.'
argument-hint: 'Paste the error and the command you ran'
---

# SupportBuddy lab troubleshooting

Find the cause of a lab problem with the least invasive checks, then give the participant one clear fix.
The full symptom table is the **Troubleshooting** section of [docs/workshop.md](../../../docs/workshop.md).

## Ground rules

- Never open, print or paste `src/.env`, `src/.devui_token`, access tokens or keys. To check a setting exists,
  test for the variable name without showing its value.
- Prefer read-only checks. Ask before anything that changes state: creating or deleting Azure resources,
  publishing agents or toolboxes, killing processes, changing port visibility, or running evaluations.
- Never change package pins in `src/pyproject.toml` or set Codespaces ports to Public.
- Do not "fix" a participant's exercise files beyond the reported problem.

## Procedure

1. **Collect facts.** The exact command, its working directory, the full error, and the participant's stage.
   If the stage is unknown, detect it with the stage map in
   [supportbuddy-lab-coach](../supportbuddy-lab-coach/references/stage-map.md).
2. **Check the stage first.** Many errors come from running a later stage's command before its prerequisite
   exists. Example: a 404 on `.../toolboxes/lab-<alias>-supportbuddy-tools/mcp` before Stage 9 means the
   toolbox has not been created yet, not that Foundry is broken.
3. **Match a known cause** (table below, then the workshop's Troubleshooting table).
4. **Confirm with one safe check** before proposing a fix.
5. **Fix and verify.** Give the single command or edit, then the observation that proves it worked.

## Common causes

| Symptom | Likely cause | Safe check | Fix |
| --- | --- | --- | --- |
| Agent ignores edited code or instructions | Agent loads files only at start | Was the process restarted? | Ctrl+C, rerun `uv run python main.py` from `src/`, **New Conversation** in DevUI |
| `No module named 'lab_agent'` / `'solutions'` / `'mcp_server'` | Wrong directory | `pwd` | `cd src` |
| DevUI **Authentication Required** / initial `401` on `/meta` | Expected before the token is entered | none | Open `src/.devui_token` in the editor (Ctrl+P) and paste its value into DevUI; do not paste it into chat |
| Codespaces `invalid_host` / `host_not_allowed` | DevUI bound to loopback | `echo $CODESPACES $DEVUI_HOST` | Unset `DEVUI_HOST` (or `0.0.0.0`), restart, open port 8090 from the **Ports** tab, keep it **Private** |
| `address already in use` on 8090 / 8000 / 8070 | Previous run still alive | `lsof -i :<port>` | Ctrl+C the old terminal, or ask before killing the PID |
| Connection refused `127.0.0.1:8000` (Stages 6-8) | MCP server not running | `lsof -i :8000` | Second terminal in `src/`: `uv run python -m mcp_server.server` |
| Foundry `401`/`403` | Expired sign-in or missing **Foundry User** role | `az account show --query user.name -o tsv` | `az login --tenant <tenant>` (Codespaces: add `--use-device-code`); ask the instructor for the role |
| `AzureCliCredential.get_token failed: Timed out` | Slow Azure CLI | `az account get-access-token -o none` | Run it once, retry |
| `429 Too Many Requests` | Shared model rate limit | none | Wait a minute, retry |
| Knowledge base `404` (Stage 8) | Wrong endpoint/name or knowledge base not created | `uv run python knowledge_base.py ask "test"` | Shared environment: confirm endpoint/name with the instructor and ask them to check setup; do not run `create`. Personal environment only: `uv run python knowledge_base.py create` |
| Knowledge base `401`/`403` | Missing **Search Index Data Reader** | none | Ask the instructor; role assignments take minutes |
| Toolbox URL `404` / `Session terminated` | Toolbox not created (before Stage 9) or wrong prefix | stage map; `WORKSHOP_RESOURCE_PREFIX` name present in `.env` | Finish Stage 9: `uv run python toolbox.py create` |
| `prompt_agent.py ask` → `not found` | Prompt agent not published | none | `uv run python prompt_agent.py create` |
| `uv sync`: no matching `agent-framework-core==1.22.0` | Package mirror lacks the pin | Read the `uv sync` error for the index URL | Ask the instructor for the package index; never change the pin |
| `Processor is already shutdown` | OpenTelemetry exporter shutdown noise | Did the answer still arrive? | Harmless if the run completed |

## Environment checks (read-only)

From `src/`:

- `uv run python preflight.py` - settings, sign-in and model access; expect four `[OK]` and `Ready for the lab.`
- `az account show -o table` - signed-in tenant and subscription.
- `git status --short` - which lab files the participant changed.

## Escalate to the instructor

Missing roles, missing shared resources (project connections, network MCP server on Azure, guardrail,
memory store embedding deployment), quota or region capacity errors, and Conditional Access blocks on
device-code sign-in. Say which resource or role is missing and include the error text without secrets.
