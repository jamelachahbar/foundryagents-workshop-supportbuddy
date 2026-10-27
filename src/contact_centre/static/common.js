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

// Agents answer in Markdown. This renders only **bold**, "- " bullets and "1. " numbered lists, building DOM
// nodes (no HTML parsing), which is enough for SupportBuddy's answer format.
function richText(text) {
  const fragment = document.createDocumentFragment();
  let list = null;
  for (const line of text.split("\n")) {
    const item = line.match(/^\s*(?:([-*])|\d+[.)])\s+(.*)$/);
    if (item) {
      const tag = item[1] ? "ul" : "ol";
      if (list?.tagName.toLowerCase() !== tag) list = fragment.appendChild(el(tag, "md-list"));
      list.append(inline(el("li"), item[2]));
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
  const avatar = avatarFor(message, view);
  if (avatar) node.append(avatar);
  const bubble = el("div", "bubble");
  const who = whoLabel(message, view);
  if (who) {
    const head = el("span", "who", who);
    if (message.at) head.append(el("time", null, clock(message.at)));
    bubble.append(head);
  }
  if (message.role === "approval") {
    bubble.append(el("div", "title", "Approval needed"));
    bubble.append(el("div", "text", message.text));
    const args = Object.entries(message.arguments ?? {}).map(([k, v]) => `${k}: ${v}`).join(", ");
    if (args) bubble.append(el("div", "args", `${message.tool}(${args})`));
    if (message.status === "pending" && onApproval) {
      const buttons = el("div", "buttons");
      const yes = el("button", "ok", "Approve");
      const no = el("button", "secondary", "Decline");
      yes.addEventListener("click", () => { yes.disabled = no.disabled = true; onApproval(message, true); });
      no.addEventListener("click", () => { yes.disabled = no.disabled = true; onApproval(message, false); });
      buttons.append(yes, no);
      bubble.append(buttons);
    } else {
      const label = message.status === "pending" ? "Waiting for approval" : `Decision: ${message.status}`;
      bubble.append(el("div", `decision ${message.status}`, label));
    }
  } else if (["agent", "advisor"].includes(message.role)) {
    bubble.append(richText(message.text));
  } else {
    bubble.append(el("div", "text", message.text));
  }
  node.append(bubble);
  return node;
}

function avatarFor(message, view) {
  if (message.role === "agent") {
    const avatar = el("span", "avatar");
    const img = el("img");
    img.src = "/icon.svg";
    img.alt = "";
    avatar.append(img);
    return avatar;
  }
  if (message.role === "advisor") return el("span", "avatar", initials(message.author ?? "Advisor"));
  if (message.role === "customer" && view !== "customer") return el("span", "avatar", "C");
  return null;
}

const initials = (name) => name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join("");
const clock = (iso) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

// The element that scrolls a list: its pane (.scroller) on the desk, the page in the customer chat.
const scrollerOf = (list) => list.closest(".scroller") ?? document.scrollingElement;

function scrollToEnd(list) {
  const scroller = scrollerOf(list);
  scroller.scrollTop = scroller.scrollHeight;
}

// Adds or replaces a message in a list. Follows new messages only if the reader is already at the end.
function upsertMessage(list, message, view, onApproval) {
  const node = renderMessage(message, view, onApproval);
  const existing = list.querySelector(`[data-id="${CSS.escape(message.id)}"]`);
  const scroller = scrollerOf(list);
  const atEnd = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 120;
  if (existing) existing.replaceWith(node);
  else list.append(node);
  if (atEnd) scrollToEnd(list);
}

// Grows a textarea with its content (up to its CSS max-height) and sends on Enter.
function composer(textarea, form) {
  const fit = () => {
    textarea.style.height = "auto";
    textarea.style.height = `${textarea.scrollHeight}px`;
  };
  textarea.addEventListener("input", fit);
  textarea.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  return { clear() { textarea.value = ""; fit(); }, fit };
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
