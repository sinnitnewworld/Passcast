"""Synthetic end-to-end scenario with known truth.

Builds Himalayan-style terrain with three passes, places known clouds on it,
simulates what a 4 km geostationary imager at 74 E (INSAT-3DR's slot) would
record -- including parallax displacement, footprint blurring and noise -- and
then runs the real pipeline on that simulated image. Because the truth is
known, we can measure whether each pass gets the right score.

Scenario (default, daytime):
  Pass A: valley stratus below the crest (top ~3,500 m) -> crest clear, expect 1
  Pass B: deep convective cloud engulfing the pass       -> expect 0
  Pass C: broken cumulus over part of the corridor + thin cirrus aloft -> expect 0.5
  Snow on all terrain above ~5,000 m (must NOT be called cloud)
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
from scipy.ndimage import gaussian_filter

from .dem import synthetic_himalaya, DEMGrid
from .satellite import SatScene, geo_view_angles, solar_zenith
from .config import Pass, Thresholds
from .downscale import CLEAR, ABOVE, IN_CLOUD, BELOW

LAPSE = 6.5e-3


def build_truth(dem: DEMGrid, scenario="day"):
    LON, LAT = dem.mesh()
    lat0, lon0 = dem.lat.mean(), dem.lon.mean()
    X = (LON - lon0) * 111.0 * math.cos(math.radians(lat0))
    Y = (LAT - lat0) * 111.0
    z = dem.z
    (na, laa, loa, za), (nb, lab, lob, zb), (nc, lac, loc, zc) = dem.passes
    xa, ya = (loa - lon0) * 111 * math.cos(math.radians(lat0)), (laa - lat0) * 111
    xb, yb = (lob - lon0) * 111 * math.cos(math.radians(lat0)), (lab - lat0) * 111
    xc, yc = (loc - lon0) * 111 * math.cos(math.radians(lat0)), (lac - lat0) * 111

    cloud = np.zeros(z.shape, bool)
    cth = np.full(z.shape, np.nan)
    base = np.full(z.shape, np.nan)
    kind = np.zeros(z.shape, "uint8")      # 1 water stratus, 2 deep ice, 3 cumulus, 4 cirrus

    # valley stratus around pass A: fills valleys below 3,500 m in the SW quadrant
    strat_top = min(za - 450, 3600.0)
    valley_region = np.hypot(X - xa, Y - ya) < 22
    smooth_z = gaussian_filter(z, 3)
    m = valley_region & (smooth_z < strat_top)
    cloud |= m; cth[m] = strat_top; base[m] = strat_top - 700; kind[m] = 1

    # deep convective cell over pass B
    m = np.hypot(X - xb, Y - yb) < 9
    cloud |= m; cth[m] = 9000.0; base[m] = zb - 600; kind[m] = 2

    # broken cumulus covering part of pass C corridor (east half)
    rng = np.random.default_rng(3)
    blobs = gaussian_filter(rng.standard_normal(z.shape), 12)
    m = (np.hypot(X - xc, Y - yc) < 7) & (X > xc + 0.8) & (blobs > -0.01)
    cloud |= m; cth[m] = zc + 900; base[m] = zc - 400; kind[m] = 3

    # thin cirrus band aloft over pass C (does not obscure the pass)
    cirrus = np.abs((Y - yc) - 0.3 * (X - xc)) < 6
    snow = (z > 5000) & ~cloud
    return dict(cloud=cloud, cth=cth, base=base, kind=kind, cirrus=cirrus, snow=snow,
                strat_top=strat_top)


def radiances(dem, truth, t_sl=296.0, night=False, rng=None):
    rng = rng or np.random.default_rng(0)
    z = dem.z
    bt_s = t_sl - LAPSE * z - np.where(truth["snow"], 3.0, 0.0)
    bt11 = bt_s.copy()
    cl = truth["cloud"]
    bt11[cl] = t_sl - LAPSE * truth["cth"][cl]
    bt12 = bt11 - np.where(cl, 0.3, 0.7)
    vis = np.where(truth["snow"], 0.65, 0.12)
    nir = np.where(truth["snow"], 0.07, 0.22)
    k = truth["kind"]
    vis = np.where(cl, np.where(k == 2, 0.85, 0.70), vis)
    nir = np.where(cl, np.where(k == 2, 0.30, 0.45), nir)
    # thin cirrus: 50 % transmissive ice layer at 10 km
    ci = truth["cirrus"] & ~(k == 2)
    t_ci = t_sl - LAPSE * 10000
    bt11 = np.where(ci, 0.55 * bt11 + 0.45 * t_ci, bt11)
    bt12 = np.where(ci, bt11 - 3.2, bt12)
    vis = np.where(ci, vis + 0.12, vis)
    nir = np.where(ci, nir + 0.05, nir)
    bt39 = bt11 - np.where(cl & (k == 1), 3.5, 0.4)
    height = np.where(cl, truth["cth"], np.where(ci, 10000.0, z))
    return dict(ir108=bt11, ir120=bt12, vis064=vis, nir16=nir, ir039=bt39, height=height)


def simulate_scene(dem, truth, when, sub_lon=74.0, pixel_km=4.0, night=False, seed=1) -> SatScene:
    rng = np.random.default_rng(seed)
    rad = radiances(dem, truth, night=night)
    LON, LAT = dem.mesh()
    vza, vaz = geo_view_angles(LAT, LON, sub_lon)
    # forward parallax: content appears displaced AWAY from the satellite
    d = rad["height"] * np.tan(np.radians(vza))
    az = np.radians(vaz)
    app_lat = LAT - d * np.cos(az) / 111000.0
    app_lon = LON - d * np.sin(az) / (111000.0 * np.cos(np.radians(LAT)))

    lat0, lon0 = dem.lat.mean(), dem.lon.mean()
    dlat = pixel_km / 111.0
    dlon = pixel_km / (111.0 * math.cos(math.radians(lat0)))
    plat = np.arange(dem.lat[0], dem.lat[-1] + dlat, dlat)
    plon = np.arange(dem.lon[0], dem.lon[-1] + dlon, dlon)
    iy = np.clip(((app_lat - plat[0]) / dlat + 0.5).astype(int), 0, len(plat) - 1)
    ix = np.clip(((app_lon - plon[0]) / dlon + 0.5).astype(int), 0, len(plon) - 1)
    idx = (iy * len(plon) + ix).ravel()
    cnt = np.bincount(idx, minlength=len(plat) * len(plon)).astype(float)
    ch = {}
    for name in ("ir108", "ir120", "vis064", "nir16", "ir039"):
        s = np.bincount(idx, weights=rad[name].ravel(), minlength=cnt.size)
        img = (s / np.maximum(cnt, 1)).reshape(len(plat), len(plon))
        empty = cnt.reshape(img.shape) == 0
        if empty.any():
            from scipy.ndimage import distance_transform_edt
            _, (ry, rx) = distance_transform_edt(empty, return_indices=True)
            img = img[ry, rx]
        img = gaussian_filter(img, 0.5)    # sensor PSF
        noise = 0.2 if name.startswith("ir") else 0.01
        ch[name] = (img + rng.normal(0, noise, img.shape)).astype("float32")
    PLON, PLAT = np.meshgrid(plon, plat)
    if night:
        ch["vis064"] = None
        ch["nir16"] = None
    else:
        ch["ir039"] = None   # keep the day demo to the channels a pilot would see
    svza, svaz = geo_view_angles(PLAT, PLON, sub_lon)
    sza = solar_zenith(PLAT, PLON, when)
    return SatScene(PLAT, PLON, when, "Simulated INSAT-3DR (4 km)", ch, sza, svza, svaz, pixel_km)


def truth_scores(dem, truth, passes, th: Thresholds):
    """Ground-truth score per pass from the known cloud field."""
    LON, LAT = dem.mesh()
    z = dem.z
    cl = truth["cloud"]
    obs = cl & (z <= truth["cth"]) & (z >= truth["base"])
    low_ceiling = cl & (z < truth["base"]) & (truth["base"] - z < th.min_ceiling_above_terrain_m)
    obs = obs | low_ceiling
    out = {}
    for p in passes:
        dy = (LAT - p.lat) * 111000
        dx = (LON - p.lon) * 111000 * math.cos(math.radians(p.lat))
        m = np.hypot(dx, dy) <= p.corridor_radius_m
        f = float(obs[m].mean())
        s = 1.0 if f <= th.score_clear_max_obscured else 0.5 if f <= th.score_partial_max_obscured else 0.0
        out[p.name] = (s, f)
    return out


def make_demo(night=False):
    dem = synthetic_himalaya(lat0=34.0, lon0=77.0, half_km=45.0, res_m=90.0)
    passes = [Pass(n, la, lo, round(z), "Synthetic test range") for (n, la, lo, z) in dem.passes]
    truth = build_truth(dem)
    when = dt.datetime(2026, 11, 26, 18, 30) if night else dt.datetime(2026, 11, 26, 6, 0)
    scene = simulate_scene(dem, truth, when, night=night)
    return dem, passes, truth, scene, when
