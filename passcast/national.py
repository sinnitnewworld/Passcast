"""All-India 1 km product: lets the web page score ANY location instantly.

Each run cuts India into 5 x 5 degree tiles on a 0.01 degree (~1 km) grid and,
for every tile, runs the same science as the 30 m site product (terrain-adjusted
cloud mask, parallax, terrain conditioning, layer classes). It writes compact
gzip binary tiles that the browser fetches on demand:

  terrain/<key>.bin.gz   uint16 elevation (m), static, built once and cached
  run/<key>.bin.gz       per run: obs u8 | pcol u8 | cls u8 | flags u8 | cth u16 (10 m units)
  manifest.json          image time, satellite, grid, per-tile metadata

The browser then averages the cells inside the chosen corridor and applies the
same score / confidence rules as scoring.py (see score_cells below, which the
page mirrors).
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import gzip
import json
import math
import os
import time
from pathlib import Path

import numpy as np

from .config import Thresholds, SATELLITES, Pass
from .dem import DEMGrid
from .cloudmask import cloud_mask
from .downscale import downscale, terrain_under_pixels, CLEAR, ABOVE, IN_CLOUD, BELOW
from .scoring import obscuration
from .regions import PROFILES

RES = 0.01                 # degrees (~1.1 km)
TILE = 5.0                 # degrees
N = int(round(TILE / RES))  # 500 cells per side
# 5x5 degree tiles (south-west corner) that contain Indian territory, incl. islands
TILES = [
    (5, 70), (5, 75), (5, 90),
    (10, 70), (10, 75), (10, 80), (10, 90),
    (15, 70), (15, 75), (15, 80), (15, 85),
    (20, 65), (20, 70), (20, 75), (20, 80), (20, 85), (20, 90),
    (25, 65), (25, 70), (25, 75), (25, 80), (25, 85), (25, 90), (25, 95),
    (30, 70), (30, 75), (30, 80),
    (35, 70), (35, 75),
]
GLO90 = "https://copernicus-dem-90m.s3.amazonaws.com/{n}/{n}.tif"

FLAG_NIGHT, FLAG_CIRRUS, FLAG_SNOWAMB, FLAG_BASEASSUMED = 1, 2, 4, 8
FLAG_HIGHUNC, FLAG_NODATA, FLAG_MARGINAL = 16, 32, 64


def key(lat0, lon0):
    return f"N{lat0:02d}E{lon0:03d}"


def tile_axes(lat0, lon0):
    lat = lat0 + RES * (np.arange(N) + 0.5)
    lon = lon0 + RES * (np.arange(N) + 0.5)
    return lat, lon


# ---------------------------------------------------------------------------
# Terrain (built once, cached)
# ---------------------------------------------------------------------------
def _glo90_name(la, lo):
    return (f"Copernicus_DSM_COG_30_{'N' if la >= 0 else 'S'}{abs(la):02d}_00_"
            f"{'E' if lo >= 0 else 'W'}{abs(lo):03d}_00_DEM")


def _read_degree(la, lo, cells):
    """Read one 1x1 degree GLO-90 tile at reduced resolution using COG overviews
    (only the needed bytes are fetched). Ocean tiles do not exist -> zeros."""
    import rasterio
    from rasterio.enums import Resampling
    url = "/vsicurl/" + GLO90.format(n=_glo90_name(la, lo))
    try:
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif"):
            with rasterio.open(url) as src:
                z = src.read(1, out_shape=(cells, cells), resampling=Resampling.average).astype("float32")
        z = np.where(z < -500, 0, z)[::-1]          # north-up -> south-up
        return np.clip(z, 0, 9000)
    except Exception:
        return np.zeros((cells, cells), "float32")


def build_terrain(cache_dir="~/.passcast/terrain1km", tiles=TILES, workers=16) -> dict:
    cache = Path(os.path.expanduser(cache_dir))
    cache.mkdir(parents=True, exist_ok=True)
    out = {}
    cells = int(round(1 / RES))
    for (la0, lo0) in tiles:
        f = cache / f"{key(la0, lo0)}.npy"
        if f.exists():
            out[key(la0, lo0)] = np.load(f)
            continue
        z = np.zeros((N, N), "float32")
        jobs = {}
        with cf.ThreadPoolExecutor(workers) as ex:
            for i in range(int(TILE)):
                for j in range(int(TILE)):
                    jobs[ex.submit(_read_degree, la0 + i, lo0 + j, cells)] = (i, j)
            for fut in cf.as_completed(jobs):
                i, j = jobs[fut]
                z[i * cells:(i + 1) * cells, j * cells:(j + 1) * cells] = fut.result()
        np.save(f, z)
        out[key(la0, lo0)] = z
        print(f"  terrain {key(la0, lo0)}: max {z.max():.0f} m")
    return out


def write_terrain(terrain: dict, outdir):
    d = Path(outdir) / "terrain"
    d.mkdir(parents=True, exist_ok=True)
    for k, z in terrain.items():
        (d / f"{k}.bin.gz").write_bytes(gzip.compress(np.clip(z, 0, 9000).astype("<u2").tobytes(), 6))


# ---------------------------------------------------------------------------
# Per-run tiles
# ---------------------------------------------------------------------------
def tile_profile(lat0, lon0, z):
    """Pick per-tile settings: snow test where high terrain exists, fog mode for
    plains tiles. (Per-cell regions would be finer; documented approximation.)"""
    high = float((z > 2500).mean())
    if lat0 >= 25 and high > 0.02:
        return "himalaya"
    if lon0 >= 90:
        return "islands" if lat0 < 15 else "northeast"
    if lat0 >= 20 and float(np.median(z)) < 600:
        return "plains"
    if lat0 < 20 and lon0 <= 75:
        return "western_ghats"
    return "peninsula"


def process_tile(scene, lat0, lon0, z, base_th: Thresholds):
    import dataclasses
    lat, lon = tile_axes(lat0, lon0)
    dem = DEMGrid(lat, lon, z)
    prof = tile_profile(lat0, lon0, z)
    over = {k: v for k, v in PROFILES[prof].items() if k != "note"}
    th = dataclasses.replace(base_th, region_profile=prof, **over)
    bbox = (lon0, lat0, lon0 + TILE, lat0 + TILE)
    sub = scene.subset(bbox, pad_deg=0.15)
    ir = sub.get("ir108")
    geo_ok = np.isfinite(sub.lat) & np.isfinite(sub.lon)
    valid_sat = np.isfinite(ir) & geo_ok
    # give off-disk pixels a harmless position far away so geometry code stays finite
    sub.lat = np.where(geo_ok, sub.lat, -89.0); sub.lon = np.where(geo_ok, sub.lon, 0.0)
    if valid_sat.mean() < 0.05:
        raise ValueError("no valid satellite data over tile")
    # fill off-disk / missing pixels so interpolation is stable; mark them later
    for k, a in list(sub.channels.items()):
        if a is not None:
            sub.channels[k] = np.where(np.isfinite(a), a, np.nanmedian(a)).astype("float32")
    z_pix = terrain_under_pixels(sub, dem)
    cm = cloud_mask(sub, z_pix, th)
    # drop invalid / off-disk pixels from the interpolation entirely
    for name in ("prob", "cth_m", "cth_unc_m", "snow", "cirrus", "opaque", "multilayer"):
        setattr(cm, name, np.where(valid_sat, getattr(cm, name), np.nan).astype("float32"))
    field = downscale(sub, cm, dem, z_pix, th)
    obs = obscuration(field, th)

    # nodata where the nearest satellite pixel was invalid
    from scipy.spatial import cKDTree
    LON, LAT = dem.mesh()
    kx = math.cos(math.radians(lat0 + TILE / 2))
    vflat = valid_sat.ravel()
    pts = np.column_stack([sub.lon.ravel() * kx, sub.lat.ravel()])[vflat]
    tree = cKDTree(pts)
    dist, idx = tree.query(np.column_stack([LON.ravel() * kx, LAT.ravel()]))
    nodata = (dist > 2.5 * sub.pixel_km / 111.0).reshape(LON.shape)   # outside the image / off-disk
    night_cells = cm.night.ravel()[vflat][idx].reshape(LON.shape)
    pcol = field.prob_column
    flags = np.zeros(z.shape, "uint8")
    flags |= np.where(night_cells, FLAG_NIGHT, 0).astype("uint8")
    flags |= np.where(field.cirrus > 0.5, FLAG_CIRRUS, 0).astype("uint8")
    flags |= np.where((field.snow > 0.4) & (pcol > 0.3) & (pcol < 0.7), FLAG_SNOWAMB, 0).astype("uint8")
    flags |= np.where(field.base_assumed, FLAG_BASEASSUMED, 0).astype("uint8")
    flags |= np.where(np.nan_to_num(field.cth_unc, nan=0) > 500, FLAG_HIGHUNC, 0).astype("uint8")
    flags |= np.where(nodata, FLAG_NODATA, 0).astype("uint8")
    flags |= np.where((pcol > 0.35) & (pcol < 0.65), FLAG_MARGINAL, 0).astype("uint8")
    cth = np.nan_to_num(field.cth_m, nan=0) / 10.0
    obs = np.where(nodata, 0, obs); pcol = np.where(nodata, 0, pcol); cth = np.where(nodata, 0, cth)
    cls = np.where(nodata, 0, field.cls)
    rec = {
        "obs": np.clip(obs * 255, 0, 255).astype("u1"),
        "pcol": np.clip(pcol * 255, 0, 255).astype("u1"),
        "cls": cls.astype("u1"),
        "flags": flags,
        "cth": np.clip(cth, 0, 65000).astype("<u2"),
    }
    meta = {"profile": prof, "t_ref": round(cm.t_ref_sl, 1), "from_clim": cm.ref_from_climatology,
            "valid": round(float(1 - nodata.mean()), 3), "tests": cm.diag}
    return rec, meta


def pack(rec) -> bytes:
    return gzip.compress(rec["obs"].tobytes() + rec["pcol"].tobytes() + rec["cls"].tobytes()
                         + rec["flags"].tobytes() + rec["cth"].tobytes(), 6)


def unpack(blob: bytes):
    b = gzip.decompress(blob)
    n = N * N
    out = {}
    o = 0
    for name, dtype, size in (("obs", "u1", 1), ("pcol", "u1", 1), ("cls", "u1", 1), ("flags", "u1", 1), ("cth", "<u2", 2)):
        out[name] = np.frombuffer(b[o:o + n * size], dtype=dtype).reshape(N, N)
        o += n * size
    return out


def run_national(scene, sat_key, outdir, th: Thresholds | None = None, terrain=None, tiles=TILES):
    th = th or Thresholds()
    outdir = Path(outdir)
    (outdir / "run").mkdir(parents=True, exist_ok=True)
    terrain = terrain if terrain is not None else build_terrain(tiles=tiles)
    sd = SATELLITES.get(sat_key)
    manifest = {
        "product": "PassCast national 1 km", "image_time_utc": scene.time.isoformat() + "Z",
        "satellite": scene.platform, "sat_key": sat_key,
        "sub_lon": sd.sub_lon if sd else None, "pixel_km": scene.pixel_km,
        "has_ir039": scene.get("ir039") is not None,
        "grid": {"res": RES, "tile": TILE, "n": N},
        "thresholds": {"clear_max": th.score_clear_max_obscured, "partial_max": th.score_partial_max_obscured},
        "tiles": {},
    }
    t0 = time.time()
    for (la0, lo0) in tiles:
        k = key(la0, lo0)
        try:
            rec, meta = process_tile(scene, la0, lo0, terrain[k], th)
            (outdir / "run" / f"{k}.bin.gz").write_bytes(pack(rec))
            manifest["tiles"][k] = meta
            ok = (rec["flags"] & FLAG_NODATA) == 0
            cloudy = float((rec["pcol"][ok] >= 128).mean()) if ok.any() else 0.0
            meta["cloudy"] = round(cloudy, 3)
            print(f"  tile {k}: {meta['profile']:<13} valid {meta['valid']:.0%}  cloudy {cloudy:.0%}  tests {meta['tests']}")
        except Exception as e:
            print(f"  tile {k}: skipped ({type(e).__name__}: {e})")
    manifest["processing_s"] = round(time.time() - t0, 1)
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=1))
    return manifest


# ---------------------------------------------------------------------------
# Reference scoring on 1 km cells (the web page implements the same rules)
# ---------------------------------------------------------------------------
def score_cells(rec, lat, lon, lat0, lon0, radius_m, manifest, now=None):
    i0 = (lat - lat0) / RES - 0.5
    j0 = (lon - lon0) / RES - 0.5
    rr = radius_m / 111000.0 / RES
    rj = rr / math.cos(math.radians(lat))
    I, J = np.mgrid[0:N, 0:N]
    m = ((I - i0) / rr) ** 2 + ((J - j0) / rj) ** 2 <= 1.0
    if m.sum() == 0:
        m[int(round(i0)), int(round(j0))] = True
    flags = rec["flags"][m]
    valid = (flags & FLAG_NODATA) == 0
    if valid.mean() < 0.5:
        return {"score": None, "reason": "no satellite data here"}
    obs = rec["obs"][m][valid] / 255.0
    pcol = rec["pcol"][m][valid] / 255.0
    fl = flags[valid]
    frac = float(obs.mean())
    th = manifest["thresholds"]
    score = 1.0 if frac <= th["clear_max"] else 0.5 if frac <= th["partial_max"] else 0.0
    c = 1.0
    if manifest.get("sub_lon") is not None:
        from .satellite import geo_view_angles
        vza = float(geo_view_angles(np.array([lat]), np.array([lon]), manifest["sub_lon"])[0][0])
        c -= 0.55 if vza > 70 else 0.25 if vza > 60 else 0
    night = (fl & FLAG_NIGHT).astype(bool).mean() > 0.5
    if night:
        c -= 0.1 if manifest.get("has_ir039") else 0.2
    cir = (fl & FLAG_CIRRUS).astype(bool).mean()
    hidden = ((fl & FLAG_CIRRUS).astype(bool) & (fl & FLAG_BASEASSUMED).astype(bool)).mean()
    if hidden > 0.2 and not night:
        c -= 0.3
    if cir > 0.3:
        c -= 0.45 if night else 0.1
    if (fl & FLAG_SNOWAMB).astype(bool).mean() > 0.2:
        c -= 0.2
    cloudy = (pcol >= 0.5).mean()
    if (fl & FLAG_HIGHUNC).astype(bool).any() and 0.1 < cloudy < 0.95:
        c -= 0.3
    if (fl & FLAG_MARGINAL).astype(bool).mean() > 0.3:
        c -= 0.15
    c = max(0.0, min(1.0, c))
    label = "HIGH" if c >= 0.75 else "MEDIUM" if c >= 0.5 else "LOW"
    if score == 1.0 and label == "LOW":
        score = 0.5
    return {"score": score, "obscured": round(frac, 3), "confidence": round(c, 2), "label": label}
