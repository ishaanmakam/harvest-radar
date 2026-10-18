"""field_env.py: soil and terrain per field from two free services (the auxiliary inputs YieldSAT pairs with Sentinel-2):
SoilGrids 2.0 (organic carbon, clay, sand, pH over 0-30 cm, depth-weighted) and Open-Meteo elevation (height and a slope
from a 100 m stencil). Cached per field; one failing field never costs the others; written to field_env.json."""
import json
from pathlib import Path

import pytest

import field_env as fe

ROOT = Path(__file__).resolve().parents[2]


def soilgrids_doc(soc=(250, 180, 120), clay=(280, 290, 300), sand=(300, 310, 320), ph=(63, 64, 66)):
    """A SoilGrids v2 response: integer values in mapped units, d_factor 10 to reach the target units."""
    def layer(name, vals, mapped, target):
        return {"name": name, "unit_measure": {"d_factor": 10, "mapped_units": mapped, "target_units": target},
                "depths": [{"range": {"top_depth": 0, "bottom_depth": 5}, "label": "0-5cm", "values": {"mean": vals[0]}},
                           {"range": {"top_depth": 5, "bottom_depth": 15}, "label": "5-15cm", "values": {"mean": vals[1]}},
                           {"range": {"top_depth": 15, "bottom_depth": 30}, "label": "15-30cm", "values": {"mean": vals[2]}}]}
    return {"type": "Feature", "properties": {"layers": [layer("soc", soc, "dg/kg", "g/kg"), layer("clay", clay, "g/kg", "%"),
                                                          layer("sand", sand, "g/kg", "%"), layer("phh2o", ph, "pH*10", "pH")]}}


class TestSoil:
    def test_depth_weighted_means_in_target_units(self):
        out = fe.parse_soilgrids(soilgrids_doc())
        # 0-30 cm weights 5, 10, 15 cm: soc (250*5 + 180*10 + 120*15) / 30 = 161.7 dg/kg -> 16.2 g/kg
        assert out["soc_g_kg"] == pytest.approx(16.17, abs=0.01)
        assert out["clay_pct"] == pytest.approx(29.33, abs=0.01) and out["sand_pct"] == pytest.approx(31.33, abs=0.01)
        assert out["ph"] == pytest.approx(6.48, abs=0.01)                       # (63*5 + 64*10 + 66*15) / 30 / 10
        assert out["texture"] == "clay loam"

    def test_missing_layer_or_null_values_read_as_none(self):
        doc = soilgrids_doc()
        doc["properties"]["layers"][0]["depths"][1]["values"]["mean"] = None       # one depth missing: weight the rest
        del doc["properties"]["layers"][3]                                            # no pH layer
        out = fe.parse_soilgrids(doc)
        assert out["soc_g_kg"] == pytest.approx((250 * 5 + 120 * 15) / 20 / 10, abs=0.01) and out["ph"] is None
        assert fe.parse_soilgrids({"properties": {"layers": []}}) == {k: None for k in ("soc_g_kg", "clay_pct", "sand_pct", "ph", "texture")}
        assert fe.parse_soilgrids(None)["soc_g_kg"] is None

    def test_texture_classes(self):
        assert fe.texture_class(10.0, 85.0) == "loamy sand"
        assert fe.texture_class(20.0, 40.0) == "loam"
        assert fe.texture_class(45.0, 20.0) == "clay"
        assert fe.texture_class(None, 20.0) is None


class TestTerrain:
    def test_slope_from_a_stencil(self):
        # centre 300 m; east 302, west 298 (4 m over 200 m = 2%); north 300.5, south 299.5 (0.5%): slope = sqrt(4 + 0.25) = 2.06%
        out = fe.terrain_from_stencil([300.0, 300.5, 299.5, 302.0, 298.0])
        assert out["elevation_m"] == 300.0 and out["slope_pct"] == pytest.approx(2.06, abs=0.01)
        assert out["aspect"] == "west"                                               # downhill towards the lower side (west)
        flat = fe.terrain_from_stencil([300.0, 300.0, 300.0, 300.0, 300.0])
        assert flat["slope_pct"] == 0.0 and flat["aspect"] == "flat"
        assert fe.terrain_from_stencil([300.0, None, 299.0, 302.0, 298.0])["slope_pct"] is None

    def test_stencil_points_are_100_m_apart(self):
        pts = fe.stencil(-93.47, 42.03)
        assert len(pts) == 5 and pts[0] == (-93.47, 42.03)
        assert pts[1][1] - 42.03 == pytest.approx(100 / 111_320, rel=1e-3)          # north
        assert abs(pts[3][0] - (-93.47)) * 111_320 * 0.7427 == pytest.approx(100, rel=0.02)   # east, cos(42 deg)


class TestRun:
    def http_factory(self, soil_fail_for=()):
        calls = {"soil": 0, "elev": 0}

        def http(method, url, headers, body, timeout):
            if "soilgrids" in url:
                calls["soil"] += 1
                if any(f in url for f in soil_fail_for):
                    return 503, b"busy"
                return 200, json.dumps(soilgrids_doc()).encode()
            calls["elev"] += 1
            n = url.split("latitude=")[1].split("&")[0].count(",") + 1
            return 200, json.dumps({"elevation": [300.0 + (i % 5) for i in range(n)]}).encode()
        return http, calls

    def test_run_writes_the_file_and_caches(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir()
        (data / "fields.geojson").write_bytes((ROOT / "data" / "mock" / "fields.geojson").read_bytes())
        http, calls = self.http_factory()
        doc = fe.run(data, cache_dir=tmp_path / "cache", http=http, fetched_at="2026-10-18T07:00Z", limit=12)
        assert set(doc) >= {"fetched_at", "sources", "fields", "landscape", "zones"} and len(doc["fields"]) == 12
        one = next(iter(doc["fields"].values()))
        assert one["clay_pct"] == pytest.approx(29.33, abs=0.01) and one["elevation_m"] == 300.0 and one["slope_pct"] is not None
        assert one["note"] and one["zone_id"]
        saved = json.loads((data / "field_env.json").read_text(encoding="utf-8"))
        assert saved["fields"].keys() == doc["fields"].keys()
        soil_calls, elev_calls = calls["soil"], calls["elev"]
        assert soil_calls == 12 and 1 <= elev_calls <= 2                               # elevation is batched
        fe.run(data, cache_dir=tmp_path / "cache", http=http, fetched_at="2026-10-18T07:30Z", limit=12)
        assert calls["soil"] == soil_calls and calls["elev"] == elev_calls              # second run: all from cache
        schema = json.loads((ROOT / "contracts" / "field_env.schema.json").read_text())
        from jsonschema import Draft202012Validator
        assert not list(Draft202012Validator(schema).iter_errors(saved))

    def test_a_failing_field_does_not_cost_the_others(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir()
        (data / "fields.geojson").write_bytes((ROOT / "data" / "mock" / "fields.geojson").read_bytes())
        fields = json.loads((data / "fields.geojson").read_text())["features"][:3]
        lon, lat = fe.centroid(fields[1])
        http, _ = self.http_factory(soil_fail_for=(f"lon={lon:.4f}",))
        doc = fe.run(data, cache_dir=tmp_path / "cache", http=http, fetched_at="2026-10-18T07:00Z", limit=3)
        bad = doc["fields"][fields[1]["properties"]["field_id"]]
        assert bad["soc_g_kg"] is None and "soil" in bad["note"].lower() and bad["elevation_m"] == pytest.approx(300.0)
        good = doc["fields"][fields[0]["properties"]["field_id"]]
        assert good["soc_g_kg"] is not None

    def test_notes_compare_each_field_to_its_zone(self):
        fields = {"A": {"zone_id": "Z1", "soc_g_kg": 10.0, "slope_pct": 0.5, "texture": "loam"},
                  "B": {"zone_id": "Z1", "soc_g_kg": 20.0, "slope_pct": 0.5, "texture": "loam"},
                  "C": {"zone_id": "Z1", "soc_g_kg": 30.0, "slope_pct": 6.0, "texture": "silt loam"}}
        notes = fe.zone_notes(fields)
        assert "below" in notes["A"] and "organic carbon" in notes["A"]
        assert "above" in notes["C"] and "steeper" in notes["C"]
        assert notes["B"].startswith("Loam")

    def test_load_is_tolerant(self, tmp_path):
        assert fe.load_field_env(tmp_path) is None
        (tmp_path / "field_env.json").write_text("not json")
        assert fe.load_field_env(tmp_path) is None
        (tmp_path / "field_env.json").write_text(json.dumps({"fields": {"F1": {"soc_g_kg": 12.0}}}))
        assert fe.load_field_env(tmp_path)["fields"]["F1"]["soc_g_kg"] == 12.0
