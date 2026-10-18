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
