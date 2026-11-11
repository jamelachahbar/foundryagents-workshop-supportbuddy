# Participant Terminal Cheatsheet

Keep this page nearby when reopening terminals. Each terminal has its own working directory and environment variables; a running server stops when its terminal closes.

For first-time installation, `.env` values and preflight, follow [Setup and preflight](workshop.md#setup-and-preflight).

## Windows: Every New PowerShell Terminal

Open PowerShell in the repository root (the folder containing `setup-windows.ps1`). If the prompt shows `src`, close that terminal and open a new one at the repository root. Replace the venv name with the value from your setup output:

```powershell
if (-not (Test-Path '.\setup-windows.ps1')) {
  throw 'Open PowerShell in the repository root first.'
}
$env:UV_PROJECT_ENVIRONMENT = Join-Path $PWD 'src\.venv-windows'
if (-not (Test-Path (Join-Path $env:UV_PROJECT_ENVIRONMENT 'Scripts\python.exe'))) {
  throw 'Project venv not found. Check the venv name and setup output.'
}
uv run --directory src --no-sync python -c "import sys; print(sys.executable)"
```

The last command should print the Python executable inside the project venv. You do not need to activate the venv. Reapply these settings in each new PowerShell window; do not point Windows at the devcontainer's Linux `.venv`.

## macOS, Linux, or Dev Container

From the repository root, synchronize once after setup, then run each command from `src/`:

```bash
cd src
uv sync --python 3.12
```

In each new terminal, return to `src/` and use `uv run`; uv selects the project's `.venv` automatically. Do not share a Windows venv with Linux or a dev container.

## Which Terminals To Run

Start only the processes needed for your stage. Keep PowerShell in the repository root; run each local process in its own terminal and leave server terminals open while using the client.

| Terminal | Command | When to use |
| --- | --- | --- |
| Local network MCP server, port 8000 | `uv run --directory src --no-sync python -m mcp_server.server` | Stages 6–8; also later when your `.env` sets `NETWORK_TOOLS=local`. |
| Code-first agent with DevUI, port 8090 | `uv run --directory src --no-sync python main.py` | Stages 1-9 and 11-13. |
| Contact centre, port 8070 | `uv run --directory src --no-sync python -m contact_centre` | Stage 7 and later contact-centre exercises. Open `/chat` for customer chat and `/desk` for the advisor desk. |
| Stage 14 local Responses server, port 8088 | `uv run --directory src --no-sync python hosted_agent.py` | Only for a local hosted-agent protocol test; keep it running in one terminal. |
| Stage 14 local invoke | `azd ai agent invoke --local --new-conversation "I'm CUST-1004, my connection keeps dropping"` | In a second terminal after starting the local Responses server. |
| Stage 14 deployed hosted agent | `azd ai agent invoke lab-<alias>-supportbuddy-hosted --new-session --new-conversation "Hi, I'm CUST-1002, my internet is down"` | Calls Foundry-managed compute. No local agent server is needed. |

For the Stage 14 contact-centre variant, run `uv run --directory src --no-sync python -m contact_centre hosted_agent` in its own terminal instead of starting `hosted_agent.py`.

## Sign-In And Settings

- `src/.env` persists between terminals. It contains project endpoints and names, not keys or passwords.
- Azure CLI sign-in usually persists; run `az login --tenant <tenant-id>` again only if it expires or preflight reports an authentication error.
- For Stage 14 deployment, use `azd auth login` or the instructor's configured Azure CLI auth method. Do not run `azd up` just to resume a local stage.
- If a port is already in use, stop the old server with **Ctrl+C** or choose a free port, such as setting `$env:DEVUI_PORT = '8091'` before starting DevUI.
- After editing code or instructions, restart the affected local server. For deployed Stage 14 code, run `azd deploy` from `src/` again.
