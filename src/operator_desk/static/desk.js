// Operator desk UI. Case data comes from the agent and from customers, so it is only ever
// inserted with textContent, never as HTML (one case contains injected instructions on purpose).
const state = { handovers: [], actions: [], selected: null, drafts: {}, busy: false };
const $ = (selector, root = document) => root.querySelector(selector);

const STATUS_LABEL = { waiting_for_advisor: "waiting", in_progress: "in progress", resolved: "resolved" };
const STATUS_NOTICE = {
  waiting_for_advisor: "Take the case to tell the customer an advisor is on it.",
  in_progress: "You own this case. Write a reply for the customer, then send it.",
  resolved: "Reply sent. The customer is not notified automatically: the agent gives them this reply when they ask about this case.",
};

function item(text, small) {
  const li = document.createElement("li");
  li.textContent = text;
  if (small) {
    const note = document.createElement("small");
    note.textContent = small;
    li.append(note);
  }
  return li;
}

function renderQueue() {
  const list = $("#cases");
  list.replaceChildren(
    ...state.handovers.map((h) => {
      const li = item(`${h.handover_id} - ${h.customer_id ?? "unidentified"}`, `${STATUS_LABEL[h.status] ?? h.status} - ${h.reported_problem}`);
      li.classList.toggle("selected", h.handover_id === state.selected);
      li.addEventListener("click", () => { state.selected = h.handover_id; render(); });
      return li;
    }),
  );
  $("#empty").hidden = state.handovers.length > 0;
  $("#actions").replaceChildren(
    ...state.actions.map((a) => item(`${a.action} - ${a.customer_id} (${a.equipment_id})`, `approved and run at ${a.requested_at}`)),
  );
}

function renderDetail() {
  const detail = $("#detail");
  const h = state.handovers.find((x) => x.handover_id === state.selected);
  if (!h) return;
  const view = $("#case-detail").content.cloneNode(true);
  for (const el of view.querySelectorAll("[data-field]")) {
    const value = h[el.dataset.field];
    el.textContent = Array.isArray(value) ? value.join(", ") : (value ?? "-");
  }
  const pill = $(".pill", view);
  pill.textContent = STATUS_LABEL[h.status] ?? h.status;
  pill.classList.add(h.status);
  $("[data-list=evidence]", view).replaceChildren(...h.evidence.map((e) => item(e)));
  const textarea = $("textarea", view);
  const resolved = h.status === "resolved";
  textarea.value = resolved ? (h.advisor_message ?? "") : (state.drafts[h.handover_id] ?? h.advisor_message ?? "");
  textarea.addEventListener("input", () => { state.drafts[h.handover_id] = textarea.value; });
  for (const button of view.querySelectorAll("button")) {
    button.disabled = resolved || (button.dataset.action === "take" && h.status === "in_progress");
    button.addEventListener("click", () => act(h.handover_id, button, textarea.value));
  }
  textarea.disabled = resolved;
  const notice = $(".notice", view);
  notice.textContent = STATUS_NOTICE[h.status] ?? "";
  notice.classList.toggle("sent", resolved);
  $(".error", view).textContent = state.error?.id === h.handover_id ? state.error.text : "";
  detail.replaceChildren(view);
}

function render() { renderQueue(); renderDetail(); }

async function act(id, button, message) {
  if (state.busy) return;
  state.busy = true;
  state.error = null;
  for (const b of document.querySelectorAll("#detail button")) b.disabled = true;
  button.textContent = button.dataset.action === "resolve" ? "Sending..." : "Taking...";
  $(".error").textContent = "";
  try {
    const response = await fetch(`/api/handovers/${encodeURIComponent(id)}/${button.dataset.action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ advisor: $("#advisor").value, message }),
    });
    if (!response.ok) {
      state.error = { id, text: (await response.json()).error };
      return;
    }
    if (button.dataset.action === "resolve") delete state.drafts[id];
    document.activeElement?.blur();
    await refresh();
  } catch {
    state.error = { id, text: "The desk server did not answer. Is it still running?" };
  } finally {
    state.busy = false;
    state.detailStale = false;
    renderDetail();
  }
}

async function refresh() {
  const data = await (await fetch("/api/handovers")).json();
  const typing = document.activeElement?.tagName === "TEXTAREA";
  // Re-render only when something changed, so clicks and typing are not interrupted.
  const signature = JSON.stringify(data);
  if (signature !== state.signature) {
    const selectedBefore = JSON.stringify(state.handovers.find((x) => x.handover_id === state.selected));
    state.signature = signature;
    state.handovers = data.handovers;
    state.actions = data.actions;
    if (!state.selected && state.handovers.length) state.selected = state.handovers[0].handover_id;
    renderQueue();
    // Re-rendering the open case replaces its buttons, so only do it when that case changed.
    if (JSON.stringify(state.handovers.find((x) => x.handover_id === state.selected)) !== selectedBefore) {
      state.detailStale = true;
    }
  }
  if (state.detailStale && !typing && !state.busy) {
    renderDetail();
    state.detailStale = false;
  }
}

refresh();
setInterval(refresh, 3000);
