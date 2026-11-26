"""Regional profiles so one tool works from Ladakh to the Andamans.

Each site is assigned a profile from its location and elevation. The profile
switches tests on/off and retunes thresholds for the weather that dominates
there: snow in the Himalaya, winter radiation fog over the Indo-Gangetic
plains, monsoon stratus on the Western Ghats, convection in the Northeast and
islands.
"""
from __future__ import annotations

import dataclasses

from .config import Thresholds

PROFILES = {
    "himalaya": dict(
        snow_test=True, clim_offset_k=0.0,
        note="snow/cloud separation on; terrain-adjusted IR; deep valleys"),
    "northeast": dict(
        snow_test=False, clim_offset_k=1.0, default_low_cloud_thickness_m=1000.0,
        note="frequent low stratus and convection over hills; no snow test"),
    "western_ghats": dict(
        snow_test=False, clim_offset_k=2.0, default_low_cloud_thickness_m=1200.0,
        note="monsoon stratus banked against the Ghats; cloud often sits on the crest"),
    "plains": dict(
        snow_test=False, clim_offset_k=2.0, fog_btd_low=1.0, fog_btd_high=3.0,
        default_low_cloud_thickness_m=300.0, low_cloud_max_depth_m=800.0,
        note="winter fog mode: more sensitive night fog test, thin fog layers"),
    "islands": dict(
        snow_test=False, clim_offset_k=3.0,
        note="maritime convection; low terrain"),
    "peninsula": dict(
        snow_test=False, clim_offset_k=3.0,
        note="general profile"),
}


def detect_region(lat: float, lon: float, elevation_m: float) -> str:
    if lat >= 26.5 and elevation_m >= 2500:
        return "himalaya"
    if lat >= 28.5 and lon <= 81.5 and elevation_m >= 1000:
        return "himalaya"
    if lon >= 92.0 and lat < 14.5:
        return "islands"
    if lon < 74.0 and lat < 12.5:
        return "islands"        # Lakshadweep
    if lon >= 89.5 and lat < 29.5:
        return "northeast"
    ghats_lon = 77.4 if lat < 12.5 else 75.9          # crest line runs NNW
    if 8.0 <= lat <= 21.5 and 72.8 <= lon <= ghats_lon and elevation_m >= 300:
        return "western_ghats"
    if lat >= 22.0 and elevation_m < 600:
        return "plains"
    return "peninsula"


def thresholds_for(site, base: Thresholds) -> Thresholds:
    region = detect_region(site.lat, site.lon, site.elevation_m)
    overrides = {k: v for k, v in PROFILES[region].items() if k != "note"}
    return dataclasses.replace(base, region_profile=region, **overrides)
