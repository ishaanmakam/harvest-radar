"""Harvest rule on crops that senesce standing in the field (corn, soybeans): the collapse must persist at the
harvested level, and the post-harvest regrow ('sown') check is optional for regions without a following crop.
Thresholds here are the Iowa ones (harvest_ndvi_max 0.25)."""
import harvest_model as hm

TH_IOWA = {"peak_ndvi_min": 0.55, "harvest_ndvi_max": 0.25, "harvest_drop_min": 0.12, "burn_nbr_max": -0.05,
           "default_peak_to_harvest_days": 58, "peak_plateau_drop": 0.04}
TH_PUNJAB = dict(TH_IOWA, harvest_ndvi_max=0.35, sown_ndvi_min=0.3, default_peak_to_harvest_days=40)

DATES = [f"2026-{m:02d}-{d:02d}" for m, d in [(7, 1), (7, 11), (7, 21), (7, 31), (8, 10), (8, 20), (8, 30), (9, 9), (9, 19),
                                               (9, 24), (9, 29), (10, 4), (10, 9), (10, 14), (10, 19), (10, 24)]]
#            green-up / peak / plateau ........ senescence ........ dip   standing  standing  HARVEST residue
CORN = [0.45, 0.70, 0.85, 0.87, 0.86, 0.80, 0.62, 0.45, 0.35, 0.30, 0.24, 0.31, 0.33, 0.18, 0.17, 0.16]
NBR = [0.3] * len(CORN)


class TestCollapseMustPersist:
    def test_noise_dip_on_dry_standing_corn_is_not_a_harvest(self):
        rec, _ = hm.analyze_field("F1", "Z01", DATES, CORN, NBR, {}, TH_IOWA)
        assert rec["stage"] == "harvested"
        assert "2026-10-09" < rec["harvest_date"] <= "2026-10-14"        # between the last standing pass and the residue pass

    def test_old_relative_confirmation_would_have_called_the_dip(self):
        # documents the failure mode: with the 0.35 threshold the dip at 0.24 looks like a harvest
        rec, _ = hm.analyze_field("F1", "Z01", DATES, CORN, NBR, {}, TH_PUNJAB)
        assert rec["harvest_date"] <= "2026-10-14"

    def test_harvest_on_the_latest_pass_is_still_called_with_lower_confidence(self):
        ndvi = CORN[:13] + [0.18]
        rec, _ = hm.analyze_field("F1", "Z01", DATES[:14], ndvi, NBR[:14], {}, TH_IOWA)
        assert rec["stage"] == "harvested" and rec["confidence"] == 0.7


class TestMockWorldFollowsCropSystem:
    def test_regenerated_mock_is_corn_and_soybeans_when_region_says_so(self):
        import json
        import subprocess
        import sys
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        region = json.loads((root / "config" / "region.json").read_text())
        subprocess.run([sys.executable, str(root / "scripts" / "make_mock_data.py")], check=True, cwd=root)
        fields = json.loads((root / "data" / "mock" / "fields.geojson").read_text())["features"]
        crops = {f["properties"]["crop"] for f in fields}
        truth = json.loads((root / "data" / "mock" / "truth.json").read_text())
        weather = json.loads((root / "data" / "mock" / "weather.json").read_text())
        if region.get("crop_system") == "corn_soy":
            assert crops == {"corn", "soybean", "other"}
            assert truth["crop_system"] == "corn_soy"
            assert 100 <= len(fields) <= 600                                  # 50-100 acre fields on an 8 x 10 km box
            assert "tmin_c" in weather["forecast"] and min(weather["forecast"]["tmin_c"][:3]) <= 0   # a frost night in the lookahead
            assert sum(1 for t in truth["fields"].values() if t["burned"]) < 0.05 * len(fields)
        else:
            assert crops == {"rice", "other"}


class TestRegrowCheckIsOptional:
    def test_without_sown_threshold_regreening_weeks_later_never_reads_as_sown(self):
        dates = DATES + ["2026-10-29", "2026-11-03"]
        ndvi = CORN + [0.32, 0.36]                                       # residue for three passes, then volunteer / cover crop
        rec, _ = hm.analyze_field("F1", "Z01", dates, ndvi, NBR + [0.3, 0.3], {}, TH_IOWA)
        assert rec["stage"] == "harvested"

    def test_with_sown_threshold_the_regrow_check_still_works(self):
        dates = DATES + ["2026-10-29", "2026-11-03"]
        ndvi = CORN[:14] + [0.17, 0.18, 0.32, 0.36]
        rec, _ = hm.analyze_field("F1", "Z01", dates, ndvi, NBR + [0.3, 0.3], {}, TH_PUNJAB)
        assert rec["stage"] == "sown"
