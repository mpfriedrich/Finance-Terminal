""".streamlit/config.toml: the look may change, the network settings may not (R24,
CLAUDE.md section 6.11). The audit of 02.10.2026 changed [theme] only."""
import tomllib
from pathlib import Path

CONFIG = tomllib.loads((Path(__file__).resolve().parents[1] / ".streamlit" /
                        "config.toml").read_text())


def test_the_app_still_refuses_foreign_pages_and_hosts():
    server = CONFIG["server"]
    assert server["enableCORS"] is True
    assert server["enableXsrfProtection"] is True
    assert server["allowedHosts"] == ["localhost", "127.0.0.1", "*.local"]


def test_the_terminal_theme():
    theme = CONFIG["theme"]
    assert theme["base"] == "dark" and theme["backgroundColor"] == "#000000"
    assert theme["primaryColor"] == theme["chartCategoricalColors"][0] == "#FF9E1B"
    assert theme["baseRadius"] == "none"
