"""All-India 1 km product: lets the web page score ANY location instantly.

Each run cuts India into 5 x 5 degree tiles on a 0.01 degree (~1 km) grid and,
for every tile, runs the same science as the 30 m site product (terrain-adjusted
cloud mask, parallax, terrain conditioning, layer classes). It writes compact
gzip binary tiles that the browser fetches on demand:

  terrain/<key>.bin.gz   static: mean u16 | max u16 elevation (m)
  run/<key>.bin.gz       per run: obs u8 | pcol u8 | cls u8 | flags u8 | cth u16 | base u16 (10 m units; base 0 = to the ground)
  manifest.json          image time, satellite, grid, per-tile metadata

The browser then averages the cells inside the chosen corridor and applies the
same score / confidence rules as scoring.py (see score_cells below, which the
page mirrors).
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import gzip
import shutil
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
    """Read one 1x1 degree GLO-90 tile at ~1 km. Returns (mean, max) per cell:
    the mean drives the cloud science, the max drives terrain clearance (a 1 km
    average would hide peaks). Ocean tiles do not exist -> zeros."""
    import rasterio
    from rasterio.enums import Resampling
    url = "/vsicurl/" + GLO90.format(n=_glo90_name(la, lo))
    try:
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif"):
            with rasterio.open(url) as src:
                full = src.read(1).astype("float32")             # 1200x1200 at 3 arc-sec
        full = np.where(full < -500, 0, full)[::-1]              # north-up -> south-up
        f = full.shape[0] // cells
        blk = full[:f * cells, :f * cells].reshape(cells, f, cells, -1)
        return np.clip(blk.mean(axis=(1, 3)), 0, 9000), np.clip(blk.max(axis=(1, 3)), 0, 9000)
    except Exception:
        z = np.zeros((cells, cells), "float32")
        return z, z


TERRAIN_MAX = {}     # tile key -> per-cell maximum elevation (for terrain clearance)


def build_terrain(cache_dir="~/.passcast/terrain1km_v2", tiles=TILES, workers=16) -> dict:
    """Returns {key: mean elevation}; per-cell maxima go to TERRAIN_MAX."""
    cache = Path(os.path.expanduser(cache_dir))
    cache.mkdir(parents=True, exist_ok=True)
    out = {}
    cells = int(round(1 / RES))
    for (la0, lo0) in tiles:
        k = key(la0, lo0)
        f = cache / f"{k}.npy"
        if f.exists():
            both = np.load(f)
            out[k], TERRAIN_MAX[k] = both[0], both[1]
            continue
        z = np.zeros((N, N), "float32"); zx = np.zeros((N, N), "float32")
        jobs = {}
        with cf.ThreadPoolExecutor(workers) as ex:
            for i in range(int(TILE)):
                for j in range(int(TILE)):
                    jobs[ex.submit(_read_degree, la0 + i, lo0 + j, cells)] = (i, j)
            for fut in cf.as_completed(jobs):
                i, j = jobs[fut]
                zm, zmax = fut.result()
                z[i * cells:(i + 1) * cells, j * cells:(j + 1) * cells] = zm
                zx[i * cells:(i + 1) * cells, j * cells:(j + 1) * cells] = zmax
        np.save(f, np.stack([z, zx]))
        out[k], TERRAIN_MAX[k] = z, zx
        print(f"  terrain {k}: max {zx.max():.0f} m")
    return out


def write_terrain(terrain: dict, outdir):
    """terrain/<key>.bin.gz = uint16 mean elevation | uint16 max elevation."""
    d = Path(outdir) / "terrain"
    d.mkdir(parents=True, exist_ok=True)
    for k, z in terrain.items():
        zx = TERRAIN_MAX.get(k, z)
        blob = np.clip(z, 0, 9000).astype("<u2").tobytes() + np.clip(zx, 0, 9000).astype("<u2").tobytes()
        (d / f"{k}.bin.gz").write_bytes(gzip.compress(blob, 6))


# ---------------------------------------------------------------------------
# 90 m terrain for low-level (valley) planning: mountain belt only
# terrain90/NddEddd.bin.gz = int16, 1200x1200, south-up, each row stored as
# first value then differences (so gzip compresses it well); index.json lists keys.
# ---------------------------------------------------------------------------
MOUNTAIN_MIN_M = 2000
N90 = 1200


def mountain_degrees(lat_min=26, min_m=MOUNTAIN_MIN_M):
    """1x1 degree cells (south-west corners) with terrain >= min_m, from the 1 km maxima."""
    cells = int(round(1 / RES)); out = []
    for (la0, lo0) in TILES:
        zx = TERRAIN_MAX.get(key(la0, lo0))
        if zx is None:
            continue
        for i in range(int(TILE)):
            for j in range(int(TILE)):
                la, lo = la0 + i, lo0 + j
                if la >= lat_min and zx[i * cells:(i + 1) * cells, j * cells:(j + 1) * cells].max() >= min_m:
                    out.append((la, lo))
    return out


def encode90(z):
    z = np.clip(np.asarray(z), -500, 9000).astype("<i2")
    d = np.diff(z.astype("int32"), axis=1, prepend=0).astype("<i2")
    return gzip.compress(d.tobytes(), 6)


def decode90(blob, n=N90):
    d = np.frombuffer(gzip.decompress(blob), "<i2").reshape(n, n).astype("int32")
    return np.cumsum(d, axis=1).astype("int16")


def _read_full90(la, lo):
    import rasterio
    url = "/vsicurl/" + GLO90.format(n=_glo90_name(la, lo))
    try:
        with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif"):
            with rasterio.open(url) as src:
                full = src.read(1).astype("float32")
        if full.shape != (N90, N90):
            return None
        return np.where(full < -500, 0, full)[::-1]              # north-up -> south-up
    except Exception:
        return None


def build_terrain90(outdir, cache_dir="~/.passcast/terrain90_v1", workers=16, degrees=None):
    cache = Path(os.path.expanduser(cache_dir)); cache.mkdir(parents=True, exist_ok=True)
    d = Path(outdir) / "terrain90"; d.mkdir(parents=True, exist_ok=True)
    degrees = degrees if degrees is not None else mountain_degrees()
    todo = [(la, lo) for la, lo in degrees if not (cache / f"{key(la, lo)}.bin.gz").exists()]
    with cf.ThreadPoolExecutor(workers) as ex:
        for (la, lo), z in zip(todo, ex.map(lambda t: _read_full90(*t), todo)):
            if z is not None:
                (cache / f"{key(la, lo)}.bin.gz").write_bytes(encode90(z))
    keys = []
    for la, lo in degrees:
        f = cache / f"{key(la, lo)}.bin.gz"
        if f.exists():
            shutil.copy(f, d / f.name); keys.append(key(la, lo))
    (d / "index.json").write_text(json.dumps({"n": N90, "keys": keys, "source": "Copernicus GLO-90 DSM"}))
    print(f"  90 m terrain: {len(keys)} mountain degree cells")
    return keys


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


def process_tile(scene, lat0, lon0, z, base_th: Thresholds, scene_sub_lon=None):
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
    if scene_sub_lon is not None:      # too close to the satellite's horizon to be usable
        from .satellite import geo_view_angles
        nodata |= geo_view_angles(LAT, LON, scene_sub_lon)[0] > 80.0
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
    base = np.nan_to_num(field.base_m, nan=0) / 10.0
    base = np.where(field.base_assumed, 0, np.maximum(base, 1))       # 0 = reaches the ground
    base = np.where(np.isfinite(field.cth_m), base, 0)
    obs = np.where(nodata, 0, obs); pcol = np.where(nodata, 0, pcol); cth = np.where(nodata, 0, cth)
    base = np.where(nodata, 0, base)
    cls = np.where(nodata, 0, field.cls)
    rec = {
        "obs": np.clip(obs * 255, 0, 255).astype("u1"),
        "pcol": np.clip(pcol * 255, 0, 255).astype("u1"),
        "cls": cls.astype("u1"),
        "flags": flags,
        "cth": np.clip(cth, 0, 65000).astype("<u2"),
        "base": np.clip(base, 0, 65000).astype("<u2"),
    }
    meta = {"profile": prof, "t_ref": round(cm.t_ref_sl, 1), "from_clim": cm.ref_from_climatology,
            "valid": round(float(1 - nodata.mean()), 3), "tests": cm.diag}
    return rec, meta


def pack(rec) -> bytes:
    return gzip.compress(rec["obs"].tobytes() + rec["pcol"].tobytes() + rec["cls"].tobytes()
                         + rec["flags"].tobytes() + rec["cth"].tobytes() + rec["base"].tobytes(), 6)


def unpack(blob: bytes):
    b = gzip.decompress(blob)
    n = N * N
    out = {}
    o = 0
    for name, dtype, size in (("obs", "u1", 1), ("pcol", "u1", 1), ("cls", "u1", 1), ("flags", "u1", 1), ("cth", "<u2", 2), ("base", "<u2", 2)):
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
            rec, meta = process_tile(scene, la0, lo0, terrain[k], th, sd.sub_lon if sd else None)
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


def score_route(recs: dict, stops, half_width_m, manifest):
    """Route rating (the web page implements the same rules).

    stops: [(lat, lon), ...]; recs: {tile_key: unpacked tile}.
    Samples every ~1 km along straight legs and scores each sample's corridor.
    Route = 0 if any blocked stretch (>partial_max) is >= 2 km long,
            0.5 if any partially obscured or short blocked stretch, else 1.
    """
    def hav(a, b):
        la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
        x = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
        return 2 * 6371 * math.asin(math.sqrt(x))
    samples, dist = [], 0.0
    for l in range(len(stops) - 1):
        a, b = stops[l], stops[l + 1]
        d = hav(a, b); n = max(1, math.ceil(d))
        for k in range(0 if l == 0 else 1, n + 1):
            f = k / n
            samples.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, dist + d * f))
        dist += d
    th = manifest["thresholds"]
    st = []
    for la, lo, km in samples:
        k = key(int(la // 5 * 5), int(lo // 5 * 5))
        if k not in recs:
            st.append(("nd", km)); continue
        r = score_cells(recs[k], la, lo, int(la // 5 * 5), int(lo // 5 * 5), half_width_m, manifest)
        if r.get("score") is None:
            st.append(("nd", km)); continue
        o = r["obscured"]
        st.append((0.0 if o > th["partial_max"] else 0.5 if o > th["clear_max"] else 1.0, km))
    runs = []
    for s, km in st:
        if runs and runs[-1][0] == s:
            runs[-1][2] = km
        else:
            runs.append([s, km, km])
    blocked = [r for r in runs if r[0] == 0.0 and max(r[2] - r[1], 1) >= 2]
    partial = [r for r in runs if r[0] == 0.5 or (r[0] == 0.0 and max(r[2] - r[1], 1) < 2)]
    score = 0.0 if blocked else 0.5 if partial else 1.0
    nd = sum(1 for s, _ in st if s == "nd") / max(len(st), 1)
    if nd > 0.2 and score == 1.0:
        score = 0.5
    return {"score": score, "km": round(dist, 1), "blocked": len(blocked), "partial": len(partial), "no_data": round(nd, 3)}
