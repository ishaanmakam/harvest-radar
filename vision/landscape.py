"""Prompt text for the configured landscape. Reads config/region.json crop_system."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

IOWA_STRESS = ("'stress' = a patch clearly worse than the rest of the SAME field "
               "(irregular bare, flattened, drowned or discoloured spots). "
               "The uniform seasonal dry-down of a whole field is NOT stress.")
IOWA_FLOOD = ("'flood' = standing water on a field (dark, smooth, often in low spots). "
              "Rivers, ponds, tree shadows and cloud shadows are NOT flood.")
PUNJAB_STRESS = "'stress' = parts of green fields that are browning or patchy but not cleanly harvested"
PUNJAB_FLOOD = "'flood' = standing water on fields (dark blue/black, smooth, often along low ground)"


def landscape():
    region = json.loads((ROOT / "config" / "region.json").read_text())
    crop = region.get("crop_system") or ""
    name = region.get("name") or "farmland"
    extra = (region.get("assumptions") or {}).get("landscape")
    if crop == "corn_soy":
        scene = (
            "Sentinel-2 true-colour image(s), 10 m pixels, north up, of corn and soybean farmland in "
            f"{name}, USA, in autumn. Fields are large rectangles on a 1-mile road grid, with a town, "
            "farmsteads, and tree-lined streams. In October mature crops dry down from green to yellow, "
            "tan or dark brown before harvest; harvested fields show light tan stubble or dark bare/tilled soil. "
            "Field burning is rare. Ignore clouds and cloud shadows."
        )
        if extra:
            scene = f"{scene} {extra}"
        key, stress, flood, labels = "iowa", IOWA_STRESS, IOWA_FLOOD, ["standing", "harvested"]
    elif crop == "rice_wheat":
        scene = (
            "Sentinel-2 satellite images (10 m pixels, north up) of the same ~3 km patch of rice farmland "
            "in Punjab, India. Green = standing rice, tan/light brown = harvested, very dark/black = burned stubble."
        )
        key, stress, flood, labels = "punjab", PUNJAB_STRESS, PUNJAB_FLOOD, ["standing", "harvested", "burned"]
    else:
        scene = "Sentinel-2 true-colour image(s), 10 m pixels, north up, of farmland. Ignore clouds and cloud shadows."
        key, stress, flood, labels = "generic", IOWA_STRESS, IOWA_FLOOD, ["standing", "harvested"]
    return {
        "name": name,
        "key": key,
        "scene": scene,
        "stress_def": stress,
        "flood_def": flood,
        "state_labels": labels,
        "crop_system": crop or None,
    }
