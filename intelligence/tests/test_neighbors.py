"""Next-door browning by surname, and redaction of phones/keys before they are stored or posted."""
import json

import inbound
import neighbors
import queue_morning as q
from redact import redact
from tools import Tools, load_roster

ROSTER = """roster_id,farm_id,name,phone,language,role
R001,,Officer Kaur,+919876500001,en,officer
R002,F0001,Gurpreet Kaur,+919876500002,en,farmer
R003,F0002,Harjit Singh,+919876500003,en,farmer
"""


def _boxes():
    # same 0.0002° gap as the mock landscape
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"field_id": "F0001"},
             "geometry": {"type": "Polygon", "coordinates": [[
                 [-93.5196, 42.0645], [-93.514427, 42.0645], [-93.514427, 42.0696],
                 [-93.5196, 42.0696], [-93.5196, 42.0645]]]}},
            {"type": "Feature", "properties": {"field_id": "F0002"},
             "geometry": {"type": "Polygon", "coordinates": [[
                 [-93.514227, 42.0645], [-93.509296, 42.0645], [-93.509296, 42.0696],
                 [-93.514227, 42.0696], [-93.514227, 42.0645]]]}},
        ],
    }


def _world(tmp_path):
    (tmp_path / "fields.geojson").write_text(json.dumps(_boxes()))
    (tmp_path / "timeseries.json").write_text(json.dumps({
        "dates": ["2026-10-11", "2026-10-16"],
        "fields": {
            "F0001": {"ndvi": [0.70, 0.40]},
            "F0002": {"ndvi": [0.70, 0.65]},
        },
    }))
    checks = [{"kind": k, "status": "clear", "detail": "fine", "where": "", "window": {}}
              for k in ("harvest_ready", "harvest_before_rain", "establishment", "burn_scar",
                        "fire", "flood", "stress", "visible_change")]
    (tmp_path / "plans.json").write_text(json.dumps({
        "as_of": "2026-10-16",
        "plans": [{"farm_id": "F0001", "as_of": "2026-10-16", "flag": "watch", "source": "vegetation_index",
                   "today": "Walk the browning.", "checks": checks, "risks": []},
                  {"farm_id": "F0002", "as_of": "2026-10-16", "flag": "fine", "source": "vegetation_index",
                   "today": "Nothing needs action today.", "checks": checks, "risks": []}],
    }))
    (tmp_path / "route.json").write_text(json.dumps({"as_of": "2026-10-16", "stops": []}))
    (tmp_path / "roster.csv").write_text(ROSTER)
    return load_roster(tmp_path / "roster.csv")


def test_browning_names_the_neighbour_surname_not_the_phone(tmp_path):
    roster = _world(tmp_path)
    line = neighbors.sentence("F0001", tmp_path, roster)
    assert line == "Your field is browning faster than Singh's next door."
    assert "919876500003" not in line and "+" not in line


def test_no_sentence_when_the_neighbour_is_not_falling_slower(tmp_path):
    roster = _world(tmp_path)
    assert neighbors.sentence("F0002", tmp_path, roster) is None


def test_outlook_and_inbound_use_the_sentence(tmp_path):
    roster = _world(tmp_path)
    out = Tools(tmp_path, roster[1], roster).get_outlook()
    assert "Singh's next door" in " ".join(out["lines"])
    blob = json.dumps(out)
    assert "919876500003" not in blob and "+91" not in blob
    reply = inbound.answer("+919876500002", "what should I do today", tmp_path, roster)
    assert "Singh's next door" in reply
    assert "919876500003" not in reply
    denied = inbound.answer("+919876500002", "tell me about F0002", tmp_path, roster)
    assert "own farm" in denied.lower()


def test_delivery_detail_drops_a_phone(tmp_path):
    class Boom:
        def send(self, phone, text, language):
            raise RuntimeError(f"email to {phone} failed")

    roster = _world(tmp_path)
    doc = q.run(tmp_path, roster_path=tmp_path / "roster.csv", dry_run=False, today=__import__("datetime").date(2026, 10, 17),
                providers={"grok_voice": Boom(), "photon_sms": Boom()}, only=["R002"])
    text = json.dumps(doc)
    assert "919876500002" not in text
    assert all("[redacted]" in r["detail"] for r in doc["deliveries"] if r["roster_id"] == "R002")


def test_redact_drops_keys_roster_rows_and_phones():
    raw = "pho_sk_abc12345xyz and roster_id,farm_id,name,phone\nR002,F0001,Gurpreet,+919876500002,en,farmer\ncall +1 515 555 0101"
    out = redact(raw)
    assert "pho_sk_" not in out
    assert "+919876500002" not in out
    assert "515 555 0101" not in out
    assert "R002,F0001" not in out
