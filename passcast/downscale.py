"""Put satellite cloud information onto the high-resolution DEM.

The problem statement asks for satellite grid values to be interpolated onto a
<100 m DEM with a technique suited to hilly terrain. Plain bilinear
interpolation of a 2-4 km pixel would paint the whole valley and ridge the same
colour. Instead we use a three-stage physically based downscaling:

1. Parallax correction -- each pixel is moved to where its content really is
   (cloud tops by their height, clear ground by terrain height).
2. Smooth C1 interpolation (Clough-Tocher piecewise cubic) of cloud
   probability and cloud-top height onto the DEM grid.
3. Terrain conditioning -- a DEM cell can only be inside a cloud if it lies
   below the cloud top. For low/mid cloud the probability is multiplied by a
   logistic function of (cloud top - cell elevation), so peaks that poke
   through a valley cloud sea are correctly shown clear. For valley stratus the
   top height is refined with the "cloud-edge terrain height" method used for
   fog/low-stratus retrieval (Bendix; Cermak & Bendix): where a cloud deck
   meets the slopes, the terrain height at the edge equals the cloud-top height.

Each DEM cell is then classified relative to the cloud layer:
  0 CLEAR, 1 ABOVE_CLOUD (cloud sea below), 2 IN_CLOUD, 3 BELOW_CLOUD (ceiling above)
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.interpolate import CloughTocher2DInterpolator, LinearNDInterpolator, NearestNDInterpolator
from scipy.ndimage import gaussian_filter, binary_dilation, binary_erosion, label

from .satellite import parallax_correct
from .dem import DEMGrid
from .config import Thresholds

CLEAR, ABOVE, IN_CLOUD, BELOW = 0, 1, 2, 3
CLASS_NAMES = {CLEAR: "clear", ABOVE: "above cloud", IN_CLOUD: "in cloud", BELOW: "below cloud"}


@dataclass
class DEMCloudField:
    dem: DEMGrid
    prob_column: np.ndarray     # is there cloud anywhere in the column above (0-1)
    prob_cell: np.ndarray       # is this terrain cell inside cloud (0-1)
    cth_m: np.ndarray           # cloud-top height (m AMSL), NaN where clear
    base_m: np.ndarray          # estimated cloud base (m AMSL), NaN where clear
    cls: np.ndarray             # class codes above
    cth_unc: np.ndarray         # cloud-top uncertainty (m)
    base_assumed: np.ndarray    # True where base is unobservable and assumed at terrain
    snow: np.ndarray
    cirrus: np.ndarray
    notes: list = field(default_factory=list)


def terrain_under_pixels(scene, dem: DEMGrid, iterations=2):
    """Mean terrain height under each satellite pixel footprint (parallax aware)."""
    dy, dx = dem.cell_size_m()
    sigma = (scene.pixel_km * 1000 / 2.355) / np.array([dy, dx])   # FWHM ~ pixel size
    smooth = DEMGrid(dem.lat, dem.lon, gaussian_filter(dem.z, sigma), dem.source)
    lat, lon = scene.lat, scene.lon
    z = smooth.sample(lat, lon)
    for _ in range(iterations):   # ground is displaced by its own height too
        la, lo = parallax_correct(scene.lat, scene.lon, z, scene.vza, scene.vaz)
        z = smooth.sample(la, lo)
    return z.astype("float32")


def _interp(points, values, targets, method="cubic"):
    ok = np.isfinite(values)
    if ok.sum() < 4:
        return np.full(targets[0].shape, np.nan, dtype="float32")
    pts, vals = points[ok], values[ok]
    if method == "cubic":
        f = CloughTocher2DInterpolator(pts, vals)
    else:
        f = LinearNDInterpolator(pts, vals)
    out = f(targets[0], targets[1])
    miss = ~np.isfinite(out)
    if miss.any():
        out[miss] = NearestNDInterpolator(pts, vals)(targets[0][miss], targets[1][miss])
    return out.astype("float32")


def downscale(scene, cm, dem: DEMGrid, z_pix, th: Thresholds,
              cloud_base_m: float | None = None) -> DEMCloudField:
    notes = []
    # ---- 1. parallax ------------------------------------------------------
    height = np.where(np.isfinite(cm.cth_m), cm.cth_m, z_pix)
    if th.parallax_correction and scene.vza is not None:
        la, lo = parallax_correct(scene.lat, scene.lon, height, scene.vza, scene.vaz)
        shift_km = np.nanmedian(height * np.tan(np.radians(scene.vza))) / 1000
        notes.append(f"parallax correction applied (median shift {shift_km:.1f} km)")
    else:
        la, lo = scene.lat, scene.lon
    pts = np.column_stack([lo.ravel(), la.ravel()])
    LON, LAT = dem.mesh()
    tgt = (LON, LAT)

    # ---- 2. smooth interpolation -------------------------------------------
    p_col = np.clip(_interp(pts, cm.prob.ravel(), tgt, "cubic"), 0, 1)
    cth = _interp(pts, cm.cth_m.ravel(), tgt, "linear")     # only cloudy points carry values
    unc = _interp(pts, cm.cth_unc_m.ravel(), tgt, "linear")
    unc = np.where(np.isfinite(unc), unc, th.cloud_top_sigma_m)
    snow = np.clip(_interp(pts, cm.snow.ravel(), tgt, "linear"), 0, 1)
    cirrus = np.clip(_interp(pts, cm.cirrus.ravel(), tgt, "linear"), 0, 1)
    opaque = np.clip(_interp(pts, cm.opaque.ravel(), tgt, "linear"), 0, 1)
    multi = np.clip(_interp(pts, cm.multilayer.ravel(), tgt, "linear"), 0, 1)
    z = dem.z

    # local mean terrain (~5 km) to judge whether a cloud is "low"
    dy, dx = dem.cell_size_m()
    z_local = gaussian_filter(z, 5000 / np.array([dy, dx]))

    cloudy = p_col >= 0.5
    depth = cth - z_local
    is_low = cloudy & np.isfinite(cth) & (depth < th.low_cloud_max_depth_m) & (cirrus < 0.5)

    # ---- 3a. cloud-edge terrain height refinement for low cloud -------------
    if is_low.sum() > 50:
        lab, n = label(is_low)
        refined = 0
        for k in range(1, n + 1):
            comp = lab == k
            if comp.sum() < 50:
                continue
            edge = comp & ~binary_erosion(comp, iterations=2)
            ring = binary_dilation(comp, iterations=3) & ~comp & ~cloudy
            if edge.sum() < 10 or ring.sum() < 10:
                continue
            # clear slopes adjoining the deck: cloud top sits near their lower part
            top = float(np.percentile(z[ring], 25))
            cth[comp] = 0.5 * cth[comp] + 0.5 * top
            refined += 1
        if refined:
            notes.append(f"valley-cloud top refined from terrain edges ({refined} deck(s))")

    # ---- 3b. terrain conditioning -----------------------------------------
    # conservative: a cell counts as possibly in cloud up to top + 1 sigma
    cth_hi = cth + unc
    margin = cth_hi - z
    cond = 1.0 / (1.0 + np.exp(-margin / np.maximum(unc, th.cloud_top_sigma_m)))
    terrain_matters = np.isfinite(cth) & (cth - z_local < 3000) & (cirrus < 0.5)
    p_cell = np.where(terrain_matters, p_col * cond, p_col)
    p_cell = np.where(np.isfinite(p_cell), p_cell, 0).astype("float32")

    # ---- cloud base ------------------------------------------------------
    # Satellites see cloud tops, not bases. Rules (aviation-conservative):
    #   thin cirrus alone            -> high base, does not obscure terrain
    #   low cloud / valley stratus   -> base = top - typical stratus thickness
    #   opaque mid/deep cloud, or a thick layer hidden under cirrus
    #                                -> base unknown: assume it reaches the terrain
    thin_cirrus = (cirrus >= 0.5) & (multi < 0.5)
    hidden_layer = multi >= 0.5
    deep = (opaque >= 0.5) & ~is_low & ~thin_cirrus
    base = cth - th.default_low_cloud_thickness_m
    base = np.where(thin_cirrus, np.maximum(cth - 2000.0, th.high_cloud_base_m), base)
    unknown = (deep | hidden_layer) & cloudy
    base = np.where(unknown, z_local - 1000.0, base)
    mid = cloudy & ~is_low & ~thin_cirrus & ~unknown
    base = np.where(mid, cth - th.default_mid_cloud_thickness_m, base)
    if unknown.any():
        notes.append("thick cloud: base not observable, assumed to reach terrain (conservative)")
    if (hidden_layer & cloudy).any():
        notes.append("thick cloud detected beneath thin cirrus")
    if cloud_base_m is not None:
        base = np.where(np.isfinite(cth), np.minimum(cth, cloud_base_m), base)
        notes.append(f"cloud base from ancillary data: {cloud_base_m:.0f} m")
    base = np.where(cloudy, base, np.nan)
    # a hidden lower layer is not limited by the cirrus top height
    # for a hidden lower layer, the chance a terrain cell is in cloud is the
    # chance that the thick layer exists there
    p_cell = np.where(cirrus >= 0.5, multi * p_col, p_cell).astype("float32")

    # ---- classes -----------------------------------------------------------
    cls = np.full(z.shape, CLEAR, dtype="uint8")
    cls[cloudy & (z > cth_hi) & ~hidden_layer] = ABOVE
    cls[cloudy & (z <= cth_hi) & (z >= base)] = IN_CLOUD
    cls[cloudy & hidden_layer] = IN_CLOUD
    cls[cloudy & (z < base)] = BELOW
    cls[cloudy & ~np.isfinite(cth)] = IN_CLOUD

    if cloudy.any() and np.nanmedian(unc[cloudy]) > 500:
        notes.append("low-contrast cloud: top height uncertain, treated conservatively")
    return DEMCloudField(dem, p_col.astype("float32"), p_cell, np.where(cloudy, cth, np.nan).astype("float32"),
                         base.astype("float32"), cls, np.where(cloudy, unc, np.nan).astype("float32"), unknown,
                         snow.astype("float32"), cirrus.astype("float32"), notes)


def crop_field(f: DEMCloudField, lat0, lon0, half_km) -> DEMCloudField:
    import math
    dlat = half_km / 111.0
    dlon = half_km / (111.0 * math.cos(math.radians(lat0)))
    i = (f.dem.lat >= lat0 - dlat) & (f.dem.lat <= lat0 + dlat)
    j = (f.dem.lon >= lon0 - dlon) & (f.dem.lon <= lon0 + dlon)
    c = lambda a: a[np.ix_(i, j)]
    d = DEMGrid(f.dem.lat[i], f.dem.lon[j], c(f.dem.z), f.dem.source)
    return DEMCloudField(d, c(f.prob_column), c(f.prob_cell), c(f.cth_m), c(f.base_m), c(f.cls),
                         c(f.cth_unc), c(f.base_assumed), c(f.snow), c(f.cirrus), f.notes)
