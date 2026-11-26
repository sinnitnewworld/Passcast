"""Satellite ingestion.

Every source is converted to one common SatScene so the science never cares
which satellite the data came from (requirement 4 of the problem statement).

Channel convention (logical names):
  vis064  ~0.64 um reflectance, 0-1           (day only)
  nir16   ~1.6 um reflectance, 0-1            (day only, snow vs cloud)
  ir039   ~3.9 um brightness temperature, K   (night fog)
  ir108   ~10.8 um brightness temperature, K  (core cloud test, cloud-top height)
  ir120   ~12.0 um brightness temperature, K  (thin cirrus)
Missing channels are simply None; tests that need them are skipped and the
confidence score is reduced.
"""
from __future__ import annotations

import datetime as dt
import math
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .config import SATELLITES, SatelliteDef

R_EARTH = 6378.137   # km, equatorial
H_GEO = 35786.0      # km, geostationary altitude


@dataclass
class SatScene:
    lat: np.ndarray
    lon: np.ndarray
    time: dt.datetime
    platform: str
    channels: dict = field(default_factory=dict)
    sza: np.ndarray | None = None   # solar zenith (deg)
    vza: np.ndarray | None = None   # satellite zenith (deg)
    vaz: np.ndarray | None = None   # satellite azimuth (deg, from north, clockwise)
    pixel_km: float = 4.0

    def get(self, name):
        return self.channels.get(name)

    def subset(self, bbox, pad_deg=0.3):
        lo0, la0, lo1, la1 = bbox
        m = ((self.lat >= la0 - pad_deg) & (self.lat <= la1 + pad_deg) &
             (self.lon >= lo0 - pad_deg) & (self.lon <= lo1 + pad_deg))
        rows = np.where(m.any(axis=1))[0]
        cols = np.where(m.any(axis=0))[0]
        if len(rows) == 0 or len(cols) == 0:
            raise ValueError("Scene does not cover the requested area")
        sl = (slice(rows[0], rows[-1] + 1), slice(cols[0], cols[-1] + 1))
        cut = lambda a: None if a is None else a[sl]
        return SatScene(self.lat[sl], self.lon[sl], self.time, self.platform,
                        {k: cut(v) for k, v in self.channels.items()},
                        cut(self.sza), cut(self.vza), cut(self.vaz), self.pixel_km)


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
def geo_view_angles(lat, lon, sub_lon):
    """Satellite zenith and azimuth for a geostationary satellite."""
    phi = np.radians(lat)
    dlam = np.radians(lon - sub_lon)
    cos_g = np.cos(phi) * np.cos(dlam)               # central angle to sub-point
    gamma = np.arccos(np.clip(cos_g, -1, 1))
    r_ratio = R_EARTH / (R_EARTH + H_GEO)
    vza = np.degrees(np.arctan2(np.sin(gamma), cos_g - r_ratio))
    # azimuth from the ground point toward the sub-satellite point
    vaz = np.degrees(np.arctan2(np.sin(-dlam), -np.sin(phi) * np.cos(dlam))) % 360
    return vza, vaz


def solar_zenith(lat, lon, when: dt.datetime):
    doy = when.timetuple().tm_yday
    hour = when.hour + when.minute / 60 + when.second / 3600
    g = 2 * math.pi / 365 * (doy - 1 + (hour - 12) / 24)
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
            - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g))
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                    - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    tst = hour * 60 + eqt + 4 * np.asarray(lon)
    ha = np.radians(tst / 4 - 180)
    la = np.radians(lat)
    cz = np.sin(la) * math.sin(decl) + np.cos(la) * math.cos(decl) * np.cos(ha)
    return np.degrees(np.arccos(np.clip(cz, -1, 1)))


def parallax_correct(lat, lon, height_m, vza, vaz):
    """Move apparent positions back toward the satellite.

    An object at height h is seen displaced *away* from the satellite by
    h*tan(vza). In the Himalaya both cloud tops (up to 12 km) and the terrain
    itself (3-7 km) are high, so at INSAT/Meteosat viewing angles the shift is
    several km -- larger than a mountain pass.
    """
    d = np.asarray(height_m) * np.tan(np.radians(vza))       # metres
    az = np.radians(vaz)
    dlat = d * np.cos(az) / 111000.0
    dlon = d * np.sin(az) / (111000.0 * np.cos(np.radians(lat)))
    return lat + dlat, lon + dlon


# ---------------------------------------------------------------------------
# satpy-based readers
# ---------------------------------------------------------------------------
def _to_reflectance(arr, units):
    a = np.asarray(arr, dtype="float32")
    if units and "%" in str(units):
        a = a / 100.0
    elif np.nanmax(a) > 2.0:      # heuristic: percent without units attr
        a = a / 100.0
    return a


def read_with_satpy(sat_key: str, files: list[str], bbox=None) -> SatScene:
    """Read any supported satellite file set into a SatScene."""
    from satpy import Scene

    sd: SatelliteDef = SATELLITES[sat_key]
    scn = Scene(reader=sd.satpy_reader, filenames=[str(f) for f in files])
    wanted = list(sd.channels.values())
    avail = set(scn.available_dataset_names())
    load = [c for c in wanted if c in avail]
    if sd.channels["ir108"] not in load:
        raise RuntimeError(f"{sd.name}: the 10.8 um channel is required but not in the files")
    scn.load(load)
    # Resample everything onto the IR grid so channels align pixel by pixel
    ir_name = sd.channels["ir108"]
    if sd.kind == "geo":
        if bbox is not None:
            try:
                scn = scn.crop(ll_bbox=tuple(bbox))
            except Exception:
                pass
        scn = scn.resample(scn[ir_name].attrs["area"], resampler="native") \
            if len({scn[c].attrs["area"].shape for c in load}) > 1 else scn
    lons, lats = scn[ir_name].attrs["area"].get_lonlats()
    lats = np.asarray(lats, dtype="float64")
    lons = np.asarray(lons, dtype="float64")

    chans = {}
    for logical, native in sd.channels.items():
        if native not in load:
            chans[logical] = None
            continue
        arr = scn[native]
        if logical in ("vis064", "nir16"):
            chans[logical] = _to_reflectance(arr.values, arr.attrs.get("units"))
        else:
            chans[logical] = np.asarray(arr.values, dtype="float32")

    t = scn[ir_name].attrs.get("start_time") or dt.datetime.utcnow()
    if sd.kind == "geo":
        vza, vaz = geo_view_angles(lats, lons, sd.sub_lon)
    else:
        vza = _try_load_angle(scn, "satellite_zenith_angle", lats)
        vaz = _try_load_angle(scn, "satellite_azimuth_angle", lats)
    sza = solar_zenith(lats, lons, t)
    sc = SatScene(lats, lons, t, sd.name, chans, sza, vza, vaz, sd.nominal_res_km)
    if bbox is not None:
        sc = sc.subset(bbox)
    return sc


def _try_load_angle(scn, name, like):
    try:
        scn.load([name])
        return np.asarray(scn[name].values, dtype="float32")
    except Exception:
        return np.zeros_like(like, dtype="float32")


# ---------------------------------------------------------------------------
# Himawari-9 from NOAA's public AWS bucket (no login)
# ---------------------------------------------------------------------------
HIMA_BUCKET = "https://noaa-himawari9.s3.amazonaws.com"


def fetch_himawari(when: dt.datetime, bands=("B03", "B05", "B07", "B13", "B15"),
                   segments=(2, 3, 4, 5), cache_dir="~/.passcast/himawari") -> list[Path]:
    """Download Himawari full-disk HSD segments for one time slot.

    Segments 2-5 of 10 cover roughly 0-50 N (all of India). Note: India sits at the far
    western edge of Himawari's disk (view angle >60 deg over the western
    Himalaya), so prefer INSAT or Meteosat IODC there; Himawari is most useful
    for the eastern Himalaya (Sikkim, Arunachal).
    """
    import os
    when = when.replace(minute=(when.minute // 10) * 10, second=0, microsecond=0)
    prefix = f"AHI-L1b-FLDK/{when:%Y/%m/%d/%H%M}/"
    listing = urllib.request.urlopen(f"{HIMA_BUCKET}/?list-type=2&prefix={prefix}").read().decode()
    keys = re.findall(r"<Key>([^<]+)</Key>", listing)
    cache = Path(os.path.expanduser(cache_dir)) / f"{when:%Y%m%d_%H%M}"
    cache.mkdir(parents=True, exist_ok=True)
    out = []
    for k in keys:
        m = re.search(r"_(B\d\d)_FLDK_R\d\d_S(\d\d)10", k)
        if not m or m.group(1) not in bands or int(m.group(2)) not in segments:
            continue
        dest = cache / Path(k).name
        if not dest.exists():
            print(f"  downloading {Path(k).name}")
            urllib.request.urlretrieve(f"{HIMA_BUCKET}/{k}", dest)
        out.append(dest)
    if not out:
        raise RuntimeError(f"No Himawari files found for {when:%Y-%m-%d %H:%M} UTC")
    return out


def latest_himawari_slot(now: dt.datetime | None = None, lag_min=20) -> dt.datetime:
    now = now or dt.datetime.utcnow()
    t = now - dt.timedelta(minutes=lag_min)
    return t.replace(minute=(t.minute // 10) * 10, second=0, microsecond=0)


def files_in(folder: str, patterns=("*.h5", "*.nat", "*.DAT*", "*.hdf")) -> list[Path]:
    p = Path(folder)
    out = []
    for pat in patterns:
        out.extend(sorted(p.glob(pat)))
    return out
