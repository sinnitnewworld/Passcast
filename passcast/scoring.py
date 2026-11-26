"""Pass scores (1 / 0.5 / 0), confidence, and cloud amount for any region."""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field, asdict

import numpy as np
from matplotlib.path import Path as MplPath

from .downscale import DEMCloudField, CLEAR, ABOVE, IN_CLOUD, BELOW, CLASS_NAMES
from .config import Pass, Thresholds

LABELS = {1.0: "CLEAR", 0.5: "PARTIALLY CLEAR", 0.0: "NOT CLEAR"}


@dataclass
class PassResult:
    name: str
    region: str
    lat: float
    lon: float
    elevation_m: float
    score: float
    label: str
    obscured_fraction: float
    cloud_top_m: float | None
    cloud_base_m: float | None
    ceiling_above_pass_m: float | None
    class_breakdown: dict
    confidence: float
    confidence_label: str
    reasons: list = field(default_factory=list)
    caveats: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def _metres_grid(field: DEMCloudField, lat0, lon0):
    LON, LAT = field.dem.mesh()
    dy = (LAT - lat0) * 111000.0
    dx = (LON - lon0) * 111000.0 * math.cos(math.radians(lat0))
    return dx, dy


def corridor_mask(field: DEMCloudField, p: Pass):
    dx, dy = _metres_grid(field, p.lat, p.lon)
    m = np.hypot(dx, dy) <= p.corridor_radius_m
    for (la, lo) in p.approach or []:
        ddx = (field.dem.mesh()[0] - lo) * 111000.0 * math.cos(math.radians(la))
        ddy = (field.dem.mesh()[1] - la) * 111000.0
        m |= np.hypot(ddx, ddy) <= p.approach_buffer_m
    return m


def obscuration(field: DEMCloudField, th: Thresholds):
    """Per-cell 0-1 'cannot fly here' value."""
    z = field.dem.z
    ceiling = field.base_m - z
    low_ceiling = (field.cls == BELOW) & (ceiling < th.min_ceiling_above_terrain_m)
    obs = np.zeros(z.shape, dtype="float32")
    obs = np.where(field.cls == IN_CLOUD, field.prob_cell, obs)
    obs = np.where(low_ceiling, field.prob_column, obs)
    return obs


def score_pass(field: DEMCloudField, p: Pass, th: Thresholds, scene_meta: dict) -> PassResult:
    m = corridor_mask(field, p)
    obs = obscuration(field, th)
    frac = float(obs[m].mean()) if m.any() else float("nan")

    if frac <= th.score_clear_max_obscured:
        score = 1.0
    elif frac <= th.score_partial_max_obscured:
        score = 0.5
    else:
        score = 0.0

    cls = field.cls[m]
    breakdown = {CLASS_NAMES[k]: round(float((cls == k).mean()), 3) for k in (CLEAR, ABOVE, IN_CLOUD, BELOW)}
    cth = field.cth_m[m]
    base = field.base_m[m]
    cth_v = float(np.nanmedian(cth)) if np.isfinite(cth).any() else None
    assumed = bool(field.base_assumed[m].mean() > 0.5)
    base_v = float(np.nanmedian(base)) if (np.isfinite(base).any() and not assumed) else None
    ceiling = (base_v - p.elevation_m) if base_v is not None else None

    reasons = [f"{frac*100:.0f}% of the {p.corridor_radius_m/1000:.0f} km corridor obscured"]
    if breakdown["in cloud"] > 0.05:
        reasons.append(f"{breakdown['in cloud']*100:.0f}% of corridor terrain inside cloud")
    if breakdown["above cloud"] > 0.2:
        reasons.append("pass above a cloud layer: crest clear, valley approaches may be blocked")
    if breakdown["below cloud"] > 0.2 and ceiling is not None:
        reasons.append(f"overcast above; estimated ceiling {ceiling:+.0f} m relative to the pass")
    if cth_v is not None:
        reasons.append(f"cloud top ~{cth_v:,.0f} m AMSL")
    if assumed:
        reasons.append("thick cloud over the corridor; its base cannot be seen from space and is assumed to reach the terrain")

    conf, caveats = confidence(field, m, scene_meta)
    clabel = "HIGH" if conf >= 0.75 else "MEDIUM" if conf >= 0.5 else "LOW"
    if score == 1.0 and clabel == "LOW":
        score = 0.5
        reasons.insert(0, "data quality too low to confirm CLEAR: capped at 0.5")
    return PassResult(p.name, p.region, p.lat, p.lon, p.elevation_m, score, LABELS[score],
                      round(frac, 3), cth_v, base_v, ceiling, breakdown, round(conf, 2), clabel,
                      reasons, caveats)


def confidence(field: DEMCloudField, m, meta: dict):
    c = 1.0
    cav = []
    vza = meta.get("vza_deg")
    if vza is not None:
        if vza > 70:
            # near the edge of the satellite's disk: footprints are stretched
            # and parallax is huge; never better than LOW confidence
            c -= 0.55; cav.append(f"very oblique satellite view ({vza:.0f} deg): use INSAT/Meteosat for this region")
        elif vza > 60:
            c -= 0.25; cav.append(f"oblique satellite view ({vza:.0f} deg)")
    age = meta.get("age_min")
    if age is not None:
        if age > 90:
            c -= 0.4; cav.append(f"image is {age:.0f} min old")
        elif age > 30:
            c -= 0.15; cav.append(f"image is {age:.0f} min old")
    if meta.get("night") and not meta.get("has_ir039"):
        c -= 0.2; cav.append("night without 3.9 um channel: valley fog may be missed")
    elif meta.get("night"):
        c -= 0.1; cav.append("night: no visible channel")
    if meta.get("ref_from_climatology"):
        c -= 0.15; cav.append("mostly cloudy scene: clear-sky reference from climatology")
    snow_amb = float(((field.snow[m] > 0.4) & (field.prob_column[m] > 0.3) & (field.prob_column[m] < 0.7)).mean())
    if snow_amb > 0.2:
        c -= 0.2; cav.append(f"snow/cloud ambiguity over {snow_amb*100:.0f}% of corridor")
    hidden = float(((field.cirrus[m] > 0.5) & field.base_assumed[m]).mean())
    if hidden > 0.2 and not meta.get("night"):
        c -= 0.3; cav.append("thick cloud under cirrus: its top is hidden, score may be one step pessimistic")
    if float((field.cirrus[m] > 0.5).mean()) > 0.3:
        if meta.get("night"):
            c -= 0.45; cav.append("night + cirrus: any cloud beneath the cirrus is invisible")
        else:
            c -= 0.1; cav.append("thin cirrus present: layers below it may be hidden")
    if field.cth_unc is not None:
        obs_m = obscuration_cache = None
        u = field.cth_unc[m]
        cloudy_m = np.isfinite(u)
        broken = 0.1 < cloudy_m.mean() < 0.95 or float(((field.prob_column[m] > 0.3) & (field.prob_column[m] < 0.9)).mean()) > 0.3
        if cloudy_m.any() and np.nanmedian(u) > 500 and broken:
            c -= 0.3; cav.append("broken, low-contrast cloud near the pass: score may be one step pessimistic")
    px = meta.get("pixel_km", 4)
    cav.append(f"satellite pixel ~{px:.0f} km; sub-pixel detail is model-estimated")
    p = field.prob_column[m]
    marginal = float(((p > 0.35) & (p < 0.65)).mean())
    if marginal > 0.3:
        c -= 0.15; cav.append("many marginal cloud detections in corridor")
    return max(0.0, min(1.0, c)), cav


def region_cloud_amount(field: DEMCloudField, polygon_lonlat, th: Thresholds | None = None) -> dict:
    """Cloud amount inside any user polygon [(lon, lat), ...]."""
    LON, LAT = field.dem.mesh()
    path = MplPath(np.asarray(polygon_lonlat))
    inside = path.contains_points(np.column_stack([LON.ravel(), LAT.ravel()])).reshape(LON.shape)
    if not inside.any():
        return {"error": "polygon contains no DEM cells"}
    col = field.prob_column[inside]
    cell = field.prob_cell[inside]
    frac = float((col >= 0.5).mean())
    cls = field.cls[inside]
    cth = field.cth_m[inside]
    return {
        "cells": int(inside.sum()),
        "area_km2": round(inside.sum() * np.prod(field.dem.cell_size_m()) / 1e6, 1),
        "cloud_cover_percent": round(frac * 100, 1),
        "cloud_cover_oktas": int(round(frac * 8)),
        "terrain_in_cloud_percent": round(float((cell >= 0.5).mean()) * 100, 1),
        "classes": {CLASS_NAMES[k]: round(float((cls == k).mean()) * 100, 1) for k in (CLEAR, ABOVE, IN_CLOUD, BELOW)},
        "median_cloud_top_m": float(np.nanmedian(cth)) if np.isfinite(cth).any() else None,
        "terrain_min_max_m": [float(field.dem.z[inside].min()), float(field.dem.z[inside].max())],
    }
