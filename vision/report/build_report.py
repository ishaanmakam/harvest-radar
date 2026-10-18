"""Build the Perceptron test report from the eval json. No API calls.

    python vision/report/build_report.py
"""
import html
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw
from shapely.geometry import shape

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eval.common import DEV_ZONES, ROOT, area_ha, frame_path, load_json  # noqa: E402
from eval.metrics import polygon_match  # noqa: E402
from marks import crop_bounds, draw_marks, group_fields, lonlat_to_px, upscale  # noqa: E402

EVAL = ROOT / "data" / "out" / "perceptron" / "eval"
OUT = ROOT / "data" / "out" / "perceptron" / "report"
IMG = OUT / "img"
TEMPLATE = Path(__file__).resolve().parent / "template.html"
DEV = ("Z01", "Z07")
CROP_COLOUR = {"corn": "#f4a259", "soybean": "#3bb273", "unknown": "#cccccc"}
RENAME = {"yes": "changed", "no": "unchanged", "unsure": "unsure"}


def style_block():
    text = TEMPLATE.read_text()
    return text[text.index("<style>"):text.index("</style>") + len("</style>")]


def fit(image, width=1000):
    image = image.convert("RGB")
    if image.width > width:
        height = max(1, round(image.height * width / image.width))
        image = image.resize((width, height), Image.LANCZOS)
    return image


def save(image, name, quality=72):
    path = IMG / name
    fit(image).save(path, quality=quality)
    return f"img/{name}"


def esc(value):
    return html.escape("" if value is None else str(value))


def draw_refs(image, window, features, colour_of):
    draw = ImageDraw.Draw(image)
    for feat in features:
        geom = shape(feat["geometry"])
        polys = [geom] if geom.geom_type == "Polygon" else list(getattr(geom, "geoms", []))
        colour = colour_of(feat)
        for poly in polys:
            if poly.geom_type != "Polygon":
                continue
            pts = [lonlat_to_px(x, y, window, image.width, image.height) for x, y in poly.exterior.coords]
            if len(pts) >= 2:
                draw.line(pts, fill=colour, width=2)
    return image


def colour_for(fid, pred, truth):
    if truth == "unsure" or pred in (None, "unsure"):
        return "#9aa59e"
    return "#2f8a4e" if pred == truth else "#c23b33"


def metric_cell(stats, kind):
    if not stats:
        return "—"
    if kind == "accuracy":
        return stats.get("accuracy_text") or "—"
    if kind == "change":
        return f"precision {stats.get('precision_text', '—')}; recall {stats.get('recall_text', '—')}"
    if kind == "outline":
        return (f"recall {stats.get('recall_text', '—')}; precision {stats.get('precision_text', '—')}; "
                f"duplicates {stats.get('duplicates_gt', '—')}")
    return "—"


def pill(passed):
    if passed is True:
        return '<span class="pill good">PASS</span>'
    if passed is False:
        return '<span class="pill bad">FAIL</span>'
    return '<span class="pill info">Comparison</span>'


def main():
    IMG.mkdir(parents=True, exist_ok=True)
    board = load_json(EVAL / "scoreboard.json")
    chosen = load_json(EVAL / "chosen.json")
    frames = load_json(ROOT / "data" / "out" / "frames" / "frames.json")
    fields = load_json(ROOT / "data" / "out" / "fields.geojson")["features"]
    zones = load_json(ROOT / "data" / "out" / "zones.geojson")["features"]
    truth = load_json(ROOT / "vision" / "eval" / "truth.json")["fields"]
    insights = load_json(ROOT / "data" / "out" / "insights.json")
    spend = load_json(ROOT / "data" / "out" / "perceptron" / "spend.json")
    start = load_json(EVAL / "spend_start.json")["usd"]
    latest = frame_path(frames, "2026-10-17", ROOT / "data" / "out")
    prev = frame_path(frames, "2026-10-10", ROOT / "data" / "out")
    base = Image.open(latest).convert("RGB")
    bounds = frames["bounds"]
    by_zone = {}
    zone_of = {}
    for feat in fields:
        zid = feat["properties"]["zone_id"]
        by_zone.setdefault(zid, []).append(feat)
        zone_of[feat["properties"]["field_id"]] = zid
    outline_name = chosen["outline"]
    outlines = load_json(EVAL / f"outlines_{outline_name}.geojson")["features"]
    state = load_json(EVAL / f"classify_state_{chosen['state']}.json")
    change = load_json(EVAL / f"classify_change_{chosen['change']}.json")

    seg = draw_refs(base.copy(), bounds, fields, lambda f: CROP_COLOUR.get(f["properties"].get("crop"), "#cccccc"))
    per = draw_refs(base.copy(), bounds, outlines, lambda f: "#3dc2ff")
    seg_src, per_src = save(seg, "seg_full.jpg"), save(per, "perceptron_full.jpg")
    seeds = base.copy()
    draw = ImageDraw.Draw(seeds)
    for seed in load_json(EVAL / "seeds.json")["seeds"]:
        x, y = lonlat_to_px(seed["lon"], seed["lat"], bounds, seeds.width, seeds.height)
        draw.ellipse([x - 3, y - 3, x + 3, y + 3], fill="#ff2d2d")
    seed_src = save(seeds, "seeds.jpg")
    z07 = next(z for z in zones if z["properties"]["zone_id"] == "Z07")
    xs = [p[0] for p in z07["geometry"]["coordinates"][0]]
    ys = [p[1] for p in z07["geometry"]["coordinates"][0]]
    zbox = [min(xs), min(ys), max(xs), max(ys)]
    zcrop, zwindow = crop_bounds(latest, bounds, zbox)
    zcrop, _scale = upscale(zcrop, 768)
    z_fields = [f for f in fields if f["properties"]["zone_id"] == "Z07"]
    z_out = [f for f in outlines if f["properties"].get("zone_id") == "Z07"]
    z_seg = save(draw_refs(zcrop.copy(), zwindow, z_fields, lambda f: CROP_COLOUR.get(f["properties"].get("crop"), "#cccccc")), "z07_seg.jpg")
    z_per = save(draw_refs(zcrop.copy(), zwindow, z_out, lambda f: "#3dc2ff"), "z07_perceptron.jpg")

    examples = []
    kept_images = [f["properties"].get("image") for f in outlines if f["properties"].get("image")]
    for rel in kept_images[:4]:
        path = ROOT / rel
        if path.exists():
            examples.append((save(Image.open(path), f"trace_ok_{len(examples)}.jpg"), "Kept: the polygon contains the dot"))
    fail_dir = EVAL / "traces"
    for path in sorted(fail_dir.glob("A_Z07_*.jpg")):
        if path.name.endswith("_retry.jpg"):
            continue
        rel = str(path.relative_to(ROOT))
        if rel in kept_images:
            continue
        examples.append((save(Image.open(path), f"trace_bad_{len(examples)}.jpg"), "Not kept: the outline missed the dot, was too small, or the call was cut off"))
        if sum(1 for _src, cap in examples if cap.startswith("Not kept")) >= 2:
            break

    def marked_groups(doc, task, dates):
        limit = doc["max_per_group"]
        preds = doc["predictions"]
        cards = []
        for zid in DEV:
            groups = group_fields(by_zone.get(zid, []), limit)
            for index, (bbox, feats) in enumerate(groups):
                colours = {}
                for feat in feats:
                    fid = feat["properties"]["field_id"]
                    if task == "state":
                        colours[fid] = colour_for(fid, preds.get(fid), truth.get(fid, {}).get("state_oct17"))
                    else:
                        colours[fid] = colour_for(fid, preds.get(fid), RENAME.get((truth.get(fid) or {}).get("changed")))
                paths = []
                for date, frame in dates:
                    crop, window = crop_bounds(frame, bounds, bbox)
                    crop, _s = upscale(crop, 768)
                    marked, _ids = draw_marks(crop, window, feats, colours=colours)
                    paths.append(save(marked, f"{task}_{zid}_{index}_{date}.jpg", quality=70))
                cards.append((zid, index, paths))
        return cards

    state_cards = marked_groups(state, "state", [("2026-10-17", latest)])
    change_cards = marked_groups(change, "change", [("2026-10-10", prev), ("2026-10-17", latest)])

    wrong = []
    for fid, row in truth.items():
        actual = RENAME[row["changed"]]
        if actual == "unsure":
            continue
        guess = change["predictions"].get(fid)
        if guess != actual:
            wrong.append((fid, actual, guess or "missing"))
    wrong_cards = []
    for fid, actual, guess in wrong:
        feat = next(f for f in fields if f["properties"]["field_id"] == fid)
        minx, miny, maxx, maxy = shape(feat["geometry"]).bounds
        pad_x, pad_y = (maxx - minx) * 0.4 or 1e-5, (maxy - miny) * 0.4 or 1e-5
        bbox = [max(bounds[0], minx - pad_x), max(bounds[1], miny - pad_y), min(bounds[2], maxx + pad_x), min(bounds[3], maxy + pad_y)]
        thumbs = []
        for date, frame in (("1010", prev), ("1017", latest)):
            crop, window = crop_bounds(frame, bounds, bbox)
            scale = 180 / max(crop.height, 1)
            thumb = crop.resize((max(1, round(crop.width * scale)), 180), Image.LANCZOS)
            draw = ImageDraw.Draw(thumb)
            geom = shape(feat["geometry"])
            pts = []
            for lon, lat in geom.exterior.coords:
                x = (lon - window[0]) / (window[2] - window[0]) * crop.width * (thumb.width / crop.width)
                y = (window[3] - lat) / (window[3] - window[1]) * crop.height * (thumb.height / crop.height)
                pts.append((x, y))
            if len(pts) >= 2:
                draw.line(pts, fill="#ffe14d", width=2)
            thumbs.append(save(thumb, f"wrong_{fid}_{date}.jpg", quality=68))
        wrong_cards.append((fid, actual, guess, thumbs))

    zone_figs = []
    for item in insights["items"]:
        zid = item["zone_id"]
        thumbs = []
        for date, label in (("2026-08-28", "Aug 28"), ("2026-10-02", "Oct 2"), ("2026-10-17", "Oct 17")):
            src = ROOT / "data" / "out" / "perceptron" / "crops" / f"{zid}_{date}.jpg"
            if src.exists():
                thumbs.append((save(Image.open(src), f"zone_{zid}_{date}.jpg", quality=70), label))
        zone_figs.append((item, thumbs))

    rows = []
    old = {
        "outlines_full": "71 grid shapes, cut off at the token limit",
        "outlines_tile_z07": "206 duplicates stacked on the left edge",
        "standing_vs_harvested": "196 standing, 0 harvested",
        "change": "5 clear, 2 partial, 3 wrong boxes",
        "zone_summaries": "9 of 9 grounded; no false flood or damage",
        "ndvi_state": "Oct 17 NDVI ≤ 0.25 counted as harvested",
        "ndvi_change": "NDVI drop ≥ 0.15 counted as changed",
    }
    for row in board:
        kind = "outline" if row["test"].startswith("outline") else ("accuracy" if "standing" in row["test"] or row["test"] == "ndvi_state" else "change")
        if row["test"] == "zone_summaries":
            dev, held = "2 zones", "7 zones"
        else:
            dev = metric_cell(row.get("metrics_dev"), kind)
            held = metric_cell(row.get("metrics_heldout"), kind)
        rows.append(f"<tr><td>{esc(row['test'])}</td><td>{esc(old.get(row['test'], ''))}</td><td>{esc(row['method'])} {esc(row['variant'])}</td>"
                    f"<td>{esc(dev)}</td><td>{esc(held)}</td><td>{pill(row['pass'])}<div class=\"note\">{esc(row['bar'])}</div></td></tr>")

    def cards_html(cards, pair):
        bits = []
        for zid, index, paths in cards:
            figs = "".join(f'<figure><img src="{src}" alt="{zid} group {index}"></figure>' for src in paths)
            wrap = "two" if pair else "four"
            bits.append(f'<div class="box"><div class="top"><b>{zid} group {index + 1}</b></div><div class="{wrap}">{figs}</div></div>')
        return "".join(bits)

    wrong_html = []
    for fid, actual, guess, thumbs in wrong_cards:
        wrong_html.append(
            f'<div class="box"><div class="top"><b>{esc(fid)}</b><span class="pill bad">{esc(guess)} vs {esc(actual)}</span></div>'
            f'<div class="two"><figure><img src="{thumbs[0]}" alt="{fid} Oct 10"><figcaption>Oct 10</figcaption></figure>'
            f'<figure><img src="{thumbs[1]}" alt="{fid} Oct 17"><figcaption>Oct 17</figcaption></figure></div></div>'
        )

    zone_html = []
    for item, thumbs in zone_figs:
        gate = item.get("gate") or {}
        counts = item.get("counts") or {}
        strip = "".join(f'<figure><img src="{src}" alt="{item["zone_id"]} {label}"><figcaption>{label}</figcaption></figure>' for src, label in thumbs)
        chips = "".join(
            f'<span class="chip{" on" if gate.get(name) else ""}">{name} {"yes" if gate.get(name) else "no"}</span>'
            for name in ("flood", "stress", "change")
        )
        zone_html.append(
            f'<article class="zone"><div class="strip">{strip}</div><div class="txt"><div class="top"><h3>{esc(item["zone_id"])}</h3>'
            f'<span class="pill info">counts_source {esc(item.get("counts_source") or "model_estimate")}</span></div>'
            f'<p class="said">“{esc(item.get("summary"))}”</p><div class="chips">{chips}</div>'
            f'<p class="note">standing {counts.get("standing", "—")} · harvested {counts.get("harvested", "—")} · burned {counts.get("burned", 0)}</p></div></article>'
        )

    count_rows = []
    for zid in sorted(by_zone):
        ids = [f["properties"]["field_id"] for f in by_zone[zid]]
        def tally(mapping, key):
            return sum(1 for fid in ids if mapping.get(fid) == key)
        count_rows.append(
            f"<tr><td class=\"n\">{zid}</td><td class=\"n\">{tally(state['predictions'], 'standing')}</td>"
            f"<td class=\"n\">{tally(truth, 'state') if False else sum(1 for fid in ids if truth[fid]['state_oct17']=='standing')}</td>"
            f"<td class=\"n\">{tally(state['predictions'], 'harvested')}</td>"
            f"<td class=\"n\">{sum(1 for fid in ids if truth[fid]['state_oct17']=='harvested')}</td>"
            f"<td class=\"n\">{sum(1 for fid in ids if truth[fid]['state_oct17']=='unsure')}</td></tr>"
        )

    def counted(score, label, kind):
        conf = score.get("confusion") or {}
        tp = conf.get(label, {}).get(label, 0)
        if kind == "recall":
            support = sum(conf.get(label, {}).values())
            return f"{score['per_label'][label]['recall']:.2f} ({tp} of {support})"
        predicted = sum(row.get(label, 0) for row in conf.values())
        return f"{score['per_label'][label]['precision']:.2f} ({tp} of {predicted})"

    variant_rows = []
    for task in ("state", "change"):
        for name in "ABC":
            path = EVAL / f"classify_{task}_{name}.json"
            if not path.exists():
                continue
            doc = load_json(path)
            score = (doc.get("tuning") or {}).get("score") or doc.get("score") or {}
            if task == "state":
                conf = score.get("confusion") or {}
                correct = sum(conf.get(label, {}).get(label, 0) for label in ("standing", "harvested"))
                text = f"accuracy {score.get('accuracy', 0):.2f} ({correct} of {score.get('n', 0)})"
            else:
                text = f"precision {counted(score, 'changed', 'precision')}; recall {counted(score, 'changed', 'recall')}"
            mark = " ← chosen" if chosen[task] == name else ""
            variant_rows.append(f"<tr><td>{task} {name}{mark}</td><td class=\"n\">{esc(text)}</td><td>{'8 per group' if name=='C' else '12 per group'}{'; look-inside sentence' if name=='B' else ''}</td></tr>")
    dev_refs = [f for f in fields if f["properties"]["zone_id"] in DEV_ZONES]
    for name in "ABC":
        path = EVAL / f"outlines_{name}.geojson"
        if not path.exists():
            continue
        feats = [f for f in load_json(path)["features"] if f["properties"].get("zone_id") in DEV_ZONES]
        preds = [shape(f["geometry"]) for f in feats if area_ha(shape(f["geometry"])) >= 8]
        refs = [shape(f["geometry"]) for f in dev_refs if area_ha(shape(f["geometry"])) >= 8]
        matched = polygon_match(preds, refs, iou_min=0.5, min_area_ha=0)
        hits = len(matched["matched_pairs"])
        text = (f"recall {matched['recall']:.2f} ({hits} of {matched['n_ref']}); "
                f"precision {matched['precision']:.2f} ({hits} of {matched['n_pred']})")
        mark = " ← chosen" if chosen["outline"] == name else ""
        variant_rows.append(f"<tr><td>outline {name}{mark}</td><td class=\"n\">{esc(text)}</td><td>{'160 px window' if name=='B' else '200 px window'}{'; colour sentence' if name=='C' else ''}</td></tr>")

    phases = {"State labels": 0.0, "Change labels": 0.0, "Seeds": 0.0, "Traces": 0.0}
    counts = {key: 0 for key in phases}
    for call in spend["calls"]:
        label = call.get("label") or ""
        usd = call.get("usd") or 0
        if label.startswith("state_"):
            phases["State labels"] += usd
            counts["State labels"] += 1
        elif label.startswith("change_"):
            phases["Change labels"] += usd
            counts["Change labels"] += 1
        elif label.startswith("seed_"):
            phases["Seeds"] += usd
            counts["Seeds"] += 1
        elif label[:2] in ("A_", "B_", "C_"):
            phases["Traces"] += usd
            counts["Traces"] += 1
    phase_rows = "".join(
        f"<tr><td>{name}</td><td class=\"n\">{counts[name]}</td><td class=\"n\">${phases[name]:.4f}</td></tr>" for name in phases
    )
    this_run = spend["usd"] - start
    state_row = next(r for r in board if r["test"] == "standing_vs_harvested")
    change_row = next(r for r in board if r["test"] == "change")
    confusion = state_row["metrics_all"]["confusion"]
    conf_rows = "".join(
        f"<tr><td>{label}</td>" + "".join(f"<td class=\"n\">{confusion[label].get(col, 0)}</td>" for col in ("standing", "harvested", "unsure")) + "</tr>"
        for label in ("standing", "harvested")
    )
    trace_html = "".join(f'<figure><img src="{src}" alt="Trace example"><figcaption>{esc(cap)}</figcaption></figure>' for src, cap in examples)
    reason = ""
    if state_row["pass"] is False:
        reason += "<p>Standing vs harvested fails the 0.90 bar. On the dev zones the model called nearly every dark field harvested. The fixed prompt treats freshly tilled dark soil as harvested, and October standing corn and soybeans are also dark, so the two classes overlap.</p>"
    if change_row["pass"] is False:
        reason += "<p>Change recall can look high while precision stays well under 0.85: the model marks many unchanged fields as changed. " + esc(change_row.get("note") or "") + "</p>"
    outline_row = next(r for r in board if r["test"] == "outlines_full")
    if outline_row["pass"] is False:
        held = outline_row.get("metrics_heldout") or {}
        reason += (f"<p>Outlines miss the bar. Held-out recall {esc(held.get('recall_text', '—'))}, "
                   f"precision {esc(held.get('precision_text', '—'))}, "
                   f"duplicates {esc(held.get('duplicates_gt', '—'))}. "
                   f"Every trace call has to finish with stop; this run did not.</p>")

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">{style_block()}</head>
<body><div class="wrap">
<header>
  <span class="eyebrow">Overfield · Vision workstream · Test report</span>
  <h1>Perceptron field test, second method</h1>
  <p class="lede">Set-of-marks labels and seed-then-trace outlines, scored against the human answer key. Pass bars were fixed before these numbers existed.</p>
  <div class="meta"><span>Model {esc(insights.get('model'))}</span><span>Frames 2026-10-10 and 2026-10-17</span><span>This run ${this_run:.4f}</span><span>Overall ${spend['usd']:.4f}</span><span>Fields not in the answer key: 0</span></div>
</header>
<section>
  <div class="sec-head"><span class="num">Scoreboard</span><h2>Dev zones beside held-out zones</h2>
  <p>Dev zones are Z01 and Z07. Held-out zones are Z02, Z03, Z04, Z05, Z06, Z08 and Z09. A number in parentheses is the count behind the rate.</p></div>
  <div class="tablewrap"><table>
    <thead><tr><th>Test</th><th>Previous result</th><th>New method</th><th>Dev</th><th>Held-out</th><th>Bar</th></tr></thead>
    <tbody>{''.join(rows)}</tbody>
  </table></div>
  {reason}
</section>
<section>
  <div class="sec-head"><span class="num">Outlines</span><h2>Seed, then trace one field</h2>
  <p>Variant {esc(outline_name)} won on dev-zone recall. Cyan is a traced polygon. Orange is corn and green is soybean in the segmentation reference.</p></div>
  <div class="two">
    <figure><img src="{per_src}" alt="Perceptron outlines on the Oct 17 frame"><figcaption>Perceptron outlines, variant {esc(outline_name)}, {len(outlines)} polygons after merging duplicates.</figcaption></figure>
    <figure><img src="{seg_src}" alt="Segmentation outlines"><figcaption>Segmentation reference, {len(fields)} fields.</figcaption></figure>
  </div>
  <div class="two">
    <figure><img src="{z_per}" alt="Zone Z07 Perceptron outlines"><figcaption>Zone Z07, traced polygons.</figcaption></figure>
    <figure><img src="{z_seg}" alt="Zone Z07 segmentation"><figcaption>Zone Z07, segmentation.</figcaption></figure>
  </div>
  <figure><img src="{seed_src}" alt="Seed points"><figcaption>Seed points kept after the 6×6 grid. Tiles with an edge scan or more than 10 points were dropped; none were.</figcaption></figure>
  <div class="four">{trace_html}</div>
</section>
<section>
  <div class="sec-head"><span class="num">Standing vs harvested</span><h2>Numbered outlines on Oct 17</h2>
  <p>Green matches the answer key, red disagrees, grey is unsure on either side. Truth-unsure fields are not in the accuracy.</p>
  <div class="chips"><span class="pill good">green matches</span><span class="pill bad">red disagrees</span><span class="pill info">grey unsure</span></div></div>
  <div class="boxes">{cards_html(state_cards, False)}</div>
  <div class="tablewrap"><table>
    <thead><tr><th>Truth \\ predicted</th><th>standing</th><th>harvested</th><th>unsure</th></tr></thead>
    <tbody>{conf_rows}</tbody>
  </table></div>
  <p class="note">NDVI rule (Oct 17 ≤ 0.25 = harvested), held-out: {esc(metric_cell(next(r for r in board if r['test']=='ndvi_state')['metrics_heldout'], 'accuracy'))}. Comparison only.</p>
  <div class="tablewrap"><table>
    <thead><tr><th>Zone</th><th>Predicted standing</th><th>Truth standing</th><th>Predicted harvested</th><th>Truth harvested</th><th>Truth unsure</th></tr></thead>
    <tbody>{''.join(count_rows)}</tbody>
  </table></div>
</section>
<section>
  <div class="sec-head"><span class="num">Change</span><h2>Oct 10 beside Oct 17</h2>
  <p>{esc(change_row.get('note') or '')}</p>
  <div class="chips"><span class="pill good">green matches</span><span class="pill bad">red disagrees</span><span class="pill info">grey unsure</span></div></div>
  <p class="note">Held-out {esc(metric_cell(change_row.get('metrics_heldout'), 'change'))}. Dev {esc(metric_cell(change_row.get('metrics_dev'), 'change'))}.</p>
  <div class="boxes">{cards_html(change_cards, True)}</div>
  <h3>Wrong fields ({len(wrong_cards)})</h3>
  <div class="boxes">{''.join(wrong_html)}</div>
  <p class="note">NDVI rule (drop ≥ 0.15 = changed), held-out: {esc(metric_cell(next(r for r in board if r['test']=='ndvi_change')['metrics_heldout'], 'change'))}. Comparison only.</p>
</section>
<section>
  <div class="sec-head"><span class="num">Zone summaries</span><h2>The narration that already passed</h2>
  <p>No new flood or stress claim. Counts show the source written into insights.json.</p></div>
  <div class="zones">{''.join(zone_html)}</div>
</section>
<section>
  <div class="sec-head"><span class="num">Variants</span><h2>What was tuned, and only on Z01 and Z07</h2></div>
  <div class="tablewrap"><table>
    <thead><tr><th>Variant</th><th>Dev score</th><th>Difference</th></tr></thead>
    <tbody>{''.join(variant_rows)}</tbody>
  </table></div>
  <p class="note">State C won on accuracy. Change A won on changed-recall, then precision. Outline A won on recall. Held-out zones were not used to choose.</p>
</section>
<section>
  <div class="sec-head"><span class="num">Cost</span><h2>This run</h2></div>
  <div class="tablewrap"><table>
    <thead><tr><th>Phase</th><th>Calls</th><th>Spend</th></tr></thead>
    <tbody>{phase_rows}<tr><td><b>This run</b></td><td class="n"></td><td class="n"><b>${this_run:.4f}</b></td></tr>
    <tr><td>Overall, including earlier tests</td><td class="n">{len(spend['calls'])}</td><td class="n">${spend['usd']:.4f}</td></tr></tbody>
  </table></div>
</section>
<section>
  <div class="sec-head"><span class="num">Method</span><h2>Fixed before the scores</h2></div>
  <ul>
    <li>Pass bars: outline recall ≥ 0.75 and precision ≥ 0.80 on fields of at least 8 ha at IoU ≥ 0.5, no pred-pred pair above IoU 0.5, every trace call finished with stop. State accuracy ≥ 0.90 with predicted unsure on at most 10% of scored fields. Change precision ≥ 0.85 and recall ≥ 0.80.</li>
    <li>Dev zones Z01 and Z07. At most three prompt variants. The winner is the one with the better dev score. Held-out zones were scored after that choice.</li>
    <li>The answer key is human labels on the Oct 10 and Oct 17 imagery, in vision/eval/truth.json. NDVI is only a comparison: standing and harvested overlap between 0.19 and 0.35.</li>
    <li>Fields in the geojson that are not in the answer key would be classified and then left out of the score. This run had none. Labelled geometries were checked against commit ccf4be1 and were unchanged.</li>
    <li>Product wiring uses the held-out bar. A failing test does not replace counts, cues, or the segmentation fields.</li>
  </ul>
</section>
</div></body></html>
"""
    dest = OUT / "index.html"
    dest.write_text(page)
    total = sum(p.stat().st_size for p in IMG.glob("*.jpg"))
    print(f"wrote {dest.relative_to(ROOT)} images={len(list(IMG.glob('*.jpg')))} bytes={total}")


if __name__ == "__main__":
    main()
