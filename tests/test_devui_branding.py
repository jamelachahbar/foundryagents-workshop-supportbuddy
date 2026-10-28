"""DevUI branding must apply to DevUI's real start page and keep its app scripts."""

from unittest.mock import Mock

import pytest
from agent_framework_devui import DevServer
from fastapi.testclient import TestClient

from devui_branding import BRAND_NAME, ICON_FILE, branded_index
from runtime_settings import app_url, devui_host

import devui_branding


def test_branded_index_keeps_devui_app_and_swaps_title_and_icon():
    page = branded_index(ICON_FILE.read_text(encoding="utf-8"))

    assert "./assets/index.js" in page
    assert f"<title>{BRAND_NAME} · DevUI</title>" in page
    assert 'href="data:image/svg+xml;base64,' in page
    assert 'href="agentframework.svg"' not in page
    assert page.count("<script>") == 1


@pytest.mark.parametrize(
    "codespaces,override,expected_host,expected_url",
    [
        (None, None, "127.0.0.1", "http://127.0.0.1:8090"),
        ("false", None, "127.0.0.1", "http://127.0.0.1:8090"),
        ("true", None, "0.0.0.0", "https://lab-8090.app.github.dev"),
        ("true", "127.0.0.1", "127.0.0.1", "https://lab-8090.app.github.dev"),
        (None, "0.0.0.0", "0.0.0.0", "http://127.0.0.1:8090"),
    ],
)
def test_host_and_url_selection(monkeypatch, codespaces, override, expected_host, expected_url):
    monkeypatch.delenv("CODESPACES", raising=False)
    monkeypatch.delenv("DEVUI_HOST", raising=False)
    monkeypatch.setenv("CODESPACE_NAME", "lab")
    monkeypatch.setenv("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "app.github.dev")
    if codespaces is not None:
        monkeypatch.setenv("CODESPACES", codespaces)
    if override is not None:
        monkeypatch.setenv("DEVUI_HOST", override)

    assert devui_host() == expected_host
    assert app_url(8090) == expected_url


@pytest.mark.parametrize("missing", ["CODESPACE_NAME", "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN"])
def test_url_falls_back_without_forwarding_metadata(monkeypatch, missing):
    monkeypatch.setenv("CODESPACES", "true")
    monkeypatch.setenv("CODESPACE_NAME", "lab")
    monkeypatch.setenv("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "app.github.dev")
    monkeypatch.delenv(missing)

    assert app_url(8070) == "http://127.0.0.1:8070"


def test_codespaces_devui_accepts_forwarded_host_and_requires_token(monkeypatch):
    monkeypatch.setenv("CODESPACES", "true")
    monkeypatch.delenv("DEVUI_HOST", raising=False)
    server = DevServer(host=devui_host(), auth_token="offline-test-token")
    with TestClient(server.get_app(), base_url="https://x-8090.app.github.dev") as client:
        assert client.get("/v1/entities", headers={"Authorization": "Bearer offline-test-token"}).status_code == 200
        assert client.get("/v1/entities").status_code == 401
        assert client.get("/v1/entities", headers={"Authorization": "Bearer wrong-token"}).status_code == 401


def test_network_devui_requires_authentication(monkeypatch):
    monkeypatch.delenv("DEVUI_AUTH_TOKEN", raising=False)
    with pytest.raises(ValueError, match="required"):
        DevServer(host="0.0.0.0")
    with pytest.raises(ValueError, match="cannot be disabled"):
        DevServer(host="0.0.0.0", auth_enabled=False)


@pytest.mark.parametrize("codespaces", [False, True])
@pytest.mark.parametrize("missing_icon", [False, True])
def test_serve_uses_selected_host_and_only_opens_browser_locally(monkeypatch, tmp_path, codespaces, missing_icon):
    monkeypatch.setenv("CODESPACES", "true" if codespaces else "false")
    monkeypatch.delenv("DEVUI_HOST", raising=False)
    timer = Mock()
    run_server = Mock()
    serve = Mock()
    server_factory = Mock(wraps=DevServer)
    monkeypatch.setattr(devui_branding.threading, "Timer", timer)
    monkeypatch.setattr(devui_branding.uvicorn, "run", run_server)
    monkeypatch.setattr(devui_branding, "serve", serve)
    monkeypatch.setattr(devui_branding, "DevServer", server_factory)
    if missing_icon:
        monkeypatch.setattr(devui_branding, "ICON_FILE", tmp_path / "missing.svg")

    agent = object()
    devui_branding.serve_branded(agent, 8090, "offline-test-token")

    host = "0.0.0.0" if codespaces else "127.0.0.1"
    if missing_icon:
        serve.assert_called_once_with(
            entities=[agent], port=8090, host=host, auto_open=not codespaces, auth_token="offline-test-token",
        )
        server_factory.assert_not_called()
        timer.assert_not_called()
    else:
        server_factory.assert_called_once_with(port=8090, host=host, auth_token="offline-test-token")
        assert run_server.call_args.kwargs == {"host": host, "port": 8090, "log_level": "info"}
        if codespaces:
            timer.assert_not_called()
        else:
            timer.assert_called_once_with(2.0, devui_branding.webbrowser.open, ["http://127.0.0.1:8090"])
            timer.return_value.start.assert_called_once_with()
