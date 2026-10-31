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
  agent: "SupportBuddy (AI)",
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

// Agents answer in Markdown. This renders only **bold**, `code`, "- " bullets and "1. " numbered lists, building DOM
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
  text.split(/(\*\*[^*]+\*\*|`[^`]+`)/).forEach((part) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) node.append(el("strong", null, part.slice(2, -2)));
    else if (part.startsWith("`") && part.endsWith("`") && part.length > 2) node.append(el("code", null, part.slice(1, -1)));
    else if (part) node.append(document.createTextNode(part));
  });
  return node;
}

// Renders one message. onApproval(message, approved) is called when an approval card is answered.
function renderMessage(message, view, onApproval, onEvidenceDetails) {
  const role = view === "assist" && message.role === "agent" ? "assist" : message.role;
  const node = el("li", `msg ${role}`);
  node.dataset.id = message.id;
  if (message.role === "evidence") {
    node.className = "msg evidence-message";
    const diagram = el("section", "evidence");
    diagram.setAttribute("aria-label", "Connection evidence");
    renderEvidence(diagram, message.evidence, message.evidence_phase,
      onEvidenceDetails ? (capability) => onEvidenceDetails(message, capability) : null, message.trace);
    node.append(diagram);
    return node;
  }
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
    if (message.action_details) {
      const details = el("div", "approval-context");
      details.append(el("strong", null, "Service impact"), el("p", null, message.action_details.impact));
      const equipment = (message.action_details.evidence ?? []).filter((row) => row.capability === "check_equipment");
      details.append(el("strong", null, "Observed modem evidence"));
      if (equipment.length) {
        const list = el("ul");
        for (const row of equipment) list.append(el("li", null, `${row.label} (${row.status}): ${row.finding || "No usable finding"}`));
        details.append(list);
      } else {
        details.append(el("p", null, "No modem evidence observed in this conversation."));
      }
      details.append(el("p", "muted", "Evidence is context, not authorization. The pending tool arguments above define the target. Approve only if it is correct."));
      bubble.append(details);
    }
    if (message.status === "pending" && onApproval) {
      const buttons = el("div", "buttons");
      const yes = el("button", "ok", "Approve");
      const no = el("button", "secondary", "Decline");
      yes.addEventListener("click", () => { yes.disabled = no.disabled = true; onApproval(message, true); });
      no.addEventListener("click", () => { yes.disabled = no.disabled = true; onApproval(message, false); });
      buttons.append(yes, no);
      bubble.append(buttons);
    } else {
      const label = message.status === "pending"
        ? "Waiting for approval"
        : `Decision: ${message.status}. The run resumes after all approvals in this batch are decided.`;
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

function evidenceSvg(tag, attributes = {}) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
  return node;
}

const TOPOLOGY_TOOLS = {
  identify_customer: { label: "Customer", progress: "Looking up your account", icon: "M3 11 12 3 21 11 M5 10v11h14V10 M9 21v-7h6v7" },
  check_equipment: { label: "Modem", progress: "Testing your modem", icon: "M3 14h18v7H3z M7 14V9 M17 14V9 M8 5a6 6 0 0 1 8 0 M10 8a3 3 0 0 1 4 0 M7 18h.01 M11 18h.01" },
  get_line_status: { label: "Access line", progress: "Checking your line", icon: "M2 7h6v10H2z M16 7h6v10h-6z M8 12h8 M5 10v4 M19 10v4" },
  get_known_incidents: { label: "Area network", progress: "Checking for network incidents in your area", icon: "M7 18h11a4 4 0 0 0 0-8 6 6 0 0 0-11-3 5.5 5.5 0 0 0 0 11" },
  knowledge_base_retrieve: { label: "Runbook guidance", progress: "Looking up the troubleshooting guide", icon: "M3 4h7l2 2 2-2h7v16h-7l-2 2-2-2H3z M12 6v16 M6 8h3 M15 8h3 M6 12h3 M15 12h3" },
  escalate_to_human: { label: "Human advisor", progress: "Preparing the handover to a human advisor", icon: "M5 12a7 7 0 0 1 14 0 M5 12H3v6h4v-6z M19 12h2v6h-4v-6z M19 18v3h-7 M9 8a3 3 0 1 0 6 0 3 3 0 1 0-6 0" },
  restart_modem: { label: "Modem restart", progress: "The modem restart tool has started", icon: "M20 7v5h-5 M20 12a8 8 0 1 0-2 6" },
  get_handover_status: { label: "Case status", progress: "Checking your case", icon: "M5 4h14v18H5z M9 4V2h6v2 M8 9h8 M8 13h8 M8 17h5" },
};
const GENERIC_TOOL = { label: "Additional tool", progress: "Running an additional support tool", icon: "M8 3h8v5h5v8h-5v5H8v-5H3V8h5z" };
const TOOL_STATES = {
  checking: "Checking", completed: "Completed", failed: "Failed", incomplete: "Incomplete",
  previous: "Previous evidence", received: "Result received", approval: "Awaiting approval",
  approved: "Approved; not yet confirmed", declined: "Declined", waiting: "Waiting for advisor",
  joined: "Advisor joined", returned: "Back with SupportBuddy", closed: "Chat closed",
};
const HUMAN_EVENTS = {
  handover_created: "A handover record was created. An advisor has not joined yet.",
  handover_waiting: "Your conversation is waiting for a human advisor. You can keep writing.",
  advisor_joined: "A human advisor has joined your conversation.",
  handed_back: "The human advisor returned the conversation to SupportBuddy.",
  case_closed: "The human advisor closed this conversation.",
  customer_left: "The customer left this conversation. An open case still needs follow-up.",
};

function traceMatches(event, capability) {
  return capability.startsWith("other_tool:")
    ? event.capability === "other_tool" && event.call_id === capability.slice("other_tool:".length)
    : event.capability === capability;
}

function topologyRows(rows, trace = [], phase = "idle") {
  const projected = new Set(rows.map((row) => row.capability));
  const additional = new Map();
  const states = {
    started: "checking", result: "received", failed: "failed", incomplete: "incomplete",
    approval_requested: "approval", approval_approved: "approved", approval_declined: "declined",
    handover_created: "waiting", handover_waiting: "waiting", advisor_joined: "joined",
    handed_back: "returned", case_closed: "closed", customer_left: "closed",
  };
  for (const event of trace) {
    if (event.capability === "agent" || projected.has(event.capability) || !states[event.event]) continue;
    const capability = event.capability === "other_tool" ? `other_tool:${event.call_id}` : event.capability;
    additional.set(capability, {
      key: capability, capability, label: TOPOLOGY_TOOLS[capability]?.label ?? GENERIC_TOOL.label,
      status: states[event.event], finding: HUMAN_EVENTS[event.event] ?? "", run_id: event.run_id,
      signal: "unknown",
    });
  }
  for (const row of additional.values()) {
    if (row.status === "checking" && ["finished", "failed", "incomplete", "approval"].includes(phase)) {
      row.status = "incomplete";
      row.finding = "The run ended without a confirmed tool result.";
    }
  }
  return [...rows, ...additional.values()];
}

function customerProgress(event) {
  if (HUMAN_EVENTS[event.event]) return HUMAN_EVENTS[event.event];
  const tool = TOPOLOGY_TOOLS[event.capability] ?? GENERIC_TOOL;
  const messages = {
    started: `${tool.progress}.`,
    completed: `${tool.label} check completed. Completion does not mean the issue is fixed.`,
    result: `${tool.label}: a result arrived; its outcome has not been validated for this display.`,
    failed: `${tool.label}: no usable result was returned.`,
    incomplete: `${tool.label}: the run ended without a confirmed result.`,
    approval_requested: `${tool.label}: waiting for your permission. The action is not confirmed as executed.`,
    approval_approved: `${tool.label}: you approved the request. Execution is not yet confirmed.`,
    approval_declined: `${tool.label}: you declined the request.`,
    run_started: "SupportBuddy started processing your message.",
    run_finished: "SupportBuddy finished this turn. See the reply for the next steps.",
    run_failed: "SupportBuddy could not finish this turn. Please try again.",
    run_interrupted: "SupportBuddy's turn ended before it completed.",
    approval_required: "SupportBuddy paused for your decision. Use the approval card to respond.",
    step_started: "SupportBuddy started a processing step.",
    step_finished: "SupportBuddy finished a processing step.",
  };
  return messages[event.event] ?? "A support activity update was received.";
}

function shortIssue(findings) {
  const text = findings.filter((row) => row.signal === "attention").map((row) => row.finding).join("; ");
  if (text.includes("Not responding")) return "Modem not responding";
  if (text.includes("Sync down")) return "Line sync down";
  if (text.includes("Sync unstable")) return "Line sync unstable";
  if (text.includes("packet loss")) return "Packet loss observed";
  if (text.includes("open incidents returned")) return "Open area incident";
  if (text.includes("Customer not identified")) return "Account not identified";
  return "Issue observed";
}

function renderCustomerProgress(panel, trace) {
  const log = panel.querySelector(".evidence-progress");
  log.hidden = !trace.length;
  const follow = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  for (const event of trace) {
    const key = JSON.stringify([event.run_id, event.call_id, event.event]);
    if ([...log.children].some((item) => item.dataset.event === key)) continue;
    const item = el("li", `progress-event ${event.event}`);
    item.dataset.event = key;
    const timestamp = el("time", null, clock(event.at));
    timestamp.dateTime = event.at;
    item.append(timestamp, el("span", null, customerProgress(event)));
    log.append(item);
  }
  if (follow) log.scrollTop = log.scrollHeight;
}

function drawEvidenceLinks(map) {
  if (!map.isConnected || map.hidden) return;
  const bounds = map.getBoundingClientRect();
  const svg = map.querySelector(".evidence-links");
  svg.setAttribute("viewBox", `0 0 ${bounds.width} ${bounds.height}`);
  const points = {};
  for (const node of map.querySelectorAll(".evidence-node")) {
    const circle = node.querySelector(".evidence-marker").getBoundingClientRect();
    points[node.dataset.capability] = {
      x: circle.x + circle.width / 2 - bounds.x, y: circle.y + circle.height / 2 - bounds.y,
      radius: circle.width / 2 + 3, checking: node.classList.contains("checking"),
    };
  }
  const path = ["identify_customer", "check_equipment", "get_line_status", "get_known_incidents"];
  const available = path.filter((capability) => points[capability]);
  const target = available.sort((a, b) => points[b].x - points[a].x || points[b].y - points[a].y)[0];
  const edges = path.slice(1).map((to, i) => [path[i], to, "connection"])
    .filter(([from, to]) => points[from] && points[to]);
  if (points.knowledge_base_retrieve && target) edges.push(["knowledge_base_retrieve", target, "guidance"]);
  const branches = [...map.querySelectorAll(".evidence-branches .evidence-node")].map((node) => node.dataset.capability);
  const anchor = available[0] ?? (points.knowledge_base_retrieve ? "knowledge_base_retrieve" : branches[0]);
  for (const branch of branches) {
    if (anchor && anchor !== branch) edges.push([anchor, branch, "workflow"]);
  }
  for (const link of [...svg.children]) {
    if (!edges.some(([from, to]) => `${from}:${to}` === link.dataset.edge)) link.remove();
  }
  for (const [from, to, kind] of edges) {
    const a = points[from], b = points[to];
    let d;
    if (kind === "workflow") {
      const midpoint = (a.y + b.y) / 2;
      d = `M ${a.x} ${a.y + a.radius} C ${a.x} ${midpoint}, ${b.x} ${midpoint}, ${b.x} ${b.y - b.radius}`;
    } else if (kind === "guidance") {
      const rail = bounds.width - 4;
      const bottom = a.y + a.radius + 16;
      const corner = Math.min(12, Math.abs(bottom - b.y) / 2, Math.max(0, rail - b.x - b.radius));
      d = `M ${a.x} ${a.y + a.radius} Q ${a.x} ${bottom} ${a.x + 16} ${bottom}`
        + ` H ${rail - corner} Q ${rail} ${bottom} ${rail} ${bottom - corner}`
        + ` V ${b.y + corner} Q ${rail} ${b.y} ${rail - corner} ${b.y} H ${b.x + b.radius}`;
    } else if (kind === "connection" && Math.abs(a.y - b.y) < 2) {
      const direction = Math.sign(b.x - a.x);
      d = `M ${a.x + direction * a.radius} ${a.y} H ${b.x - direction * b.radius}`;
    } else {
      const right = kind === "guidance" || a.x > bounds.width / 2;
      const direction = right ? 1 : -1;
      const rail = right ? bounds.width - 4 : 4;
      d = `M ${a.x + direction * a.radius} ${a.y} H ${rail} V ${b.y} H ${b.x + direction * b.radius}`;
    }
    const key = `${from}:${to}`;
    let link = [...svg.children].find((candidate) => candidate.dataset.edge === key);
    if (!link) {
      link = evidenceSvg("path", { "data-edge": key, pathLength: "1" });
      link.classList.add("revealing");
      svg.append(link);
      link.addEventListener("animationend", (event) => {
        if (event.animationName === "evidence-connect") link.classList.remove("revealing");
      });
    }
    link.setAttribute("d", d);
    const length = link.getTotalLength();
    if (length > 0) {
      link.style.setProperty("--evidence-dash", `${4 / length} ${6 / length}`);
      link.style.setProperty("--evidence-flow-offset", String(-20 / length));
    }
    const revealing = link.classList.contains("revealing");
    link.setAttribute("class", `evidence-link ${kind}${revealing ? " revealing" : ""}${a.checking || b.checking ? " checking" : ""}`);
  }
}

window.addEventListener("resize", () => document.querySelectorAll(".evidence-map").forEach(drawEvidenceLinks));

function renderEvidence(panel, rows, phase = "idle", onDetails, trace = []) {
  panel.hidden = !Array.isArray(rows);
  if (panel.hidden) {
    panel.replaceChildren();
    delete panel.dataset.signature;
    return;
  }
  const signature = JSON.stringify([rows, phase, trace]);
  if (panel.dataset.signature === signature) return;
  panel.dataset.signature = signature;
  rows = topologyRows(rows, trace, phase);
  const capabilities = [...new Set([
    ...Object.keys(TOPOLOGY_TOOLS).filter((capability) => rows.some((row) => row.capability === capability)),
    ...rows.map((row) => row.capability),
  ])];
  const phases = {
    idle: "Ready to investigate", running: "Agent is investigating", approval: "Waiting for your decision",
    finished: "Agent turn finished", failed: "Agent turn failed", incomplete: "Agent turn interrupted",
  };
  const activeTools = capabilities
    .filter((capability) => rows.some((row) => row.capability === capability && row.status === "checking"))
    .map((capability) => TOPOLOGY_TOOLS[capability]?.label ?? GENERIC_TOOL.label);
  const liveText = phase === "running" && activeTools.length
    ? `${activeTools.length === 1 ? "Tool running" : "Tools running"}: ${activeTools.join(", ")}`
    : phase === "running" && !rows.length ? "Waiting for tool events" : phases[phase] ?? phases.incomplete;
  const heading = el("div", "evidence-heading");
  heading.append(
    el("h3", null, "Your connection"),
    el("span", `evidence-live ${Object.hasOwn(phases, phase) ? phase : "incomplete"}`,
      liveText),
  );
  const flow = el("ul", "evidence-flow");
  flow.setAttribute("aria-label", "Logical connection topology; not a discovered network map");
  const branches = el("ul", "evidence-branches");
  branches.setAttribute("aria-label", "Support activities; dashed links are workflow associations");
  let completedChecks = 0;
  let observedIssues = 0;
  for (const capability of capabilities) {
    const { label, icon: iconPath } = TOPOLOGY_TOOLS[capability] ?? GENERIC_TOOL;
    const findings = rows.filter((row) => row.capability === capability);
    if (!findings.length) continue;
    const active = findings.some((row) => row.status === "checking");
    const failed = findings.some((row) => row.status === "failed");
    const incomplete = findings.some((row) => row.status === "incomplete");
    const status = active ? "checking" : failed ? "failed" : incomplete ? "incomplete"
      : findings.some((row) => row.status === "completed") ? "completed"
      : findings[findings.length - 1].status;
    const attention = findings.some((row) => row.signal === "attention");
    if (status === "completed") {
      completedChecks += 1;
      if (attention) observedIssues += 1;
    }
    const node = el("li", `evidence-node ${status}${attention ? " attention" : ""}`);
    node.dataset.capability = capability;
    node.setAttribute("aria-busy", String(active));
    const head = el("button", "evidence-node-head");
    head.type = "button";
    const issue = attention ? shortIssue(findings) : "";
    head.setAttribute("aria-label", `${label}: ${TOOL_STATES[status] ?? "Incomplete"}.${issue ? ` ${issue}.` : ""} Open diagnostic inspector`);
    head.setAttribute("aria-controls", "diagnostic-panel");
    head.addEventListener("click", () => {
      if (!window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
        head.getAnimations().filter((animation) => !(animation instanceof CSSTransition))
          .forEach((animation) => animation.cancel());
        head.animate([
          { transform: "scale(1)" },
          { transform: "scale(1.035)", offset: .5 },
          { transform: "scale(1)" },
        ], { duration: 650, iterations: 2, easing: "ease-in-out" });
        marker.animate([
          { boxShadow: "0 0 0 0 transparent" },
          { boxShadow: "0 0 0 8px color-mix(in srgb, currentColor 25%, transparent)", offset: .5 },
          { boxShadow: "0 0 0 12px transparent" },
        ], { duration: 500, iterations: 2, easing: "ease-out" });
      }
      onDetails?.(capability);
    });
    const marker = el("span", "evidence-marker");
    marker.setAttribute("aria-hidden", "true");
    const icon = evidenceSvg("svg", { viewBox: "0 0 24 24", focusable: "false" });
    icon.append(evidenceSvg("path", { d: iconPath }));
    marker.append(icon);
    const badge = el("span", `evidence-badge ${status}`);
    const badgeIcon = evidenceSvg("svg", { viewBox: "0 0 24 24", focusable: "false" });
    badgeIcon.append(evidenceSvg("path", {
      d: status === "checking" ? "M12 5v7l4 3" : status === "failed" ? "M7 7l10 10 M17 7 7 17"
        : status === "completed" && !attention || status === "joined" ? "m6 12 4 4 8-9"
        : "M12 5v9 M12 18h.01",
    }));
    badge.append(badgeIcon);
    marker.append(badge);
    head.append(marker, el("strong", null, label),
      el("span", `evidence-state evidence-status ${status}`, TOOL_STATES[status] ?? TOOL_STATES.incomplete),
      el("span", "evidence-detail-hint", "Inspect"));
    if (attention) head.append(el("span", "evidence-attention", issue));
    if (capability === "knowledge_base_retrieve") node.append(el("span", "evidence-section-label", "Guidance used"));
    node.append(head);
    (["identify_customer", "check_equipment", "get_line_status", "get_known_incidents", "knowledge_base_retrieve"]
      .includes(capability) ? flow : branches).append(node);
  }
  if (!panel.querySelector(".evidence-flow")) {
    const map = el("div", "evidence-map");
    const resize = new ResizeObserver(() => {
      if (map.isConnected) drawEvidenceLinks(map);
      else resize.disconnect();
    });
    resize.observe(map);
    map.append(evidenceSvg("svg", { class: "evidence-links", "aria-hidden": "true", focusable: "false" }), flow, branches);
    const pathLabel = el("span", "evidence-section-label evidence-path-label", "Connection path");
    map.insertBefore(pathLabel, flow);
    const log = el("ol", "evidence-progress");
    log.setAttribute("role", "log");
    log.setAttribute("aria-label", "Progress updates");
    log.setAttribute("aria-live", "polite");
    log.setAttribute("aria-relevant", "additions");
    const details = el("details", "evidence-progress-details");
    const summary = el("summary", null, "Show progress details");
    details.append(
      summary,
      el("p", "evidence-footnote", "Solid links: logical connection path. Dashed links: guidance and support activities, not execution order. Not a discovered network; completed does not mean fixed."),
      log,
    );
    details.addEventListener("toggle", () => {
      summary.textContent = details.open ? "Hide progress details" : "Show progress details";
      if (details.open) log.scrollTop = log.scrollHeight;
    });
    panel.replaceChildren(
      heading,
      el("p", "evidence-overview"),
      map,
      el("p", "evidence-empty", "No diagnostic tool events were reported for this turn."),
      details,
    );
  } else {
    // Preserve unchanged nodes so a result from one tool does not restart another tool's animation.
    for (const group of [flow, branches]) {
      const current = panel.querySelector(`.${group.className}`);
      const nodes = [...group.children];
      for (const existing of [...current.children]) {
        if (!nodes.some((node) => node.dataset.capability === existing.dataset.capability)) existing.remove();
      }
      for (const [index, node] of nodes.entries()) {
        const existing = [...current.children].find((candidate) => candidate.dataset.capability === node.dataset.capability);
        if (!existing) current.insertBefore(node, current.children[index] ?? null);
        else if (!existing.isEqualNode(node)) {
          const focused = existing.contains(document.activeElement);
          existing.replaceWith(node);
          if (focused) node.querySelector("button").focus({ preventScroll: true });
        }
      }
    }
    const live = panel.querySelector(".evidence-live");
    if (!live.isEqualNode(heading.lastChild)) live.replaceWith(heading.lastChild);
  }
  const map = panel.querySelector(".evidence-map");
  panel.querySelector(".evidence-overview").textContent =
    `${completedChecks} ${completedChecks === 1 ? "check" : "checks"} completed \u00b7 ${observedIssues} ${observedIssues === 1 ? "issue" : "issues"} observed`;
  panel.querySelector(".evidence-path-label").hidden = !capabilities.some((capability) =>
    ["identify_customer", "check_equipment", "get_line_status", "get_known_incidents"].includes(capability));
  const empty = !panel.querySelector(".evidence-node");
  map.hidden = empty;
  const notice = panel.querySelector(".evidence-empty");
  notice.hidden = !empty;
  notice.textContent = phase === "running"
    ? "Topology nodes appear as their diagnostic tools start."
    : "No diagnostic tool events were reported for this turn.";
  panel.querySelector(".evidence-footnote").hidden = empty;
  renderCustomerProgress(panel, trace);
  requestAnimationFrame(() => drawEvidenceLinks(map));
}

// The element that scrolls a list: its pane (.scroller) on the desk, the page in the customer chat.
const scrollerOf = (list) => list.closest(".scroller") ?? document.scrollingElement;

function scrollToEnd(list) {
  const scroller = scrollerOf(list);
  scroller.scrollTop = scroller.scrollHeight;
}

// Adds or replaces a message in a list. Follows new messages only if the reader is already at the end.
function upsertMessage(list, message, view, onApproval, onEvidenceDetails) {
  const existing = list.querySelector(`[data-id="${CSS.escape(message.id)}"]`);
  const scroller = scrollerOf(list);
  const atEnd = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 120;
  if (existing && message.role === "evidence") {
    renderEvidence($(".evidence", existing), message.evidence, message.evidence_phase,
      onEvidenceDetails ? (capability) => onEvidenceDetails(message, capability) : null, message.trace);
  } else {
    const node = renderMessage(message, view, onApproval, onEvidenceDetails);
    if (existing) existing.replaceWith(node);
    else list.append(node);
  }
  if (message.role === "evidence" && diagnosticSelection?.messageId === message.id) {
    diagnosticSelection.message = message;
    updateEvidenceDialog(message);
  }
  if (atEnd) scrollToEnd(list);
}

let diagnosticDialog = null;
let diagnosticSelection = null;
let traceConversation = null;
let traceOpener = null;

function updateTraceConversation(conversation) {
  traceConversation = conversation;
  if (diagnosticDialog && !diagnosticDialog.hidden) updateEvidenceDialog(diagnosticSelection?.message);
}

function closeEvidenceDialog() {
  if (!diagnosticDialog) return;
  diagnosticDialog.hidden = true;
  document.body.classList.remove("trace-open");
  const node = diagnosticSelection?.messageId
    ? document.querySelector(`[data-id="${CSS.escape(diagnosticSelection.messageId)}"] [data-capability="${CSS.escape(diagnosticSelection.capability)}"] button`)
    : null;
  (node ?? (traceOpener?.isConnected ? traceOpener : null))?.focus({ preventScroll: true });
  diagnosticSelection = null;
  requestAnimationFrame(() => document.querySelectorAll(".evidence-map").forEach(drawEvidenceLinks));
}

function showEvidenceDetails(message, capability) {
  if (!diagnosticDialog) {
    diagnosticDialog = el("aside", "diagnostic-panel");
    diagnosticDialog.id = "diagnostic-panel";
    diagnosticDialog.setAttribute("aria-labelledby", "diagnostic-title");
    diagnosticDialog.setAttribute("role", "region");
    const heading = el("div", "evidence-heading");
    const title = el("h3");
    title.id = "diagnostic-title";
    const close = el("button", "secondary", "Close traces");
    close.type = "button";
    close.addEventListener("click", closeEvidenceDialog);
    heading.append(title, close);
    const all = el("button", "secondary", "Show all traces");
    all.type = "button";
    all.addEventListener("click", () => {
      diagnosticSelection = { message: null, messageId: null, capability: null };
      updateEvidenceDialog(null);
    });
    diagnosticDialog.append(heading, all, el("div", "diagnostic-content"));
    document.body.append(diagnosticDialog);
  }
  traceOpener = document.activeElement;
  diagnosticSelection = { message, messageId: message?.id, capability };
  diagnosticDialog.hidden = false;
  document.body.classList.add("trace-open");
  updateEvidenceDialog(message);
  diagnosticDialog.querySelector("button").focus({ preventScroll: true });
  requestAnimationFrame(() => document.querySelectorAll(".evidence-map").forEach(drawEvidenceLinks));
}

function updateEvidenceDialog(message) {
  if (!diagnosticSelection || !diagnosticDialog) return;
  const capability = diagnosticSelection.capability;
  const rows = message ? topologyRows(message.evidence, message.trace, message.evidence_phase)
    .filter((row) => row.capability === capability) : [];
  const original = message ? document.querySelector(`[data-id="${CSS.escape(message.id)}"] [data-capability="${CSS.escape(capability)}"]`) : null;
  $("#diagnostic-title").textContent = capability
    ? `${original?.querySelector("strong")?.textContent ?? capability} traces` : "Conversation traces";
  const content = $(".diagnostic-content", diagnosticDialog);
  content.replaceChildren();
  if (original) {
    const emblem = el("div", original.className.replace("evidence-node", "diagnostic-emblem"));
    emblem.append(original.querySelector(".evidence-marker").cloneNode(true));
    content.append(emblem);
  }
  content.append(el("p", "muted", capability
    ? "Selected node and diagnostic turn. Use Show all traces for the whole conversation."
    : "All observed SupportBuddy run and tool events in this conversation. Advisor-assist traces stay private."));
  for (const row of rows) {
    const section = el("section", "diagnostic-resource");
    section.append(el("strong", null, row.label));
    const facts = el("dl");
    facts.append(el("dt", null, "Tool"), el("dd", null,
      capability.startsWith("other_tool:") ? GENERIC_TOOL.label : capability));
    facts.append(el("dt", null, "Status"), el("dd", `diagnostic-status ${row.status}`,
      TOOL_STATES[row.status] ?? "Incomplete"));
    facts.append(el("dt", null, "Finding"), el("dd", null,
      row.finding || (row.status === "checking" ? "Waiting for the tool result." : "No usable finding.")));
    if (row.signal === "attention") facts.append(el("dt", null, "Attention"),
      el("dd", "diagnostic-status incomplete", "Issue observed"));
    section.append(facts);
    content.append(section);
  }
  content.append(el("h4", null, "Event timeline"));
  const timeline = el("ol", "diagnostic-timeline");
  const source = traceConversation?.trace ?? message?.trace ?? [];
  const events = capability ? source.filter((event) =>
    traceMatches(event, capability) && event.run_id === message.run_id) : source;
  const labels = {
    started: "Tool call started", completed: "Validated result received",
    result: "Result received (not projected)", failed: "Unusable or error result",
    incomplete: "Run ended without a result", run_started: "Agent run started",
    run_finished: "Agent run finished", run_failed: "Agent run failed",
    run_interrupted: "Agent run interrupted", approval_required: "Approval required",
    approval_requested: "Permission requested", approval_approved: "Request approved (not execution)",
    approval_declined: "Request declined", handover_created: "Handover record created",
    handover_waiting: "Waiting for a human advisor", advisor_joined: "Human advisor joined",
    handed_back: "Returned to SupportBuddy", case_closed: "Conversation closed",
    customer_left: "Customer left the conversation",
    step_started: "Processing step started", step_finished: "Processing step finished",
  };
  for (const event of events) {
    const item = el("li", `diagnostic-event ${event.event}`);
    const timestamp = el("time", null, event.at);
    timestamp.dateTime = event.at;
    item.append(el("strong", null, labels[event.event] ?? "Unknown event"), timestamp,
      el("span", null, event.capability === "agent" ? "SupportBuddy" : event.capability),
      el("span", null, customerProgress(event)),
      el("code", null, `Call/run: ${event.call_id}`));
    const start = events.find((candidate) => candidate.call_id === event.call_id && candidate.event === "started");
    if (event.event !== "started" && start) {
      const elapsed = Date.parse(event.at) - Date.parse(start.at);
      if (Number.isFinite(elapsed) && elapsed >= 0) item.append(el("span", "muted", `${elapsed} ms between observed events`));
    }
    timeline.append(item);
  }
  content.append(events.length ? timeline : el("p", "muted", "No recorded lifecycle events for this snapshot."),
    el("p", "muted", "Timestamps are local receipt times, not provider execution durations. Raw payloads, private reasoning and Application Insights traces are not included."));
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && diagnosticDialog && !diagnosticDialog.hidden) closeEvidenceDialog();
});

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
