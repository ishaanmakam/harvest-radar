"""Named regions for Mars sites: the smallest named region that contains a point wins, else the nearest edge."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from pipeline.mars import config, nearest_region  # noqa: E402

REGIONS = [
    {"name": "Arcadia Planitia", "lat": 47.2, "lon": -175.7, "radius_km": 950},
    {"name": "Vastitas Borealis", "lat": 87.7, "lon": 32.5, "radius_km": 5000},
    {"name": "Hellas Planitia", "lat": -42.4, "lon": 70.5, "radius_km": 1150},
    {"name": "Promethei Terra", "lat": -58.0, "lon": 97.0, "radius_km": 1650},
]


def test_smallest_containing_region_wins_over_a_huge_one():
    # M01 (52.3 N, 167.4 E) is inside Arcadia Planitia and also inside the catch-all northern plains.
    assert nearest_region(52.31, 167.44, REGIONS)["name"] == "Arcadia Planitia"


def test_far_north_point_falls_to_the_northern_plains():
    assert nearest_region(59.94, -82.31, REGIONS)["name"] == "Vastitas Borealis"


def test_point_outside_every_region_gets_the_nearest_edge():
    # 20 S, 60 E is outside all four; Hellas's edge is about 270 km away, the others are much further.
    r = nearest_region(-20.0, 60.0, REGIONS)
    assert r["name"] == "Hellas Planitia"
    assert r["inside"] is False


def test_result_carries_the_distance_to_the_centre():
    r = nearest_region(52.31, 167.44, REGIONS)
    assert 600 < r["km"] < 800


def test_config_gazetteer_names_arcadia_and_the_top_site_lands_in_it():
    regions = config()["regions"]
    names = {r["name"] for r in regions}
    assert "Arcadia Planitia" in names
    assert nearest_region(52.31, 167.44, regions)["name"] == "Arcadia Planitia"
