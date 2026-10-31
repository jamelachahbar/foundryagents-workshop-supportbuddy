"""Optional real-browser checks: run with `uv run --with playwright python -m pytest ...`."""

import asyncio
import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn

playwright = pytest.importorskip("playwright.sync_api")

from contact_centre.centre import ContactCentre
from contact_centre.server import create_app
from lab_backend import backend


@pytest.fixture
def application(runbook_mcp_result):
    calls = []

    async def runner(path, body, *, buffered=False, gate=None, tool_gates=None, extra_tools=(), handover_gate=None,
                     text_gate=None):
        calls.append(body)
        if "/assist/" in path:
            yield {"type": "TEXT_MESSAGE_CONTENT", "messageId": "draft", "delta": "Please check "}
            yield {"type": "TEXT_MESSAGE_CONTENT", "messageId": "draft", "delta": "the modem power cable."}
            yield {"type": "RUN_FINISHED"}
            return
        while gate is not None and not gate.is_set():
            await asyncio.sleep(0.01)
        values = [
            ("identify_customer", backend.identify_customer(customer_id="CUST-1002")),
            ("get_line_status", backend.get_line_status("LINE-2002")),
            ("get_known_incidents", backend.get_known_incidents(postcode="MV-0002")),
            ("check_equipment", backend.check_equipment("CUST-1002")),
            ("knowledge_base_retrieve", runbook_mcp_result),
        ] + list(extra_tools)
        for index, (name, value) in enumerate(values):
            call_id = f"{body['runId']}_{index}"
            yield {"type": "TOOL_CALL_START", "toolCallId": call_id, "toolCallName": name}
            if tool_gates is not None:
                while not tool_gates[index].is_set():
                    await asyncio.sleep(0.01)
            elif not buffered:
                await asyncio.sleep(0.1)
            yield {"type": "TOOL_CALL_RESULT", "toolCallId": call_id, "content": json.dumps(value)}
        yield {"type": "TOOL_CALL_START", "toolCallId": "handover", "toolCallName": "escalate_to_human"}
        while handover_gate is not None and not handover_gate.is_set():
            await asyncio.sleep(0.01)
        yield {"type": "TOOL_CALL_RESULT", "toolCallId": "handover", "content": json.dumps(
            backend.escalate_to_human(
                reported_problem="Synthetic browser test", capabilities_checked=["get_line_status"],
                evidence=["Synthetic browser test evidence"], unresolved_issue="Test",
                recommended_next_investigation="Test", customer_id="CUST-1002",
            ),
        )}
        yield {"type": "TEXT_MESSAGE_CONTENT", "messageId": "answer", "delta": "A human advisor "}
        while text_gate is not None and not text_gate.is_set():
            await asyncio.sleep(0.01)
        yield {"type": "TEXT_MESSAGE_CONTENT", "messageId": "answer", "delta": "can help."}
        yield {"type": "RUN_FINISHED"}

    def serve(enabled, buffered, gate, tool_gates, extra_tools, handover_gate, text_gate):
        async def run(path, body):
            async for event in runner(path, body, buffered=buffered, gate=gate, tool_gates=tool_gates,
                                      extra_tools=extra_tools, handover_gate=handover_gate, text_gate=text_gate):
                yield event

        centre = ContactCentre(run, signs_in=False, agent_label="test", evidence_panel=enabled)
        app = create_app(centre, {})
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        base = f"http://127.0.0.1:{listener.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        return base, server, thread, listener

    servers = []
    gates = []

    def start(enabled=True, *, buffered=False, gate=None, tool_gates=None, extra_tools=(), handover_gate=None,
              text_gate=None):
        if gate is not None:
            gates.append(gate)
        gates.extend(tool_gates or [])
        if handover_gate is not None:
            gates.append(handover_gate)
        if text_gate is not None:
            gates.append(text_gate)
        server = serve(enabled, buffered, gate, tool_gates, extra_tools, handover_gate, text_gate)
        servers.append(server)
        base = server[0]
        for _ in range(100):
            try:
                response = httpx.get(f"{base}/api/config", timeout=1, trust_env=False)
                response.raise_for_status()
                return base
            except httpx.ConnectError:
                time.sleep(0.05)
        pytest.fail("Test application did not become ready")

    yield start, calls
    for gate in gates:
        gate.set()
    for _, server, thread, listener in servers:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()


@pytest.mark.parametrize("enabled", [False, True])
def test_text_deltas_reach_both_browsers_before_the_answer_and_run_finish(application, enabled):
    start, calls = application
    text_gate = threading.Event()
    base = start(enabled, buffered=True, text_gate=text_gate)
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        try:
            chat = browser.new_page()
            desk = browser.new_page()
            failures = []
            for page in (chat, desk):
                page.on("pageerror", lambda error: failures.append(str(error)))
            chat.goto(base + "/chat")
            desk.goto(base + "/desk")
            chat.locator("#begin").click()
            playwright.expect(chat.locator("#dock")).to_be_visible()
            desk.locator("#queue li").first.click()
            chat.locator("#text").fill("Stream the synthetic answer.")
            chat.locator("#send").click()
            ids = []
            for page in (chat, desk):
                answer = page.locator("#messages .msg.agent")
                playwright.expect(answer).to_have_count(1)
                playwright.expect(answer.locator(".md-line")).to_have_text("A human advisor ")
                playwright.expect(page.locator("#mode")).to_have_text("SupportBuddy (AI)")
                assert "can help." not in answer.inner_text()
                ids.append(answer.get_attribute("data-id"))
            assert ids[0] == ids[1]
            text_gate.set()
            for page, message_id in zip((chat, desk), ids):
                answer = page.locator("#messages .msg.agent")
                playwright.expect(answer.locator(".md-line")).to_have_text("A human advisor can help.")
                playwright.expect(page.locator("#mode")).to_have_text("Waiting for a human advisor")
                assert answer.count() == 1
                assert answer.get_attribute("data-id") == message_id
            if enabled:
                button = chat.locator('[data-capability="check_equipment"] button')
                button.hover()
                assert button.evaluate("(node) => getComputedStyle(node).backgroundColor") == "rgba(0, 0, 0, 0)"
                button.click()
                playwright.expect(chat.locator("#diagnostic-panel")).to_be_visible()
                marker = button.locator(".evidence-marker")
                assert marker.evaluate("(node) => node.getAnimations().some((animation) => animation.effect.getTiming().iterations === 2)")
                assert marker.evaluate("(node) => getComputedStyle(node).color") == "rgb(242, 165, 22)"
                chat.keyboard.press("Escape")
                button.press("Tab")
                chat.keyboard.press("Shift+Tab")
                playwright.expect(button).to_be_focused()
                assert marker.evaluate("(node) => getComputedStyle(node).outlineStyle") == "solid"
                assert button.evaluate("(node) => getComputedStyle(node).outlineStyle") == "none"
                marker.evaluate("(node) => node.getAnimations().forEach((animation) => animation.finish())")
                chat.emulate_media(reduced_motion="reduce")
                button.click()
                assert marker.evaluate("(node) => node.getAnimations().length") == 0
                assert button.evaluate("(node) => getComputedStyle(node).backgroundColor") == "rgba(0, 0, 0, 0)"
            assert len(calls) == 1
            assert failures == []
        finally:
            text_gate.set()
            browser.close()


def test_customer_and_desk_live_panel_reconnect_isolation_and_mobile(application):
    start, calls = application
    base = start()
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        try:
            chat = browser.new_page(viewport={"width": 390, "height": 844})
            desk = browser.new_page(viewport={"width": 1440, "height": 1000})
            failures = []
            chat.on("pageerror", lambda error: failures.append(str(error)))
            desk.on("pageerror", lambda error: failures.append(str(error)))
            chat.goto(base + "/chat")
            desk.goto(base + "/desk")
            chat.locator("#begin").click()
            playwright.expect(chat.locator("#dock")).to_be_visible()
            assert chat.locator(".evidence").count() == 0
            desk.locator("#queue li").first.click()
            chat.evaluate("""() => {
                window.evidenceChanges = [];
                new MutationObserver(() => {
                    window.evidenceChanges.push(document.querySelector("#messages").textContent);
                    window.firstEvidence ??= document.querySelector(".evidence");
                })
                    .observe(document.querySelector("#messages"), {subtree: true, childList: true});
            }""")
            chat.locator("#text").fill("Help me with this synthetic connection.")
            chat.locator("#send").click()
            playwright.expect(chat.locator("#messages .evidence-flow .evidence-status")).to_have_text(["Completed"] * 5)
            playwright.expect(desk.locator("#messages .evidence-flow .evidence-status")).to_have_text(["Completed"] * 5)
            playwright.expect(chat.locator('[data-capability="escalate_to_human"] .evidence-status')).to_have_text("Waiting for advisor")
            playwright.expect(chat.locator("#mode")).to_have_text("Waiting for a human advisor")
            playwright.expect(chat.locator(".evidence-live")).to_have_text("Agent turn finished")
            assert "Checking" in " ".join(chat.evaluate("window.evidenceChanges"))
            assert chat.evaluate('window.firstEvidence === document.querySelector("#messages .evidence")')
            assert chat.evaluate('document.querySelector(".evidence-message").previousElementSibling.classList.contains("customer")')
            chat.locator('[data-capability="get_line_status"] button').click()
            desk.locator('[data-capability="check_equipment"] button').click()
            assert "download 0/1000 Mbps" in chat.locator("#diagnostic-panel").inner_text()
            assert "Not responding" in desk.locator("#diagnostic-panel").inner_text()
            assert chat.locator(".evidence img, .evidence script").count() == 0
            assert "private text" not in chat.locator(".evidence").inner_text()
            chat.get_by_role("button", name="Close traces").click()
            desk.get_by_role("button", name="Close traces").click()
            assert chat.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), chat.evaluate("""() =>
                [...document.querySelectorAll("body *")].filter((node) =>
                    node.getBoundingClientRect().right > window.innerWidth
                ).slice(0, 8).map((node) => [node.tagName, node.id, node.className, node.getBoundingClientRect().right])
            """)
            assert len(calls) == 1
            assert chat.evaluate("sessionStorage.getItem('supportbuddy-conversation')") is not None
            chat.reload()
            playwright.expect(chat.locator(".evidence-flow .evidence-status")).to_have_text(["Completed"] * 5)
            desk.reload()
            desk.locator("#queue li").first.click()
            playwright.expect(desk.locator(".evidence-flow .evidence-status")).to_have_text(["Completed"] * 5)
            desk.locator("#take").click()
            playwright.expect(chat.locator("#mode")).to_have_text("Lea (human advisor)")
            playwright.expect(desk.locator(".evidence-flow .evidence-status")).to_have_text(["Completed"] * 5)
            desk.locator("#handback").click()
            playwright.expect(chat.locator("#mode")).to_have_text("SupportBuddy (AI)")
            assert len(calls) == 1
            chat.locator("#new").click()
            playwright.expect(chat.locator(".evidence")).to_be_hidden()
            chat.locator("#begin").click()
            playwright.expect(chat.locator("#dock")).to_be_visible()
            assert chat.locator(".evidence").count() == 0
            desk.locator("#queue li").filter(has_text="Not identified yet").click()
            assert desk.locator(".evidence").count() == 0
            desk.locator("#queue li").filter(has_text="Luc Martin").click()
            playwright.expect(desk.locator(".evidence-flow .evidence-status")).to_have_text(["Completed"] * 5)
            assert failures == []
        finally:
            browser.close()


def test_panel_hidden_when_disabled_and_renderer_treats_markup_as_text(application):
    start, _ = application
    base = start(enabled=False)
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(base + "/chat")
            page.locator("#begin").click()
            playwright.expect(page.locator("#dock")).to_be_visible()
            assert page.locator(".evidence").count() == 0
            page.evaluate("""() => {
                const message = {
                id: "renderer-test", role: "evidence", evidence: [{
                    capability: "check_equipment", label: "<script>alert(1)</script>",
                    status: "failed", finding: "<img src=x onerror=alert(1)>"
                }]
                };
                upsertMessage(document.querySelector("#messages"), message, "customer", null,
                    (source, capability) => showEvidenceDetails(source, capability));
            }""")
            page.locator(".evidence-node button").click()
            assert page.locator(".evidence img, .evidence script").count() == 0
            assert page.locator("#diagnostic-panel img, #diagnostic-panel script").count() == 0
            assert "<img src=x" in page.locator("#diagnostic-panel").inner_text()
            page.keyboard.press("Escape")
            page.evaluate("sessionStorage.setItem('supportbuddy-conversation', '0'.repeat(32))")
            page.reload()
            playwright.expect(page.locator("#begin")).to_be_visible()
            page.locator("#begin").click()
            playwright.expect(page.locator("#dock")).to_be_visible()
            assert page.evaluate("sessionStorage.getItem('supportbuddy-conversation')") != "0" * 32
        finally:
            browser.close()


def test_topology_status_colors_distinguish_tool_success_from_detected_issues(application):
    start, _ = application
    base = start(enabled=False)
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(base + "/chat")
            cases = [
                ("checking", "unknown", "Checking", "rgb(142, 203, 255)"),
                ("completed", "observed", "Completed", "rgb(120, 223, 167)"),
                ("completed", "attention", "Completed", "rgb(242, 165, 22)"),
                ("incomplete", "unknown", "Incomplete", "rgb(242, 165, 22)"),
                ("failed", "unknown", "Failed", "rgb(255, 123, 123)"),
                ("failed", "attention", "Failed", "rgb(255, 123, 123)"),
                ("checking", "attention", "Checking", "rgb(142, 203, 255)"),
                ("previous", "unknown", "Previous evidence", "rgb(154, 161, 181)"),
            ]
            for status, signal, label, color in cases:
                page.evaluate("""([status, signal]) => upsertMessage(document.querySelector("#messages"), {
                    id: "color-test", role: "evidence", evidence_phase: "running", evidence: [{
                        key: "line", capability: "get_line_status", label: "Access line",
                        status, signal, finding: "Synthetic evidence"
                    }]
                }, "customer")""", [status, signal])
                marker = page.locator(".evidence-marker")
                assert marker.evaluate("(node) => getComputedStyle(node).color") == color
                assert marker.evaluate("(node) => getComputedStyle(node).borderTopColor") == color
                playwright.expect(page.locator(".evidence-status")).to_have_text(label)
                if status == "completed":
                    assert page.locator(".evidence-status").evaluate(
                        "(node) => getComputedStyle(node).color"
                    ) == "rgb(120, 223, 167)"
                if signal == "attention":
                    playwright.expect(page.locator(".evidence-attention")).to_have_text("Issue observed")
        finally:
            browser.close()


def test_diagram_parallel_activity_stable_nodes_and_reduced_motion(application, tmp_path):
    start, _ = application
    base = start()
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base + "/chat")
            page.locator("#begin").click()
            playwright.expect(page.locator("#dock")).to_be_visible()
            assert page.locator(".evidence").count() == 0
            page.evaluate("""() => {
                window.diagramRows = [
                    {key: "line", capability: "get_line_status", label: "Line", status: "checking", finding: ""},
                    {key: "modem", capability: "check_equipment", label: "Modem", status: "checking", finding: ""}
                ];
                window.drawDiagram = (phase) => upsertMessage(document.querySelector("#messages"), {
                    id: "diagram-test", role: "evidence", evidence: window.diagramRows, evidence_phase: phase
                }, "customer");
                window.drawDiagram("running");
                window.modemNode = document.querySelector('[data-capability="check_equipment"]');
            }""")
            assert page.locator(".evidence-node.checking").count() == 2
            assert page.locator(".evidence-node").count() == 2
            playwright.expect(page.locator(".evidence-live")).to_have_text("Tools running: Modem, Access line")
            assert page.locator('.evidence-node[aria-busy="true"]').count() == 2
            assert page.locator(".evidence-marker").nth(1).evaluate(
                "(node) => getComputedStyle(node).animationName"
            ) == "evidence-pulse"
            page.evaluate("""() => {
                window.diagramRows[0] = {...window.diagramRows[0], status: "completed", finding: "Sync down", signal: "attention"};
                window.drawDiagram("running");
            }""")
            assert page.evaluate(
                'window.modemNode === document.querySelector(\'[data-capability="check_equipment"]\')'
            )
            assert page.locator(".evidence-node.checking").count() == 1
            assert page.locator('[data-capability="get_line_status"].attention').count() == 1
            page.locator(".evidence").screenshot(path=str(tmp_path / "diagram-desktop.png"))
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.locator(".evidence").screenshot(path=str(tmp_path / "diagram-mobile.png"))
            page.emulate_media(reduced_motion="reduce")
            assert page.locator('[data-capability="check_equipment"] .evidence-marker').evaluate(
                "(node) => getComputedStyle(node).animationName"
            ) == "none"
            assert page.locator(".evidence-link.checking").first.evaluate(
                "(node) => getComputedStyle(node).animationName"
            ) == "none"
            page.evaluate('window.drawDiagram("approval")')
            playwright.expect(page.locator(".evidence-live")).to_have_text("Waiting for your decision")
            page.evaluate('window.drawDiagram("failed")')
            playwright.expect(page.locator(".evidence-live")).to_have_text("Agent turn failed")
            page.evaluate("""() => {
                window.diagramRows = [{
                    key: "books", capability: "knowledge_base_retrieve",
                    label: "Runbooks", status: "checking", finding: ""
                }];
                window.drawDiagram("running");
            }""")
            playwright.expect(page.locator(".evidence-node")).to_have_count(1)
            playwright.expect(page.locator(".evidence-links path")).to_have_count(0)
            page.evaluate("""() => {
                window.diagramRows.push({
                    key: "customer", capability: "identify_customer",
                    label: "Customer", status: "checking", finding: ""
                });
                window.drawDiagram("running");
            }""")
            playwright.expect(page.locator(".evidence-node")).to_have_count(2)
            playwright.expect(page.locator(".evidence-link.guidance")).to_have_count(1)
            assert page.locator(".evidence-link.connection").count() == 0
            page.evaluate('window.diagramRows = []; window.drawDiagram("finished")')
            playwright.expect(page.locator(".evidence-map")).to_be_hidden()
            playwright.expect(page.locator(".evidence-node")).to_have_count(0)
            playwright.expect(page.locator(".evidence-empty")).to_contain_text("No diagnostic tool events")
            assert errors == []
        finally:
            browser.close()


def test_topology_appears_before_buffered_provider_results_and_preserves_wire_updates(application, tmp_path):
    start, calls = application
    gate = threading.Event()
    base = start(buffered=True, gate=gate)
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base + "/chat")
            page.locator("#begin").click()
            playwright.expect(page.locator("#dock")).to_be_visible()
            assert page.locator(".evidence").count() == 0
            page.evaluate("""() => {
                window.streamStates = [];
                const original = upsertMessage;
                window.upsertMessage = (list, message, ...args) => {
                    if (message.role === "evidence") window.streamStates.push(structuredClone(message));
                    return original(list, message, ...args);
                };
            }""")
            page.locator("#text").fill("Check this synthetic connection.")
            page.locator("#send").click()
            playwright.expect(page.locator("#typing")).to_have_text("SupportBuddy is working on it")
            assert page.locator(".evidence").count() == 0
            assert page.locator(".evidence-node").count() == 0
            assert page.locator(".evidence-links path").count() == 0
            assert len(calls) == 1
            page.locator("#traces").click()
            playwright.expect(page.locator("#diagnostic-title")).to_have_text("Conversation traces")
            playwright.expect(page.locator(".diagnostic-event")).to_have_count(1)
            assert page.locator(".diagnostic-event.run_started").count() == 1
            assert page.locator("dialog").count() == 0
            gate.set()
            playwright.expect(page.locator(".evidence-live")).to_have_text("Agent turn finished")
            playwright.expect(page.locator(".evidence-flow .evidence-status")).to_have_text(["Completed"] * 5)
            assert page.locator(".evidence").evaluate("(node) => getComputedStyle(node).backgroundColor") == "rgba(0, 0, 0, 0)"
            assert page.locator(".evidence").evaluate("(node) => getComputedStyle(node).borderTopWidth") == "0px"
            states = page.evaluate("window.streamStates")
            assert any(any(row["status"] == "checking" for row in state["evidence"]) for state in states)
            assert len({state["id"] for state in states}) == 1
            playwright.expect(page.locator(".diagnostic-event")).to_have_count(16)
            playwright.expect(page.locator("#diagnostic-panel")).to_contain_text("escalate_to_human")
            assert page.locator(".diagnostic-event.run_finished").count() == 1
            assert page.locator("#diagnostic-panel").evaluate("(node) => Math.round(node.getBoundingClientRect().right)") == 1280
            assert page.locator(".chat").evaluate("(node) => node.getBoundingClientRect().right") <= 850
            page.get_by_role("button", name="Close traces").click()
            page.locator('[data-capability="knowledge_base_retrieve"] button').click()
            assert "1 documents returned" in page.locator("#diagnostic-panel").inner_text()
            assert "private text" not in page.locator("#diagnostic-panel").inner_text()
            page.get_by_role("button", name="Close traces").click()
            assert "private text" not in page.locator(".evidence").inner_text()
            assert page.locator(".evidence-node.attention").count() == 1
            assert page.locator('[data-capability="check_equipment"].attention').count() == 1
            assert page.locator(".evidence-link.connection").count() == 3
            assert page.locator(".evidence-link.guidance").count() == 1
            assert page.locator(".evidence-flow").evaluate(
                "(node) => getComputedStyle(node).gridTemplateColumns.split(' ').length"
            ) == 4
            page.locator(".evidence").screenshot(path=str(tmp_path / "topology-desktop.png"))
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.locator(".evidence-flow").evaluate(
                "(node) => getComputedStyle(node).gridTemplateColumns.split(' ').length"
            ) == 2
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            page.locator(".evidence").screenshot(path=str(tmp_path / "topology-mobile.png"))
            page.locator("#traces").click()
            playwright.expect(page.locator(".diagnostic-event")).to_have_count(16)
            assert page.locator("#diagnostic-panel").evaluate("(node) => node.getBoundingClientRect().width") == 390
            page.screenshot(path=str(tmp_path / "trace-drawer-mobile.png"))
            page.get_by_role("button", name="Close traces").click()
            assert errors == []
        finally:
            gate.set()
            browser.close()


def test_topology_reveals_nodes_and_links_at_real_tool_starts_in_both_views(application, tmp_path):
    start, calls = application
    gate = threading.Event()
    tool_gates = [threading.Event() for _ in range(5)]
    handover_gate = threading.Event()
    base = start(gate=gate, tool_gates=tool_gates, handover_gate=handover_gate)
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        try:
            chat = browser.new_page(viewport={"width": 1280, "height": 1000})
            desk = browser.new_page(viewport={"width": 1440, "height": 1000})
            errors = []
            for page in (chat, desk):
                page.on("pageerror", lambda error: errors.append(str(error)))
            chat.goto(base + "/chat")
            desk.goto(base + "/desk")
            chat.locator("#begin").click()
            playwright.expect(chat.locator("#dock")).to_be_visible()
            desk.locator("#queue li").first.click()
            chat.locator("#text").fill("Progressively check this synthetic connection.")
            chat.locator("#send").click()
            for page in (chat, desk):
                assert page.locator(".evidence").count() == 0
                assert page.locator(".evidence-node").count() == 0
            gate.set()
            steps = [
                ("identify_customer", "Customer", 0),
                ("get_line_status", "Access line", 0),
                ("get_known_incidents", "Area network", 1),
                ("check_equipment", "Modem", 3),
                ("knowledge_base_retrieve", "Runbook guidance", 4),
            ]
            for index, (capability, label, link_count) in enumerate(steps):
                for page in (chat, desk):
                    playwright.expect(page.locator(".evidence-live")).to_have_text(f"Tool running: {label}")
                    playwright.expect(page.locator(".evidence-node")).to_have_count(index + 1)
                    playwright.expect(page.locator(f'[data-capability="{capability}"] .evidence-status')).to_have_text("Checking")
                    playwright.expect(page.locator(".evidence-links path")).to_have_count(link_count)
                    playwright.expect(page.locator(".evidence-progress .progress-event.started")).to_have_count(index + 1)
                    assert page.locator(".evidence-progress").get_attribute("aria-live") == "polite"
                    assert page.locator(".evidence-node.checking").count() == 1
                    assert page.locator(".evidence-status.not_checked").count() == 0
                    if index:
                        assert page.locator(".evidence-node.completed").count() == index
                    for future, _, _ in steps[index + 1:]:
                        assert page.locator(f'[data-capability="{future}"]').count() == 0
                    if index == 0:
                        button = page.locator('[data-capability="identify_customer"] button')
                        button.focus()
                        button.press("Enter")
                        playwright.expect(page.locator("#diagnostic-panel")).to_be_visible()
                        playwright.expect(page.locator("#diagnostic-panel")).to_contain_text("Waiting for the tool result.")
                        playwright.expect(page.locator(".diagnostic-event")).to_have_count(1)
                        assert page.locator("#diagnostic-panel .diagnostic-emblem .evidence-marker").count() == 1
                        assert page.locator("dialog").count() == 0
                        assert page.locator("#diagnostic-panel").evaluate("(node) => getComputedStyle(node).position") == "fixed"
                        assert page.locator(".evidence-node details").count() == 0
                        if page == chat:
                            page.screenshot(path=str(tmp_path / "trace-drawer-checking.png"))
                    if index == 1:
                        playwright.expect(page.locator("#diagnostic-panel")).to_contain_text("Customer identified")
                        playwright.expect(page.locator(".diagnostic-event")).to_have_count(2)
                        if page == chat:
                            page.screenshot(path=str(tmp_path / "trace-drawer-completed.png"))
                        page.keyboard.press("Escape")
                        playwright.expect(page.locator('[data-capability="identify_customer"] button')).to_be_focused()
                tool_gates[index].set()
            for page in (chat, desk):
                playwright.expect(page.locator(".evidence-live")).to_have_text("Tool running: Human advisor")
                playwright.expect(page.locator('[data-capability="escalate_to_human"] .evidence-status')).to_have_text("Checking")
                playwright.expect(page.locator(".evidence-link.workflow")).to_have_count(1)
                playwright.expect(page.locator(".evidence-progress")).to_contain_text("Preparing the handover to a human advisor.")
                assert page.locator(".progress-event.advisor_joined").count() == 0
            handover_gate.set()
            for page in (chat, desk):
                playwright.expect(page.locator(".evidence-live")).to_have_text("Agent turn finished")
                playwright.expect(page.locator(".evidence-flow .evidence-status")).to_have_text(["Completed"] * 5)
                playwright.expect(page.locator('[data-capability="escalate_to_human"] .evidence-status')).to_have_text("Waiting for advisor")
                playwright.expect(page.locator(".progress-event")).to_have_count(16)
            desk.locator("#take").click()
            for page in (chat, desk):
                human = page.locator('[data-capability="escalate_to_human"]')
                playwright.expect(human.locator(".evidence-status")).to_have_text("Advisor joined")
                assert human.locator(".evidence-marker").evaluate("(node) => getComputedStyle(node).color") == "rgb(120, 223, 167)"
                human.locator("button").click()
                playwright.expect(page.locator(".diagnostic-event.advisor_joined")).to_have_count(1)
                assert page.locator(".diagnostic-event").count() == 5
            desk.get_by_role("button", name="Close traces").click()
            desk.locator("#handback").click()
            for page in (chat, desk):
                playwright.expect(page.locator('[data-capability="escalate_to_human"] .evidence-status')).to_have_text("Back with SupportBuddy")
                playwright.expect(page.locator(".progress-event.handed_back")).to_have_count(1)
            desk.locator("#take").click()
            desk.locator("#close").click()
            for page in (chat, desk):
                playwright.expect(page.locator('[data-capability="escalate_to_human"] .evidence-status')).to_have_text("Chat closed")
                playwright.expect(page.locator(".progress-event.case_closed")).to_have_count(1)
            assert len(calls) == 1
            assert errors == []
        finally:
            gate.set()
            for tool_gate in tool_gates:
                tool_gate.set()
            handover_gate.set()
            browser.close()


def test_new_tools_render_individual_branches_progress_logs_and_animated_connections(application):
    start, calls = application
    gate = threading.Event()
    tool_gates = [threading.Event() for _ in range(7)]
    for tool_gate in tool_gates[:5]:
        tool_gate.set()
    base = start(
        buffered=True, gate=gate, tool_gates=tool_gates,
        extra_tools=[
            ("future_tool_private_name", {"private": "private result"}),
            ("another_future_tool", {"private": "<script>private payload</script>"}),
        ],
    )
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 1000})
            failures = []
            page.on("pageerror", lambda error: failures.append(str(error)))
            page.goto(base + "/chat")
            page.locator("#begin").click()
            playwright.expect(page.locator("#dock")).to_be_visible()
            page.locator("#text").fill("Run additional synthetic checks.")
            page.locator("#send").click()
            assert page.locator(".evidence-node").count() == 0
            gate.set()
            unknown = page.locator('[data-capability^="other_tool:"]')
            playwright.expect(unknown).to_have_count(1)
            playwright.expect(unknown.locator(".evidence-status")).to_have_text("Checking")
            playwright.expect(page.locator(".evidence-link.workflow")).to_have_count(1)
            animation = page.evaluate("""() => {
                const link = document.querySelector(".evidence-link.workflow");
                const animation = link.getAnimations().find((item) => item.animationName === "evidence-connect");
                if (!animation) return null;
                animation.pause();
                animation.currentTime = 100;
                const earlier = parseFloat(getComputedStyle(link).strokeDashoffset);
                animation.currentTime = 400;
                const later = parseFloat(getComputedStyle(link).strokeDashoffset);
                animation.finish();
                return {earlier, later};
            }""")
            assert animation is not None
            assert 0 <= animation["later"] < animation["earlier"] <= 1
            playwright.expect(page.locator(".evidence-progress")).to_contain_text("Running an additional support tool.")
            assert "future_tool_private_name" not in page.locator(".evidence").inner_text()
            tool_gates[5].set()
            playwright.expect(unknown).to_have_count(2)
            playwright.expect(unknown.locator(".evidence-status")).to_have_text(["Result received", "Checking"])
            playwright.expect(page.locator(".evidence-link.workflow")).to_have_count(2)
            assert page.locator(".evidence-branches .completed").count() == 0
            unknown.first.locator("button").click()
            playwright.expect(page.locator(".diagnostic-event")).to_have_count(2)
            assert "private result" not in page.locator("#diagnostic-panel").inner_text()
            page.get_by_role("button", name="Close traces").click()
            page.emulate_media(reduced_motion="reduce")
            for link in page.locator(".evidence-link.workflow").all():
                assert link.evaluate("(node) => getComputedStyle(node).animationName") == "none"
                assert link.evaluate("(node) => getComputedStyle(node).strokeDashoffset") == "0px"
            tool_gates[6].set()
            playwright.expect(page.locator(".evidence-live")).to_have_text("Agent turn finished")
            playwright.expect(unknown.locator(".evidence-status")).to_have_text(["Result received"] * 2)
            playwright.expect(page.locator(".progress-event")).to_have_count(20)
            page.reload()
            playwright.expect(unknown).to_have_count(2)
            playwright.expect(page.locator(".progress-event")).to_have_count(20)
            assert "private" not in page.locator(".evidence").inner_text()
            assert page.locator(".evidence script").count() == 0
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
            assert len(calls) == 1
            assert failures == []
        finally:
            gate.set()
            for tool_gate in tool_gates:
                tool_gate.set()
            browser.close()


def test_optional_desk_reply_draft_populates_composer_without_sending_or_overwriting(application):
    start, calls = application
    base = start()
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        try:
            chat = browser.new_page()
            desk = browser.new_page()
            chat.goto(base + "/chat")
            desk.goto(base + "/desk")
            chat.locator("#begin").click()
            playwright.expect(chat.locator("#dock")).to_be_visible()
            desk.locator("#queue li").first.click()
            desk.locator("#take").click()
            playwright.expect(desk.locator("#mode")).to_have_text("Lea (human advisor)")
            before = chat.locator("#messages").inner_text()
            desk.locator('[data-prompt="Suggest a reply to the customer."]').click()
            use = desk.get_by_role("button", name="Use suggested reply", exact=True)
            playwright.expect(use).to_be_enabled()
            desk.locator("#text").fill("My existing draft")
            use.click()
            playwright.expect(desk.locator("#assist-error")).to_contain_text("already contains a draft")
            playwright.expect(desk.locator("#text")).to_have_value("My existing draft")
            desk.locator("#text").fill("")
            use.click()
            playwright.expect(desk.locator("#text")).to_have_value("Please check the modem power cable.")
            assert chat.locator("#messages").inner_text() == before
            assert len(calls) == 1
            desk.locator("#handback").click()
            playwright.expect(use).to_be_disabled()
            assert chat.locator("#messages .advisor").count() == 0
        finally:
            browser.close()


def test_richer_approval_card_keeps_context_separate_from_explicit_decision(application):
    start, _ = application
    base = start()
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(base + "/chat")
            page.locator("#begin").click()
            playwright.expect(page.locator("#dock")).to_be_visible()
            page.evaluate("""() => {
                window.approvalDecisions = [];
                window.approvalMessage = {
                    id: "approval-ui", role: "approval", tool: "restart_modem",
                    text: "Restart the modem remotely?", status: "pending",
                    arguments: {customer_id: "CUST-1002"},
                    action_details: {
                        impact: "The connection drops for about 3 minutes.",
                        evidence: [{
                            capability: "check_equipment", label: "Modem EQ-3002",
                            status: "completed", finding: "<img src=x> Not responding; packet loss 100%"
                        }]
                    }
                };
                upsertMessage(document.querySelector("#messages"), window.approvalMessage, "customer",
                    (message, approved) => window.approvalDecisions.push(approved));
            }""")
            playwright.expect(page.locator(".approval-context")).to_contain_text("about 3 minutes")
            playwright.expect(page.locator(".approval-context")).to_contain_text("Modem EQ-3002 (completed)")
            assert page.locator(".approval-context img, .approval-context script").count() == 0
            assert page.evaluate("window.approvalDecisions") == []
            page.get_by_role("button", name="Decline", exact=True).click()
            assert page.evaluate("window.approvalDecisions") == [False]
            playwright.expect(page.get_by_role("button", name="Approve", exact=True)).to_be_disabled()
            page.evaluate("""() => {
                window.approvalMessage.status = "rejected";
                upsertMessage(document.querySelector("#messages"), window.approvalMessage, "customer");
            }""")
            playwright.expect(page.locator(".decision")).to_contain_text("rejected")
            assert page.locator(".approval .buttons").count() == 0
        finally:
            browser.close()
