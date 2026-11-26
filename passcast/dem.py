"""Digital Elevation Model handling.

DEMGrid is a regular geographic (lat/lon) grid. Sources:
  * Copernicus GLO-30 (30 m, public, no login) streamed from AWS Open Data
  * any local GeoTIFF (CartoDEM from Bhuvan, SRTM, a unit's own survey DEM)
  * a synthetic Himalayan-style terrain for testing
"""
from __future__ import annotations

import math
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np

EARTH_R = 6371000.0
COP_URL = "https://copernicus-dem-30m.s3.amazonaws.com/{n}/{n}.tif"


@dataclass
class DEMGrid:
    lat: np.ndarray   # 1D, ascending
    lon: np.ndarray   # 1D, ascending
    z: np.ndarray     # 2D [lat, lon] metres
    source: str = ""

    @property
    def shape(self):
        return self.z.shape

    def mesh(self):
        return np.meshgrid(self.lon, self.lat)

    def cell_size_m(self):
        dlat = (self.lat[-1] - self.lat[0]) / max(len(self.lat) - 1, 1)
        dlon = (self.lon[-1] - self.lon[0]) / max(len(self.lon) - 1, 1)
        mlat = math.radians(float(self.lat.mean()))
        return (abs(dlat) * math.pi / 180 * EARTH_R,
                abs(dlon) * math.pi / 180 * EARTH_R * math.cos(mlat))

    def sample(self, lat, lon):
        """Bilinear elevation at arbitrary points (outside -> nearest edge)."""
        from scipy.interpolate import RegularGridInterpolator
        f = RegularGridInterpolator((self.lat, self.lon), self.z,
                                    bounds_error=False, fill_value=None)
        la = np.clip(np.ravel(lat), self.lat[0], self.lat[-1])
        lo = np.clip(np.ravel(lon), self.lon[0], self.lon[-1])
        pts = np.column_stack([la, lo])
        return f(pts).reshape(np.shape(lat))

    def window(self, lat0, lon0, half_km):
        dlat = half_km / 111.0
        dlon = half_km / (111.0 * math.cos(math.radians(lat0)))
        i = (self.lat >= lat0 - dlat) & (self.lat <= lat0 + dlat)
        j = (self.lon >= lon0 - dlon) & (self.lon <= lon0 + dlon)
        return DEMGrid(self.lat[i], self.lon[j], self.z[np.ix_(i, j)], self.source)

    def hillshade(self, azimuth=315, altitude=45):
        dy, dx = self.cell_size_m()
        gy, gx = np.gradient(self.z, dy, dx)
        slope = np.pi / 2 - np.arctan(np.hypot(gx, gy))
        aspect = np.arctan2(-gx, gy)
        az, alt = np.radians(azimuth), np.radians(altitude)
        hs = np.sin(alt) * np.sin(slope) + np.cos(alt) * np.cos(slope) * np.cos(az - aspect)
        return np.clip(hs, 0, 1)


def bbox_around(lat0, lon0, half_km):
    dlat = half_km / 111.0
    dlon = half_km / (111.0 * math.cos(math.radians(lat0)))
    return (lon0 - dlon, lat0 - dlat, lon0 + dlon, lat0 + dlat)


# ---------------------------------------------------------------------------
# Copernicus GLO-30
# ---------------------------------------------------------------------------
def _cop_tile_name(lat_floor: int, lon_floor: int) -> str:
    ns = "N" if lat_floor >= 0 else "S"
    ew = "E" if lon_floor >= 0 else "W"
    return (f"Copernicus_DSM_COG_10_{ns}{abs(lat_floor):02d}_00_"
            f"{ew}{abs(lon_floor):03d}_00_DEM")


def fetch_copernicus(bbox, cache_dir="~/.passcast/dem") -> list[Path]:
    """Download the 1x1 degree Copernicus tiles covering bbox (lon0,lat0,lon1,lat1)."""
    cache = Path(os.path.expanduser(cache_dir))
    cache.mkdir(parents=True, exist_ok=True)
    out = []
    for la in range(math.floor(bbox[1]), math.floor(bbox[3]) + 1):
        for lo in range(math.floor(bbox[0]), math.floor(bbox[2]) + 1):
            name = _cop_tile_name(la, lo)
            dest = cache / f"{name}.tif"
            if not dest.exists():
                url = COP_URL.format(n=name)
                print(f"  downloading DEM tile {name} ...")
                tmp = dest.with_suffix(".part")
                urllib.request.urlretrieve(url, tmp)
                tmp.rename(dest)
            out.append(dest)
    return out


def load_geotiffs(paths, bbox, resolution_m=60.0, source="GeoTIFF") -> DEMGrid:
    """Mosaic + crop + resample GeoTIFF DEM(s) to a regular lat/lon grid."""
    import rasterio
    from rasterio.merge import merge
    from rasterio.enums import Resampling

    srcs = [rasterio.open(p) for p in paths]
    try:
        lat_mid = (bbox[1] + bbox[3]) / 2
        res_lat = resolution_m / 111000.0
        res_lon = resolution_m / (111000.0 * math.cos(math.radians(lat_mid)))
        mosaic, transform = merge(srcs, bounds=bbox, res=(res_lon, res_lat),
                                  resampling=Resampling.bilinear, nodata=-32767)
    finally:
        for s in srcs:
            s.close()
    z = mosaic[0].astype("float32")
    z[z <= -1000] = np.nan
    if np.isnan(z).any():
        z = np.where(np.isnan(z), np.nanmean(z), z)
    nrows, ncols = z.shape
    lon = transform.c + transform.a * (np.arange(ncols) + 0.5)
    lat = transform.f + transform.e * (np.arange(nrows) + 0.5)
    if lat[0] > lat[-1]:
        lat, z = lat[::-1], z[::-1]
    return DEMGrid(lat, lon, z, source)


def dem_for_window(lat0, lon0, half_km, resolution_m=60.0, local_paths=None) -> DEMGrid:
    bbox = bbox_around(lat0, lon0, half_km)
    if local_paths:
        return load_geotiffs(local_paths, bbox, resolution_m, "local DEM")
    tiles = fetch_copernicus(bbox)
    return load_geotiffs(tiles, bbox, resolution_m, "Copernicus GLO-30")


# ---------------------------------------------------------------------------
# Synthetic terrain (tests / demo only)
# ---------------------------------------------------------------------------
def synthetic_himalaya(lat0=34.0, lon0=77.0, half_km=40.0, res_m=90.0, seed=7) -> DEMGrid:
    """Ridges and valleys with three engineered saddles (passes).

    Returns the DEM plus, in .passes, a list of (name, lat, lon, z) saddle points.
    """
    rng = np.random.default_rng(seed)
    n = int(2 * half_km * 1000 / res_m)
    dlat = half_km / 111.0
    dlon = half_km / (111.0 * math.cos(math.radians(lat0)))
    lat = np.linspace(lat0 - dlat, lat0 + dlat, n)
    lon = np.linspace(lon0 - dlon, lon0 + dlon, n)
    X, Y = np.meshgrid(np.linspace(-half_km, half_km, n), np.linspace(-half_km, half_km, n))

    # Valley floor ~2900 m, three E-W ridge lines up to ~6000 m
    z = 2900 + 300 * np.sin(X / 23.0)
    ridges = [(-22, 2700, 6.0), (0, 2500, 5.5), (22, 2900, 6.5)]  # (y_km, height, width)
    for y0, h, w in ridges:
        z += h * np.exp(-((Y - y0) / w) ** 2) * (1 + 0.25 * np.sin(X / 7.0))
    # Saddles: depressions in each ridge where a road would cross
    saddles = [("Synthetic Pass A", -15.0, -22.0, 1500),
               ("Synthetic Pass B", 12.0, 0.0, 1200),
               ("Synthetic Pass C", -2.0, 22.0, 1500)]
    for _, x0, y0, depth in saddles:
        z -= depth * np.exp(-((X - x0) / 4.0) ** 2) * np.exp(-((Y - y0) / 6.0) ** 2)
    # Fractal roughness
    from scipy.ndimage import gaussian_filter
    for scale, amp in [(8, 320), (3, 160), (1, 60)]:
        noise = gaussian_filter(rng.standard_normal(z.shape), scale * 1000 / res_m)
        z += amp * noise / noise.std()
    dem = DEMGrid(lat, lon, z.astype("float32"), "synthetic")
    passes = []
    for name, x0, y0, _ in saddles:
        la = lat0 + y0 / 111.0
        lo = lon0 + x0 / (111.0 * math.cos(math.radians(lat0)))
        passes.append((name, la, lo, float(dem.sample(np.array([la]), np.array([lo]))[0])))
    dem.passes = passes
    return dem
