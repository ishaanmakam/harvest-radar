"""Pixel grid, STAC loading, reflectance scaling, and the cloud mask."""
import os
import sys
import time
from datetime import timezone

import numpy as np
import odc.stac
from affine import Affine
from odc.geo.geobox import GeoBox
from pystac import ItemCollection

from common import CACHE, load_region, log

# dark area, vegetation, bare soil, water, unclassified.
# Everything else (nodata, saturated, cloud shadow, clouds, cirrus, snow) is not clear.
VALID_SCL = (2, 4, 5, 6, 7)

_CONFIGURED = False


def region_geobox(region):
    """North-up frame grid aligned to the bbox. Vision crops use this pixel grid."""
    W, S, E, N = region["bbox"]
    res = region["resolution_deg"]
    width = round((E - W) / res)
    height = round((N - S) / res)
    assert (width, height) == (1000, 900), f"expected 1000x900, got {width}x{height}"
    return GeoBox(
        (height, width),
        Affine((E - W) / width, 0, W, 0, -(N - S) / height, N),
        "EPSG:4326",
    )


def native_geobox(region):
    """Sentinel-2 colour bands are 10 m in the tile's UTM zone. Load here, resample once."""
    W, S, E, N = region["bbox"]
    return GeoBox.from_bbox((W, S, E, N), "utm", resolution=10)


def load_items():
    path = CACHE / "stac_items.json"
    if not path.exists():
        log("geo", f"missing {path}. Run pipeline/s01_search.py first.")
        sys.exit(1)
    return list(ItemCollection.from_file(str(path)))


def item_date(item) -> str:
    dt = item.datetime
    if dt is None:
        log("geo", f"item {item.id} has no datetime")
        sys.exit(1)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.date().isoformat()


def _configure_rio():
    global _CONFIGURED
    if _CONFIGURED:
        return
    os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "5")
    os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "1")
    odc.stac.configure_rio(cloud_defaults=True, aws={"aws_unsigned": True})
    _CONFIGURED = True


def load_bands(items, bands, geobox=None, resampling="nearest"):
    _configure_rio()
    if geobox is None:
        geobox = native_geobox(load_region())
    band_list = [bands] if isinstance(bands, str) else list(bands)
    log("geo", f"loading {len(items)} items bands={band_list} crs={geobox.crs} {geobox.width}x{geobox.height}")
    started = time.perf_counter()
    ds = odc.stac.load(
        items,
        bands=band_list,
        geobox=geobox,
        groupby="solar_day",
        chunks={},
        resampling=resampling,
    ).compute()
    elapsed = time.perf_counter() - started
    log("geo", f"loaded {len(items)} items bands={band_list} in {elapsed:.1f}s")
    return ds


def dates_of(ds) -> list:
    out = []
    for value in ds.time.values:
        out.append(str(np.datetime_as_string(np.datetime64(value), unit="D")))
    return out


def _read_scale(item, band):
    try:
        spec = item.assets[band].extra_fields["raster:bands"][0]
        return float(spec["scale"]), float(spec["offset"])
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"{item.id} {band} missing raster:bands scale/offset") from exc


def scale_offset(items, band):
    if not items:
        raise ValueError(f"no items to read {band} scale/offset")
    first = _read_scale(items[0], band)
    last = _read_scale(items[-1], band)
    if first != last:
        raise ValueError(f"{band} scale/offset differs: first {first} last {last}")
    return first


def to_reflectance(dn, scale, offset):
    src = np.asarray(dn)
    out = src.astype(np.float32) * np.float32(scale) + np.float32(offset)
    out[src == 0] = np.nan
    return out


def resample_plane(arr, src_geobox, dst_geobox, resampling, nodata):
    """One resample from the native grid onto the frame grid."""
    from odc.geo.warp import rio_reproject

    src = np.asarray(arr)
    dst = np.empty((dst_geobox.height, dst_geobox.width), dtype=src.dtype)
    rio_reproject(
        src,
        dst,
        src_geobox,
        dst_geobox,
        resampling=resampling,
        src_nodata=nodata,
        dst_nodata=nodata,
    )
    return dst


def clear_fraction(scl_2d) -> float:
    arr = np.asarray(scl_2d)
    return float(np.isin(arr, VALID_SCL).sum()) / float(arr.size)
