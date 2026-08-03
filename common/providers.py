PROVIDERS = {
    "chatgpt": {"label": "ChatGPT", "type": "browser", "color": "#10A37F"},
    "claude": {"label": "Claude", "type": "browser", "color": "#D97757"},
    "gemini": {"label": "Gemini", "type": "browser", "color": "#4285F4"},
    "cursor": {"label": "Cursor", "type": "ide", "color": "#6C5CE7"},
    "vscode": {"label": "VS Code", "type": "ide", "color": "#3794FF"},
    "antigravity": {"label": "Antigravity IDE", "type": "ide", "color": "#EC4899"},
}

BROWSER_PROCESS_NAMES = {"chrome.exe", "msedge.exe", "brave.exe", "opera.exe"}

ALERT_COLOR = "#EF4444"
CUSTOM_COLOR = "#F59E0B"
FALLBACK_COLOR = "#6B7280"


def color_for(provider_key: str | None, display_name: str | None, kind: str) -> str:
    if kind == "alert":
        return ALERT_COLOR
    if provider_key and provider_key in PROVIDERS:
        return PROVIDERS[provider_key]["color"]
    if provider_key or display_name:
        return CUSTOM_COLOR
    return FALLBACK_COLOR
