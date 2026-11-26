"""Terrain-aware cloud detection on the satellite grid.

Standard cloud masks compare brightness temperature with a fixed threshold.
In the Himalaya that fails twice over: a 5,500 m ridge is 35 K colder than the
valley below it, so cold terrain looks like cloud; and fresh snow is as bright
as cloud in visible imagery. This module therefore:

1. Converts every IR value to a *sea-level-equivalent* temperature using the
   mean terrain height under the pixel and a standard lapse rate, so a cold
   ridge and a warm valley become comparable.
2. Estimates the clear-sky reference from the scene itself (high percentile),
   falling back to monthly climatology when the scene is mostly cloudy.
3. Separates snow from cloud in daylight with the Normalised Difference Snow
   Index (snow is dark at 1.6 um, water cloud is bright).
4. Adds a split-window test for thin cirrus and a 3.9 um test for night fog.

Each test yields a confidence 0-1; groups are combined, giving a cloud
probability per pixel plus diagnostic flags used for the confidence score.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import Thresholds, CLIM_SEA_LEVEL_SKIN_K


def ramp(x, lo, hi):
    return np.clip((np.asarray(x, dtype="float32") - lo) / (hi - lo), 0.0, 1.0)


@dataclass
class CloudMaskResult:
    prob: np.ndarray          # cloud probability 0-1
    cth_m: np.ndarray         # cloud-top height estimate (m AMSL), NaN where clear
    cth_unc_m: np.ndarray     # 1-sigma uncertainty of cth (m)
    snow: np.ndarray          # snow-likelihood 0-1
    cirrus: np.ndarray        # thin cirrus confidence 0-1
    opaque: np.ndarray        # optically thick cloud likelihood 0-1
    multilayer: np.ndarray    # thin cirrus over a thick lower layer 0-1
    night: np.ndarray         # bool
    t_ref_sl: float           # clear-sky sea-level reference (K)
    ref_from_climatology: bool
    tests_used: list


def cloud_mask(scene, terrain_z_pix, th: Thresholds) -> CloudMaskResult:
    ir = scene.get("ir108")
    G = th.lapse_rate_k_per_km / 1000.0
    t_eq = ir + G * terrain_z_pix                      # sea-level-equivalent BT
    night = scene.sza >= th.night_sza_deg
    tests = ["ir_terrain_adjusted"]

    # ---- clear-sky reference --------------------------------------------
    clim = CLIM_SEA_LEVEL_SKIN_K[scene.time.month] + th.clim_offset_k
    p = float(np.nanpercentile(t_eq, th.clear_ref_percentile))
    from_clim = p < clim - 8.0          # scene too cloudy to trust its warm tail
    t_ref = clim if from_clim else p

    deficit = t_ref - t_eq
    c_ir = ramp(deficit, th.ir_deficit_low, th.ir_deficit_high)

    # ---- thin cirrus: split window --------------------------------------
    c_ci = np.zeros_like(c_ir)
    if scene.get("ir120") is not None:
        btd = ir - scene.get("ir120")
        thr = np.clip(0.5 + 0.03 * (ir - 250.0), 0.5, 3.0)
        c_ci = ramp(btd - thr, 0.0, 1.5)
        tests.append("split_window_cirrus")

    # ---- night fog / low stratus -----------------------------------------
    c_fog = np.zeros_like(c_ir)
    if scene.get("ir039") is not None and night.any():
        d39 = ir - scene.get("ir039")
        c_fog = np.where(night, ramp(d39, th.fog_btd_low, th.fog_btd_high), 0.0)
        tests.append("night_fog_3.9um")

    # ---- daytime visible + snow discrimination ---------------------------
    c_vis = np.zeros_like(c_ir)
    snow = np.zeros_like(c_ir)
    # without visible data: thick = very cold relative to terrain and not cirrus
    opaque = ramp(deficit, 12.0, 25.0) * (1.0 - c_ci)
    vis, nir = scene.get("vis064"), scene.get("nir16")
    if vis is not None:
        mu = np.cos(np.radians(np.minimum(scene.sza, 85.0)))
        r = vis / np.maximum(mu, 0.1)                     # crude sun-angle normalisation
        if nir is not None and th.snow_test:
            rn = nir / np.maximum(mu, 0.1)
            ndsi = (r - rn) / np.maximum(r + rn, 1e-3)
            # Ice cloud also has a high NDSI; snow must additionally be near the
            # clear-sky temperature of the terrain it sits on.
            snow = (ramp(ndsi, th.ndsi_snow_low, th.ndsi_snow_high) * ramp(r, 0.15, 0.30)
                    * (1.0 - ramp(deficit, 6.0, 12.0)) * (1.0 - c_ci))
            tests.append("ndsi_snow")
        c_vis = ramp(r, th.vis_cloud_low, th.vis_cloud_high) * (1.0 - snow)
        c_vis = np.where(night, 0.0, c_vis)
        opaque = np.where(night, opaque, ramp(r, 0.45, 0.65) * (1.0 - snow))
        tests.append("visible_reflectance")
    multilayer = np.where(night, 0.0, c_ci * opaque)

    # Snow surfaces radiate a few K colder than bare ground: soften the IR test
    # where snow is confidently identified and no cirrus signal exists.
    c_ir_eff = c_ir * (1.0 - 0.6 * snow * (1.0 - c_ci))

    thermal = np.maximum.reduce([c_ir_eff, c_ci, c_fog])
    prob = 1.0 - (1.0 - thermal) * (1.0 - c_vis)
    # Bright-but-warm daytime pixels: require some thermal support unless very bright
    prob = np.where(~night & (thermal < 0.2), np.minimum(prob, 0.5 + 0.5 * ramp(c_vis, 0.8, 1.0)), prob)
    prob = np.clip(prob, 0, 1)

    # ---- cloud-top height from IR (lapse-rate method) ---------------------
    cth = (t_ref - ir) / G
    cth = np.maximum(cth, terrain_z_pix)
    # semi-transparent cirrus looks too warm -> push to a typical cirrus height
    cth = np.where(c_ci > 0.5, np.maximum(cth, 8000.0), cth)
    cth = np.where(prob >= 0.5, cth, np.nan)
    # Uncertainty grows when the cloud is barely colder than the terrain under it
    # (low cloud over high ground) or only partly fills the pixel: the IR value
    # is then a mix of cloud and warm ground and the height comes out too low.
    unc = (150.0 + 900.0 * (1.0 - ramp(deficit, 5.0, 15.0))
           + 600.0 * (1.0 - ramp(prob, 0.6, 0.95)))
    unc = np.where(prob >= 0.5, unc, np.nan)

    f32 = lambda a: np.asarray(a, dtype="float32")
    return CloudMaskResult(f32(prob), f32(cth), f32(unc), f32(snow), f32(c_ci), f32(opaque), f32(multilayer),
                           night, float(t_ref), bool(from_clim), tests)
