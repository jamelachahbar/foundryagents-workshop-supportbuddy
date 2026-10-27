// Customer chat: one conversation, owned by the contact centre.
const state = { conversation: null, source: null, config: null };
const STORAGE_KEY = "supportbuddy-conversation";

async function init() {
  state.config = await (await fetch("/api/config")).json();
  const saved = sessionStorage.getItem(STORAGE_KEY);
  if (saved) {
    connect(saved);
    return;
  }
  showStart();
}

function showStart() {
  $("#start").hidden = false;
  $("#messages").hidden = $("#composer").hidden = $("#restart").hidden = true;
  $("#mode").textContent = "";
  if (state.config.signs_in) {
    $("#signin").hidden = false;
    $("#start-help").textContent = "This agent remembers customers, so the channel signs you in first. Pick a lab customer.";
    $("#customer").replaceChildren(
      ...state.config.customers.map((c) => {
        const option = el("option", null, `${c.customer_id} - ${c.name}`);
        option.value = c.customer_id;
        return option;
      }),
    );
  } else {
    $("#start-help").textContent = "SupportBuddy will ask who you are. Try CUST-1005 for a handover, or CUST-1002 for a remote restart.";
  }
}

$("#begin").addEventListener("click", async () => {
  try {
    const customer_id = state.config.signs_in ? $("#customer").value : null;
    const conversation = await postJSON("/api/conversations", { customer_id });
    sessionStorage.setItem(STORAGE_KEY, conversation.id);
    connect(conversation.id);
  } catch (error) {
    $("#start-error").textContent = error.message;
  }
});

function connect(id) {
  state.source?.close();
  state.source = listen(`/api/conversations/${id}/events`, (event) => {
    if (event.type === "snapshot") {
      state.conversation = event.conversation;
      $("#start").hidden = true;
      $("#messages").hidden = $("#composer").hidden = false;
      $("#messages").replaceChildren();
      for (const message of event.conversation.transcript) upsertMessage($("#messages"), message, "customer", approve);
      renderState();
    } else if (event.type === "message") {
      upsertMessage($("#messages"), event.message, "customer", approve);
    } else if (event.type === "conversation") {
      Object.assign(state.conversation, event.summary);
      renderState();
    }
  });
  state.source.onerror = () => {
    // A conversation that no longer exists (the contact centre restarted): start again.
    if (state.source.readyState === EventSource.CLOSED) {
      sessionStorage.removeItem(STORAGE_KEY);
      showStart();
    }
  };
}

function renderState() {
  const c = state.conversation;
  const pill = $("#mode");
  pill.className = `pill ${c.mode}`;
  pill.textContent = c.mode === "advisor" ? `${c.advisor} (human advisor)` : MODE_LABEL[c.mode];
  $("#typing").textContent = c.busy ? "SupportBuddy is working on it..." : "";
  const closed = c.mode === "closed";
  $("#text").disabled = $("#send").disabled = closed || c.busy;
  $("#restart").hidden = !closed;
  if (!c.busy && !closed) $("#text").focus();
}

$("#composer").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = $("#text").value.trim();
  if (!text) return;
  $("#error").textContent = "";
  try {
    await postJSON(`/api/conversations/${state.conversation.id}/messages`, { text });
    $("#text").value = "";
  } catch (error) {
    $("#error").textContent = error.message;
  }
});

$("#text").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    $("#composer").requestSubmit();
  }
});

async function approve(message, approved) {
  try {
    await postJSON(`/api/conversations/${state.conversation.id}/approvals`, { interrupt_id: message.interrupt_id, approved });
  } catch (error) {
    $("#error").textContent = error.message;
  }
}

$("#restart").addEventListener("click", () => {
  sessionStorage.removeItem(STORAGE_KEY);
  state.source?.close();
  showStart();
});

init();
