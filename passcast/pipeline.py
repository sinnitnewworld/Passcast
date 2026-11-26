"""End-to-end processing for one satellite scene and a list of passes."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np

from .config import Pass, Thresholds
from .dem import DEMGrid, bbox_around
from .cloudmask import cloud_mask
from .downscale import downscale, terrain_under_pixels, DEMCloudField, crop_field
from .scoring import score_pass, PassResult
from .regions import thresholds_for, PROFILES


@dataclass
class PassProduct:
    result: PassResult
    field: DEMCloudField
    t_ref_sl: float
    tests: list


def process_pass(scene, p: Pass, dem: DEMGrid, th: Thresholds,
                 now: dt.datetime | None = None, cloud_base_m=None, regional=True) -> PassProduct:
    if regional:
        th = thresholds_for(p, th)
    bbox = bbox_around(p.lat, p.lon, th.window_half_width_km)
    sub = scene.subset(bbox, pad_deg=0.08)   # a few pixels beyond the window
    z_pix = terrain_under_pixels(sub, dem)
    cm = cloud_mask(sub, z_pix, th)
    field = downscale(sub, cm, dem, z_pix, th, cloud_base_m=cloud_base_m)
    field = crop_field(field, p.lat, p.lon, th.window_half_width_km)

    # geometry at the pass for confidence
    d = (sub.lat - p.lat) ** 2 + (sub.lon - p.lon) ** 2
    i = np.unravel_index(np.argmin(d), d.shape)
    now = now or dt.datetime.utcnow()
    meta = {
        "vza_deg": float(sub.vza[i]) if sub.vza is not None else None,
        "age_min": (now - sub.time).total_seconds() / 60.0,
        "night": bool(cm.night[i]),
        "has_ir039": sub.get("ir039") is not None,
        "ref_from_climatology": cm.ref_from_climatology,
        "pixel_km": sub.pixel_km,
    }
    res = score_pass(field, p, th, meta)
    res.caveats = field.notes + res.caveats
    res.caveats.insert(0, f"regional profile: {th.region_profile} ({PROFILES[th.region_profile]['note']})")
    return PassProduct(res, field, cm.t_ref_sl, cm.tests_used)


DEM_MARGIN_KM = 10.0   # DEM extends beyond the satellite window for footprint averaging


def run(scene, passes: list[Pass], dem_provider, th: Thresholds, now=None, cloud_base=None):
    """dem_provider(pass, half_width_km) -> DEMGrid covering that window."""
    out = []
    for p in passes:
        try:
            dem = dem_provider(p, th.window_half_width_km + DEM_MARGIN_KM)
            out.append(process_pass(scene, p, dem, th, now,
                                    cloud_base.get(p.name) if cloud_base else None))
            r = out[-1].result
            print(f"  {p.name:<20} score {r.score:<3}  {r.label:<16} conf {r.confidence_label}")
        except Exception as e:      # one bad pass must not stop the others
            print(f"  {p.name:<20} skipped: {type(e).__name__}: {e}")
    return out
