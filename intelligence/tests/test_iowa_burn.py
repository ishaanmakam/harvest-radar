"""In corn-and-soy country (crop_system corn_soy) a dark field after harvest is wet residue or a tillage pass, not a
burn: real Story County corn read NBR about -0.2 for a week after the combine and then brightened, with no hotspot
anywhere. So there a scar counts as a burn only with a FIRMS hotspot, or when the field darkens again after harvest to
a severe NBR. The Punjab rules (rice_wheat, no flag) keep calling any scar a burn."""
from datetime import date, timedelta

import harvest_model as hm

D0 = date(2026, 7, 1)
DATES = [(D0 + timedelta(days=5 * i)).isoformat() for i in range(14)]      # Jul 1 .. Sep 4, every 5 days
GREEN_NDVI = [0.72, 0.84, 0.88, 0.89, 0.90, 0.89, 0.88, 0.87, 0.86, 0.85]
GREEN_NBR = [0.60, 0.70, 0.74, 0.75, 0.76, 0.75, 0.74, 0.73, 0.72, 0.71]
POST_NDVI = [0.25, 0.22, 0.20, 0.22]                                          # harvested: residue and soil
RESIDUE_NBR = [-0.18, -0.19, -0.11, -0.07]                                    # the real F0002 shape: dark, then brightening
BURN_NBR = [-0.18, -0.19, -0.45, -0.40]                                       # a second darkening to a severe scar
TH = {"peak_ndvi_min": 0.55, "harvest_ndvi_max": 0.25, "harvest_drop_min": 0.12, "burn_nbr_max": -0.05,
      "clear_fraction_min": 0.6, "default_peak_to_harvest_days": 58, "peak_plateau_drop": 0.04}
IOWA = {**TH, "burn_requires_hotspot": True}


def field(nbr_post, fires=None, th=IOWA):
    rec, _ = hm.analyze_field("F1", "Z01", DATES, GREEN_NDVI + POST_NDVI, GREEN_NBR + nbr_post, {"F1": fires or []}, th)
    return rec


class TestCornSoyBurnEvidence:
    def test_wet_residue_after_harvest_is_not_a_burn(self):
        rec = field(RESIDUE_NBR)
        assert rec["stage"] == "harvested" and rec["burn_evidence"] == "none" and rec["burn_date"] is None
        assert "residue" in rec["notes"].lower() and "hotspot" in rec["notes"].lower()

    def test_a_hotspot_confirms_the_scar(self):
        rec = field(RESIDUE_NBR, fires=[{"date": "2026-08-22"}])
        assert rec["stage"] == "burned" and rec["burn_evidence"] == "scar+hotspot"

    def test_a_second_darkening_to_a_severe_scar_is_a_burn_without_a_hotspot(self):
        rec = field(BURN_NBR)
        assert rec["stage"] == "burned" and rec["burn_evidence"] == "scar"
        assert rec["burn_date"] == DATES[12]                                   # the pass where NBR fell to -0.45

    def test_punjab_rules_still_call_any_scar_a_burn(self):
        rec = field(RESIDUE_NBR, th=TH)
        assert rec["stage"] == "burned" and rec["burn_evidence"] == "scar"


class TestSystemDefaults:
    def test_corn_soy_requires_a_hotspot_unless_region_says_otherwise(self):
        assert hm.SYSTEM_DEFAULTS["corn_soy"]["burn_requires_hotspot"] is True
        th = hm.thresholds_for({"crop_system": "corn_soy", "thresholds": TH})
        assert th["burn_requires_hotspot"] is True and th["burn_nbr_max"] == -0.05
        th = hm.thresholds_for({"crop_system": "corn_soy", "thresholds": {**TH, "burn_requires_hotspot": False}})
        assert th["burn_requires_hotspot"] is False
        assert "burn_requires_hotspot" not in hm.thresholds_for({"crop_system": "rice_wheat", "thresholds": TH})
