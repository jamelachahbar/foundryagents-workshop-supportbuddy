// Operator desk: the human advisor's workspace.
const state = { conversations: new Map(), actions: [], selected: null, enhanced: false };
const ORDER = { waiting: 0, advisor: 1, agent: 2, closed: 3 };
const advisor = () => $("#advisor").value.trim() || "Advisor";
const reply = composer($("#text"), $("#composer"));
const ask = composer($("#assist-text"), $("#assist-form"));

fetch("/api/config").then((response) => {
  if (!response.ok) throw new Error("Could not load contact-centre configuration.");
  return response.json();
}).then((config) => {
  state.enhanced = Boolean(config.evidence_panel);
  renderDetail();
}).catch((error) => { $("#assist-error").textContent = error.message; });

listen("/api/desk/events", (event) => {
  if (event.type === "snapshot") {
    state.conversations = new Map(event.conversations.map((c) => [c.id, c]));
    state.actions = event.actions;
    renderQueue();
    renderActions();
    renderDetail();
    return;
  }
  if (event.type === "actions") {
    state.actions = event.actions;
    renderActions();
    return;
  }
  let c = state.conversations.get(event.conversation);
  if (!c && event.type === "conversation") {
    c = { ...event.summary, transcript: [], assist: [] };
    state.conversations.set(c.id, c);
  }
  if (!c) return;
  if (event.type === "conversation") {
    Object.assign(c, event.summary);
    renderQueue();
    if (c.id === state.selected) renderControls();
  } else if (event.type === "message") {
    upsertInto(c.transcript, event.message);
    if (c.id === state.selected) upsertMessage($("#messages"), event.message, "desk", null, showDetails);
    renderQueue();
  } else if (event.type === "assist") {
    upsertInto(c.assist, event.message);
    if (c.id === state.selected) addAssist(event.message);
  }
});

function upsertInto(list, message) {
  const index = list.findIndex((m) => m.id === message.id);
  if (index >= 0) list[index] = message;
  else list.push(message);
}

function renderQueue() {
  const items = [...state.conversations.values()].sort(
    (a, b) => ORDER[a.mode] - ORDER[b.mode] || b.created_at.localeCompare(a.created_at),
  );
  $("#queue").replaceChildren(
    ...items.map((c) => {
      const customer = customerOf(c);
      const li = el("li", c.mode);
      li.tabIndex = 0;
      li.setAttribute("role", "button");
      li.append(
        el("span", "avatar", customer ? customer.slice(-4) : "?"),
        el("span", "title", `${c.identified?.name || customer || "Not identified yet"}${c.handover ? ` · ${c.handover.handover_id}` : ""}`),
        el("time", null, clock(c.created_at)),
        el("span", "status", `${c.mode === "advisor" ? `${c.advisor} (human advisor)` : MODE_LABEL[c.mode]}${c.busy ? " · answering" : ""}`),
        el("span", "preview", lastMessage(c)),
      );
      li.classList.toggle("selected", c.id === state.selected);
      const open = () => { state.selected = c.id; renderQueue(); renderDetail(); };
      li.addEventListener("click", open);
      li.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); } });
      return li;
    }),
  );
  $("#queue-empty").hidden = items.length > 0;
  const waiting = items.filter((c) => c.mode === "waiting").length;
  $("#queue-count").textContent = waiting ? `${waiting} waiting` : items.length ? `${items.length}` : "";
}

function customerOf(c) {
  return c.customer_id ?? c.identified?.customer_id ?? c.handover?.customer_id ?? null;
}

function lastMessage(c) {
  const last = [...(c.transcript ?? [])].reverse().find((m) => ["customer", "agent", "advisor"].includes(m.role));
  return last ? last.text : c.last_message ?? "";
}

function renderActions() {
  $("#actions").replaceChildren(
    ...state.actions.map((a) => {
      const li = el("li", null, `${a.action} - ${a.customer_id} (${a.equipment_id})`);
      li.append(el("small", null, `approved and run at ${clock(a.requested_at)}`));
      return li;
    }),
  );
  $("#actions-empty").hidden = state.actions.length > 0;
}

function renderDetail() {
  closeEvidenceDialog();
  const c = state.conversations.get(state.selected);
  $("#pick").hidden = Boolean(c);
  $("#detail").hidden = !c;
  if (!c) {
    updateTraceConversation(null);
    $("#assist").replaceChildren();
    $("#assist-empty").hidden = false;
    return;
  }
  $("#messages").replaceChildren();
  for (const message of c.transcript) upsertMessage($("#messages"), message, "desk", null, showDetails);
  $("#assist").replaceChildren();
  for (const message of c.assist) addAssist(message);
  renderControls();
  scrollToEnd($("#messages"));
  scrollToEnd($("#assist"));
}

function renderControls() {
  const c = state.conversations.get(state.selected);
  if (!c) return;
  updateTraceConversation(c);
  $("#traces").hidden = !state.enhanced;
  const who = customerOf(c);
  $("#title").textContent = `${c.handover?.handover_id ?? "Conversation"} - ${who ? `${who}${c.identified?.name ? ` (${c.identified.name})` : ""}` : "customer not identified yet"}`;
  const pill = $("#mode");
  pill.className = `pill ${c.mode}`;
  pill.textContent = c.mode === "advisor" ? `${c.advisor} (human advisor)` : MODE_LABEL[c.mode];
  const h = c.handover;
  $("#handover").hidden = !h;
  if (h) {
    $("#h-id").textContent = `${h.handover_id}, ${clock(h.created_at)}`;
    $("#h-problem").textContent = h.reported_problem;
    $("#h-unresolved").textContent = h.unresolved_issue;
    $("#h-next").textContent = h.recommended_next_investigation;
    $("#h-contact").textContent = h.preferred_contact_channel ?? "-";
    $("#h-evidence").replaceChildren(...h.evidence.map((e) => el("li", null, e)));
  }
  const mine = c.mode === "advisor";
  $("#take").disabled = !["waiting", "agent"].includes(c.mode) || c.busy;
  $("#handback").disabled = !mine;
  $("#close").disabled = c.mode === "closed" || c.busy;
  $("#text").disabled = $("#send").disabled = !mine;
  $("#text").placeholder = mine ? "Write to the customer" : "Take the conversation to write to the customer";
  const assistOff = c.assist_busy || c.mode === "closed";
  for (const b of document.querySelectorAll(".assist .chips button, #assist-send")) b.disabled = assistOff;
  $("#assist-text").disabled = assistOff;
  $("#assist-typing").textContent = c.assist_busy ? "The assist agent is working" : "";
  $("#assist-empty").hidden = c.assist.length > 0;
  if (state.enhanced) {
    for (const button of document.querySelectorAll(".use-suggested-reply")) button.disabled = !mine || c.assist_busy;
  }
}

function addAssist(message) {
  $("#assist-empty").hidden = true;
  upsertMessage($("#assist"), message, "assist", approveAssist);
  // A suggested reply can be copied into the reply box; the advisor still decides what to send.
  const draft = state.enhanced ? message.reply_draft
    : message.text.includes("Suggested reply:") ? message.text.split("Suggested reply:").pop().trim() : null;
  if (message.role === "agent" && draft) {
    const bubble = $(`#assist [data-id="${CSS.escape(message.id)}"] .bubble`);
    const use = el("button", "secondary use-suggested-reply", state.enhanced ? "Use suggested reply" : "Use as reply");
    use.type = "button";
    const c = state.conversations.get(state.selected);
    use.disabled = state.enhanced && (c?.mode !== "advisor" || c.assist_busy);
    use.addEventListener("click", () => {
      const c = state.conversations.get(state.selected);
      if (state.enhanced && c?.mode !== "advisor") {
        $("#assist-error").textContent = "Take this conversation before using a suggested reply.";
        return;
      }
      if (state.enhanced && $("#text").value.trim()) {
        $("#assist-error").textContent = "Your composer already contains a draft. Send it or clear it before using this suggestion.";
        return;
      }
      if (draft.length > $("#text").maxLength) {
        $("#assist-error").textContent = "This suggested reply exceeds the message limit. Edit or copy a shorter portion manually.";
        return;
      }
      $("#assist-error").textContent = "";
      $("#text").value = draft;
      reply.fit();
      $("#text").focus();
    });
    bubble.append(use);
  }
}

function showDetails(message, capability) {
  showEvidenceDetails(message, capability);
}

$("#traces").addEventListener("click", () => showEvidenceDetails(null, null));

async function act(action, body = {}) {
  $("#error").textContent = "";
  try {
    await postJSON(`/api/desk/conversations/${state.selected}/${action}`, { advisor: advisor(), ...body });
    return true;
  } catch (error) {
    $("#error").textContent = error.message;
    return false;
  }
}

$("#take").addEventListener("click", () => act("take"));
$("#handback").addEventListener("click", () => act("handback"));
$("#close").addEventListener("click", () => act("close"));

$("#composer").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = $("#text").value.trim();
  if (text && (await act("messages", { text }))) reply.clear();
});

async function askAssist(text) {
  $("#assist-error").textContent = "";
  try {
    await postJSON(`/api/desk/conversations/${state.selected}/assist`, { advisor: advisor(), text });
    return true;
  } catch (error) {
    $("#assist-error").textContent = error.message;
    return false;
  }
}

for (const button of document.querySelectorAll("[data-prompt]")) {
  button.addEventListener("click", () => askAssist(button.dataset.prompt));
}

$("#assist-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = $("#assist-text").value.trim();
  if (text && (await askAssist(text))) ask.clear();
});

async function approveAssist(message, approved) {
  try {
    await postJSON(`/api/desk/conversations/${state.selected}/assist-approvals`, {
      advisor: advisor(), interrupt_id: message.interrupt_id, approved,
    });
  } catch (error) {
    $("#assist-error").textContent = error.message;
  }
}
