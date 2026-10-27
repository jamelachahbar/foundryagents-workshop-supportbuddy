// Operator desk: the human advisor's workspace.
const state = { conversations: new Map(), actions: [], selected: null };
const ORDER = { waiting: 0, advisor: 1, agent: 2, closed: 3 };
const advisor = () => $("#advisor").value.trim() || "Advisor";

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
    if (c.id === state.selected) upsertMessage($("#messages"), event.message, "desk");
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
      const li = el("li", c.mode, `${c.handover?.handover_id ?? "Chat"} - ${c.customer_id ?? c.handover?.customer_id ?? "not identified yet"}`);
      li.append(el("small", null, `${c.mode === "advisor" ? c.advisor : MODE_LABEL[c.mode]}${c.busy ? " (answering)" : ""}`));
      li.append(el("small", null, lastMessage(c)));
      li.classList.toggle("selected", c.id === state.selected);
      li.addEventListener("click", () => { state.selected = c.id; renderQueue(); renderDetail(); });
      return li;
    }),
  );
  $("#queue-empty").hidden = items.length > 0;
}

function lastMessage(c) {
  const last = [...(c.transcript ?? [])].reverse().find((m) => ["customer", "agent", "advisor"].includes(m.role));
  return last ? last.text : c.last_message ?? "";
}

function renderActions() {
  $("#actions").replaceChildren(
    ...state.actions.map((a) => {
      const li = el("li", null, `${a.action} - ${a.customer_id} (${a.equipment_id})`);
      li.append(el("small", null, `approved and run at ${a.requested_at}`));
      return li;
    }),
  );
}

function renderDetail() {
  const c = state.conversations.get(state.selected);
  $("#pick").hidden = Boolean(c);
  $("#detail").hidden = !c;
  if (!c) {
    $("#assist").replaceChildren();
    return;
  }
  $("#messages").replaceChildren();
  for (const message of c.transcript) upsertMessage($("#messages"), message, "desk");
  $("#assist").replaceChildren();
  for (const message of c.assist) addAssist(message);
  renderControls();
}

function renderControls() {
  const c = state.conversations.get(state.selected);
  if (!c) return;
  $("#title").textContent = `${c.handover?.handover_id ?? "Conversation"} - ${c.customer_id ?? c.handover?.customer_id ?? "customer not identified yet"}`;
  const pill = $("#mode");
  pill.className = `pill ${c.mode}`;
  pill.textContent = c.mode === "advisor" ? `${c.advisor} (human advisor)` : MODE_LABEL[c.mode];
  const h = c.handover;
  $("#handover").hidden = !h;
  if (h) {
    $("#h-id").textContent = `${h.handover_id} (created by SupportBuddy at ${h.created_at})`;
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
  for (const b of document.querySelectorAll(".assist button")) b.disabled = assistOff;
  $("#assist-typing").textContent = c.assist_busy ? "The assist agent is working..." : "";
}

function addAssist(message) {
  upsertMessage($("#assist"), message, "assist", approveAssist);
  // A suggested reply can be copied into the reply box; the advisor still decides what to send.
  if (message.role === "agent" && message.text.includes("Suggested reply:")) {
    const node = $(`#assist [data-id="${CSS.escape(message.id)}"]`);
    const use = el("button", "secondary", "Use as reply");
    use.addEventListener("click", () => {
      $("#text").value = message.text.split("Suggested reply:").pop().trim();
      $("#text").focus();
    });
    node.append(el("div"), use);
  }
}

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
  if (text && (await act("messages", { text }))) $("#text").value = "";
});

$("#text").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    $("#composer").requestSubmit();
  }
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
  if (text && (await askAssist(text))) $("#assist-text").value = "";
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
