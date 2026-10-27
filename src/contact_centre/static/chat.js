// Customer chat: one conversation, owned by the contact centre.
const state = { conversation: null, source: null, config: null };
const STORAGE_KEY = "supportbuddy-conversation";
const input = composer($("#text"), $("#composer"));

// One click starts a lab scenario; the signed-in channel already knows who the customer is.
const SUGGESTIONS = [
  "Hi, I'm anna.weber@example.com, my internet stopped this morning.",
  "Hello, I'm customer CUST-1005. I have no internet at all since this morning, nothing loads on any device.",
  "I'm CUST-1002, my modem doesn't respond since this morning. Please restart it remotely for me.",
  "Internet works on cable but Wi-Fi is very slow upstairs. Phone +1 202 555 0103.",
];
const SIGNED_IN_SUGGESTIONS = [
  "My modem doesn't respond since this morning.",
  "Wi-Fi is very slow upstairs, but cable works.",
  "I'd like to talk to a person, please.",
];

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
  $("#messages").hidden = $("#dock").hidden = $("#new").hidden = true;
  $("#mode").textContent = "";
  const signsIn = state.config.signs_in;
  $("#signin").hidden = !signsIn;
  if (signsIn) {
    $("#start-help").textContent = "This agent remembers customers, so the channel signs you in first. Pick a lab customer, then start.";
    $("#customer").replaceChildren(
      ...state.config.customers.map((c) => {
        const option = el("option", null, `${c.customer_id} - ${c.name}`);
        option.value = c.customer_id;
        return option;
      }),
    );
  } else {
    $("#start-help").textContent = "Tell me what's wrong with your connection. I check your line, your area and your modem, and bring in a person when needed.";
  }
  $("#suggestions").replaceChildren(
    ...(signsIn ? SIGNED_IN_SUGGESTIONS : SUGGESTIONS).map((text) => {
      const chip = el("button", "chip", text);
      chip.type = "button";
      chip.addEventListener("click", () => begin(text));
      return chip;
    }),
  );
}

async function begin(firstMessage) {
  $("#start-error").textContent = "";
  try {
    const customer_id = state.config.signs_in ? $("#customer").value : null;
    const conversation = await postJSON("/api/conversations", { customer_id });
    sessionStorage.setItem(STORAGE_KEY, conversation.id);
    connect(conversation.id);
    if (firstMessage) await postJSON(`/api/conversations/${conversation.id}/messages`, { text: firstMessage });
  } catch (error) {
    $("#start-error").textContent = error.message;
  }
}

$("#begin").addEventListener("click", () => begin(null));

function connect(id) {
  state.source?.close();
  state.source = listen(`/api/conversations/${id}/events`, (event) => {
    if (event.type === "snapshot") {
      state.conversation = event.conversation;
      $("#start").hidden = true;
      $("#messages").hidden = $("#dock").hidden = $("#new").hidden = false;
      $("#messages").replaceChildren();
      for (const message of event.conversation.transcript) upsertMessage($("#messages"), message, "customer", approve);
      scrollToEnd($("#messages"));
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
  $("#typing").textContent = c.busy ? "SupportBuddy is working on it" : "";
  const closed = c.mode === "closed";
  $("#text").disabled = $("#send").disabled = closed || c.busy;
  $("#text").placeholder = c.mode === "waiting" ? "An advisor will join shortly; you can keep writing" : "Write your message";
  $("#composer").hidden = $("#hint").hidden = closed;
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
    input.clear();
  } catch (error) {
    $("#error").textContent = error.message;
  }
});

async function approve(message, approved) {
  try {
    await postJSON(`/api/conversations/${state.conversation.id}/approvals`, { interrupt_id: message.interrupt_id, approved });
  } catch (error) {
    $("#error").textContent = error.message;
  }
}

function startOver() {
  sessionStorage.removeItem(STORAGE_KEY);
  state.source?.close();
  showStart();
}

$("#restart").addEventListener("click", startOver);
$("#new").addEventListener("click", startOver);

init();
