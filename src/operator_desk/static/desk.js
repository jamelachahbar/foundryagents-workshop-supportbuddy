// Operator desk UI. Case data comes from the agent and from customers, so it is only ever
// inserted with textContent, never as HTML (one case contains injected instructions on purpose).
const state = { handovers: [], actions: [], selected: null };
const $ = (selector, root = document) => root.querySelector(selector);

const STATUS_LABEL = { waiting_for_advisor: "waiting", in_progress: "in progress", resolved: "resolved" };

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
  const keepDraft = $("textarea", detail)?.value ?? "";
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
  textarea.value = keepDraft || (h.advisor_message ?? "");
  const resolved = h.status === "resolved";
  for (const button of view.querySelectorAll("button")) {
    button.disabled = resolved || (button.dataset.action === "take" && h.status === "in_progress");
    button.addEventListener("click", () => act(h.handover_id, button.dataset.action, textarea.value));
  }
  textarea.disabled = resolved;
  detail.replaceChildren(view);
}

function render() { renderQueue(); renderDetail(); }

async function act(id, action, message) {
  const response = await fetch(`/api/handovers/${encodeURIComponent(id)}/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ advisor: $("#advisor").value, message }),
  });
  if (!response.ok) {
    $(".error").textContent = (await response.json()).error;
    return;
  }
  $("textarea").value = "";
  document.activeElement?.blur();
  await refresh();
}

async function refresh() {
  const data = await (await fetch("/api/handovers")).json();
  const typing = document.activeElement?.tagName === "TEXTAREA";
  // Re-render only when something changed, so clicks and typing are not interrupted.
  const signature = JSON.stringify(data);
  if (signature !== state.signature) {
    state.signature = signature;
    state.handovers = data.handovers;
    state.actions = data.actions;
    if (!state.selected && state.handovers.length) state.selected = state.handovers[0].handover_id;
    renderQueue();
    state.detailStale = true;
  }
  if (state.detailStale && !typing) {
    renderDetail();
    state.detailStale = false;
  }
}

refresh();
setInterval(refresh, 3000);
