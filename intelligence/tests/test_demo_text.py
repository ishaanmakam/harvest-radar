"""demo_text.py: today's plan for one farm, texted to one phone now. Dry by default; --send goes through Photon."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import demo_text  # noqa: E402
import providers  # noqa: E402

MOCK = str(Path(__file__).resolve().parents[2] / "data" / "mock")


class _Photon:
    def __init__(self):
        self.sent = []

    def send(self, phone, text, language):
        self.sent.append((phone, text, language))
        return {"id": "msg_1"}


def test_dry_run_prints_the_plan_and_sends_nothing(capsys, monkeypatch):
    photon = _Photon()
    monkeypatch.setattr(providers, "photon_from_env", lambda env=None, http=None: photon)
    assert demo_text.main(["--data", MOCK, "--phone", "+1 515 555 0100", "--farm", "F0001"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Overfield morning plan for farm F0001")
    assert "dry run: nothing sent" in out and "+15155550100" in out
    assert photon.sent == []


def test_send_goes_through_photon(capsys, monkeypatch):
    photon = _Photon()
    monkeypatch.setattr(providers, "photon_from_env", lambda env=None, http=None: photon)
    assert demo_text.main(["--data", MOCK, "--phone", "+15155550100", "--farm", "F0002", "--send"]) == 0
    assert len(photon.sent) == 1
    phone, text, language = photon.sent[0]
    assert phone == "+15155550100" and language == "en" and text.startswith("Overfield morning plan for farm F0002")
    assert "sent to +15155550100" in capsys.readouterr().out


def test_unknown_farm_and_bad_phone_exit_with_a_message(monkeypatch):
    monkeypatch.setattr(providers, "photon_from_env", lambda env=None, http=None: _Photon())
    with pytest.raises(SystemExit, match="no plan for farm F9999"):
        demo_text.main(["--data", MOCK, "--phone", "+15155550100", "--farm", "F9999"])
    with pytest.raises(SystemExit, match="not a phone number"):
        demo_text.main(["--data", MOCK, "--phone", "call me", "--farm", "F0001"])


def test_unconfigured_photon_is_a_clear_exit(monkeypatch):
    monkeypatch.setattr(providers, "photon_from_env", lambda env=None, http=None: None)
    with pytest.raises(SystemExit, match="Photon is not configured"):
        demo_text.main(["--data", MOCK, "--phone", "+15155550100", "--farm", "F0001", "--send"])
