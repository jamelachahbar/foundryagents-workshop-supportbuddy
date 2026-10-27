// Shared by the customer chat and the operator desk.
// Conversation text comes from customers, agents and tools, so it is only ever inserted with
// textContent, never as HTML (one lab customer's Wi-Fi name contains injected instructions on purpose).
const $ = (selector, root = document) => root.querySelector(selector);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

const MODE_LABEL = {
  agent: "SupportBuddy (AI) is answering",
  waiting: "Waiting for a human advisor",
  advisor: "Human advisor",
  closed: "Closed",
};

function whoLabel(message, view) {
  if (message.role === "customer") return view === "customer" ? "You" : "Customer";
  if (message.role === "agent") return view === "assist" ? "Assist agent (AI)" : "SupportBuddy (AI)";
  if (message.role === "advisor") return `${message.author ?? "Advisor"} (human advisor)`;
  return null;
}

// Agents answer in Markdown. This renders only **bold** and "- " bullets, building DOM nodes
// (no HTML parsing), which is enough for SupportBuddy's answer format.
function richText(text) {
  const fragment = document.createDocumentFragment();
  let list = null;
  for (const line of text.split("\n")) {
    const bullet = line.match(/^\s*[-*]\s+(.*)$/);
    if (bullet) {
      list ??= fragment.appendChild(el("ul", "md-list"));
      list.append(inline(el("li"), bullet[1]));
      continue;
    }
    list = null;
    fragment.append(inline(el("div", "md-line"), line.replace(/^#+\s*/, "")));
  }
  return fragment;
}

function inline(node, text) {
  // Foundry-run knowledge tools cite as 【0:search_idx†Title】; show the title as [Title].
  text = text.replace(/【[^†】]*†([^】]+)】/g, " [$1]");
  text.split(/(\*\*[^*]+\*\*)/).forEach((part) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) node.append(el("strong", null, part.slice(2, -2)));
    else if (part) node.append(document.createTextNode(part));
  });
  return node;
}

// Renders one message. onApproval(message, approved) is called when an approval card is answered.
function renderMessage(message, view, onApproval) {
  const role = view === "assist" && message.role === "agent" ? "assist" : message.role;
  const node = el("li", `msg ${role}`);
  node.dataset.id = message.id;
  const who = whoLabel(message, view);
  if (who) node.append(el("span", "who", who));
  node.append(["agent", "advisor"].includes(message.role) ? richText(message.text) : document.createTextNode(message.text));
  if (message.role === "approval") {
    const args = Object.entries(message.arguments ?? {}).map(([k, v]) => `${k}: ${v}`).join(", ");
    if (args) node.append(el("div", "args", `${message.tool}(${args})`));
    if (message.status === "pending" && onApproval) {
      const buttons = el("div", "buttons");
      const yes = el("button", "ok", "Approve");
      const no = el("button", "secondary", "Decline");
      yes.addEventListener("click", () => { yes.disabled = no.disabled = true; onApproval(message, true); });
      no.addEventListener("click", () => { yes.disabled = no.disabled = true; onApproval(message, false); });
      buttons.append(yes, no);
      node.append(buttons);
    } else {
      node.append(el("div", "args", message.status === "pending" ? "Waiting for approval" : `Decision: ${message.status}`));
    }
  }
  return node;
}

// Adds or replaces a message in a list, keeping the list scrolled to the bottom.
function upsertMessage(list, message, view, onApproval) {
  const node = renderMessage(message, view, onApproval);
  const existing = list.querySelector(`[data-id="${CSS.escape(message.id)}"]`);
  const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 60;
  if (existing) existing.replaceWith(node);
  else list.append(node);
  if (atBottom || !existing) list.scrollTop = list.scrollHeight;
}

async function postJSON(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error ?? `Request failed (${response.status})`);
  return data;
}

// Server-Sent Events: the browser reconnects automatically; the server resends a snapshot on connect.
function listen(url, onEvent) {
  const source = new EventSource(url);
  source.onmessage = (event) => onEvent(JSON.parse(event.data));
  return source;
}
