"""Make the flat intelligence/ modules importable from the tests (they use `from common import ...`)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    """The real .env never reaches a test: load_env only fills variables that are unset, so every provider key is
    blanked, the roster path is blanked and the accounts database is a temp file. Without this a suite run writes its
    rows into the real accounts database and the provider tests see real keys."""
    for key in ("PHOTON_API_KEY", "PHOTON_PROJECT_ID", "PHOTON_FROM", "PHOTON_WEBHOOK_SECRET", "TWILIO_ACCOUNT_SID",
                "TWILIO_AUTH_TOKEN", "TWILIO_FROM", "XAI_API_KEY", "ROSTER_PATH", "PUBLIC_URL", "PUBLIC_WS_URL", "INBOUND_TOKEN"):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("ACCOUNTS_DB", str(tmp_path / "accounts.sqlite"))
