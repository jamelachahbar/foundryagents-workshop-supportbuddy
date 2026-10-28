"""Local bind settings and browser URLs for the lab servers."""

import os


def devui_host() -> str:
    return os.environ.get("DEVUI_HOST", "0.0.0.0" if os.getenv("CODESPACES") == "true" else "127.0.0.1")


def app_url(port: int) -> str:
    name = os.getenv("CODESPACE_NAME")
    domain = os.getenv("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN")
    if os.getenv("CODESPACES") == "true" and name and domain:
        return f"https://{name}-{port}.{domain}"
    return f"http://127.0.0.1:{port}"