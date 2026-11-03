# Workshop skills

Optional Copilot skills used while preparing and troubleshooting this lab. They are instructions and
reference material for a coding assistant, not tools loaded by SupportBuddy. The workshop works without
Copilot.

## Workshop skills

Written for this lab. They read your stage files and the workshop guide, coach rather than solve, and
never read `.env` or the DevUI token.

| Skill | Use it for | Example request |
| --- | --- | --- |
| [SupportBuddy lab coach](supportbuddy-lab-coach/SKILL.md) | Detect your stage, review checkpoints, hints, catching up | "Which stage am I at and what is missing?" |
| [SupportBuddy lab troubleshoot](supportbuddy-lab-troubleshoot/SKILL.md) | Errors while running the lab: sign-in, DevUI, ports, MCP, toolbox, knowledge base | "I get a 404 on the toolbox URL, why?" |

Type `/` in Copilot Chat to call one directly, for example `/supportbuddy-lab-coach check my Stage 5`.

## Included copies

| Skill | Use it for | Version |
| --- | --- | --- |
| [Azure AI](azure-ai/SKILL.md) | Search, retrieval, model APIs and other Azure AI services | 1.2.2 |
| [Azure Quotas](azure-quotas/SKILL.md) | Subscription quota checks and regional deployment planning | 1.2.2 |

Both folders are unchanged copies of the installed upstream skills on 2026-10-28. Their reference files
and scripts are included. Source: [Microsoft Azure skills](https://github.com/microsoft/azure-skills/tree/main/skills).
They are redistributed under the accompanying [MIT license](LICENSE). That license applies to these
upstream copies, not to the rest of this repository.

## Use them

Open this repository in current VS Code with GitHub Copilot Chat, in Codespaces or on your laptop.
Copilot can discover skills in `.github/skills/` and load them when their descriptions match a request.
Ask for the skill by name if needed. The files are also readable without Copilot.

Example requests:

- "Use azure-ai to explain this lab's Search knowledge base."
- "Use azure-quotas to check the model quota needed for our workshop. Do not change any resources."

Skills do not install the tools they describe. Azure operations need Azure CLI, a signed-in account
and the appropriate permissions. MCP-backed operations also need the relevant MCP server, such as
Azure MCP through the Azure tools extension. Azure and documentation queries need network access.
The quota scripts can install an Azure CLI extension and query the selected subscription; review them
before running. PowerShell scripts also need PowerShell.

The lab's pinned dependencies and workshop steps remain authoritative. Do not change package pins,
deploy resources, increase quotas or switch regions just because a general-purpose skill suggests it.
Confirm the scope and customer policy first. Never include tokens, secrets or `.env` contents in chat.

Quota is not physical capacity. A successful quota check cannot guarantee that Azure AI Search can
create a service in a region. See [the instructor settings](../../instructor/setup.md#optional-settings)
for the explicit `AZURE_SEARCH_LOCATION` override.

## Editor-provided skills

These skills were also used here. Their installed extension licenses restrict redistribution, so
their files are not copied into this repository. Install the publisher's extension to obtain the
available skills and tools; availability can vary by extension version.

| Skill | Obtain it from |
| --- | --- |
| Python fact-grounded coding | [Microsoft Pylance](https://marketplace.visualstudio.com/items?itemName=ms-python.vscode-pylance) |
| Agent customization | [GitHub Copilot Chat](https://marketplace.visualstudio.com/items?itemName=GitHub.copilot-chat); see [VS Code customization docs](https://code.visualstudio.com/docs/copilot/customization/agent-skills) |

For additional Foundry workflows, use the publisher's
[Microsoft Foundry skill](https://github.com/microsoft/azure-skills/tree/main/skills/microsoft-foundry).
It is not bundled here; install its complete folder and dependencies rather than copying only its
`SKILL.md`.

## Share and maintain

Participants and customers can keep this folder with the workshop clone, or copy it to
`.github/skills/` in another repository. Retain the license, this index and the complete skill folders.
Do not overwrite existing customer skills without checking first.

These are snapshots, not automatically updated installations. When updating, review the publisher's
license and changes, replace the whole affected skill folder, and update the version and snapshot date
in this index. Check frontmatter, local reference links and script syntax before sharing.