"""DevUI branding must apply to DevUI's real start page and keep its app scripts."""

from devui_branding import BRAND_NAME, ICON_FILE, branded_index


def test_branded_index_keeps_devui_app_and_swaps_title_and_icon():
    page = branded_index(ICON_FILE.read_text(encoding="utf-8"))

    assert "./assets/index.js" in page
    assert f"<title>{BRAND_NAME} · DevUI</title>" in page
    assert 'href="data:image/svg+xml;base64,' in page
    assert 'href="agentframework.svg"' not in page
    assert page.count("<script>") == 1
