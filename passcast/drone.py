"""Drone flight planner: "I want to fly here at this time -- will it be OK?"

The satellite shows what is happening now; planning a flight needs a forecast.
This module pulls a free hourly forecast (Open-Meteo, no key) and the
geomagnetic Kp forecast (NOAA SWPC), evaluates every hour against the limits of
a specific drone, and returns GO / CAUTION / NO-GO with reasons plus the best
windows in the next 48-72 hours.

Checks (all thresholds are configurable):
  * wind at flight height vs the drone's rated wind resistance, and gusts
  * rain (most consumer drones are not waterproof) and chance of rain
  * visibility (keeping the drone in visual line of sight)
  * cloud base vs planned height (estimated from temperature - dew point)
  * thunderstorm risk (weather code, CAPE)
  * temperature for battery performance
  * Kp index (geomagnetic storms degrade GPS)
  * daylight

Airspace permission (Digital Sky green/yellow/red zones) is NOT checked here;
the pilot must verify it on the Digital Sky platform before every flight.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, asdict

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
KP_URL = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index-forecast.json"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

HOURLY_VARS = [
    "temperature_2m", "dew_point_2m", "relative_humidity_2m", "precipitation_probability",
    "precipitation", "weather_code", "cloud_cover", "cloud_cover_low", "visibility",
    "wind_speed_10m", "wind_speed_80m", "wind_speed_120m", "wind_direction_120m",
    "wind_gusts_10m", "cape", "is_day",
]


# ---------------------------------------------------------------------------
# Drone profiles. Wind figures are the manufacturers' published "max wind
# speed resistance"; check your own manual and override with --max-wind.
# ---------------------------------------------------------------------------
@dataclass
class DroneProfile:
    key: str
    name: str
    max_wind_ms: float
    min_temp_c: float = -10.0
    max_temp_c: float = 40.0
    rain_ok: bool = False


DRONES = {
    "mini": DroneProfile("mini", "DJI Mini series (sub-250 g)", 10.7),
    "avata2": DroneProfile("avata2", "DJI Avata 2", 10.7),
    "air3": DroneProfile("air3", "DJI Air 3", 12.0),
    "mavic3": DroneProfile("mavic3", "DJI Mavic 3 series", 12.0),
    "atom2": DroneProfile("atom2", "Potensic Atom 2", 10.7),
    "fpv_light": DroneProfile("fpv_light", "Light FPV / cinewhoop", 7.0),
    "survey": DroneProfile("survey", "Enterprise survey drone (IP-rated)", 12.0, -20, 50, True),
}


@dataclass
class Limits:
    wind_go_frac: float = 0.60        # <= 60 % of rated wind: GO
    wind_nogo_frac: float = 0.85      # > 85 %: NO-GO
    rain_mm_nogo: float = 0.2
    rain_prob_caution: float = 40.0
    vis_nogo_m: float = 1500.0
    vis_caution_m: float = 5000.0
    cloud_base_margin_m: float = 50.0     # base must clear planned height by this
    cloud_base_caution_m: float = 200.0
    cape_caution: float = 1000.0
    kp_caution: float = 5.0
    kp_nogo: float = 7.0
    temp_caution_low: float = 5.0
    temp_caution_high: float = 35.0


@dataclass
class HourVerdict:
    time_ist: str
    verdict: str                 # GO / CAUTION / NO-GO
    reasons: list = field(default_factory=list)
    wind_ms: float = 0.0
    gust_ms: float = 0.0
    rain_mm: float = 0.0
    rain_prob: float = 0.0
    visibility_m: float | None = None
    cloud_base_agl_m: float | None = None
    low_cloud_pct: float = 0.0
    temp_c: float = 0.0
    kp: float | None = None
    is_day: bool = True


RANK = {"GO": 0, "CAUTION": 1, "NO-GO": 2}
THUNDER = {95, 96, 99}


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------
def fetch_forecast(lat, lon, days=3) -> dict:
    q = urllib.parse.urlencode({
        "latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}",
        "hourly": ",".join(HOURLY_VARS), "wind_speed_unit": "ms",
        "timezone": "Asia/Kolkata", "forecast_days": days,
    })
    with urllib.request.urlopen(f"{OPEN_METEO}?{q}", timeout=30) as r:
        return json.loads(r.read().decode())


def parse_kp(raw) -> dict:
    """Return {datetime_utc: kp}. Handles SWPC's old header-row format and the
    2026 object format."""
    out = {}
    if not raw:
        return out
    if isinstance(raw[0], list):
        head = [h.lower() for h in raw[0]]
        rows = [dict(zip(head, r)) for r in raw[1:]]
    else:
        rows = [{k.lower(): v for k, v in r.items()} for r in raw]
    for r in rows:
        t = r.get("time_tag")
        kp_key = next((k for k in r if k == "kp" or k.startswith("kp")), None)
        if not t or kp_key is None or r[kp_key] in (None, ""):
            continue
        try:
            when = dt.datetime.fromisoformat(str(t).replace("Z", "")[:19])
            out[when] = float(r[kp_key])
        except ValueError:
            continue
    return out


def fetch_kp() -> dict:
    try:
        with urllib.request.urlopen(KP_URL, timeout=20) as r:
            return parse_kp(json.loads(r.read().decode()))
    except Exception:
        return {}


def kp_at(kp: dict, when_utc: dt.datetime):
    if not kp:
        return None
    best = min(kp, key=lambda t: abs((t - when_utc).total_seconds()))
    return kp[best] if abs((best - when_utc).total_seconds()) <= 3 * 3600 else None


# ---------------------------------------------------------------------------
# Physics helpers
# ---------------------------------------------------------------------------
def wind_at_height(w10, w80, w120, h):
    """Interpolate wind speed to the planned flight height (m AGL)."""
    pts = [(10, w10), (80, w80), (120, w120)]
    pts = [(z, w) for z, w in pts if w is not None]
    if not pts:
        return None
    if h <= pts[0][0]:
        return pts[0][1]
    for (z0, w0), (z1, w1) in zip(pts, pts[1:]):
        if h <= z1:
            return w0 + (w1 - w0) * (h - z0) / (z1 - z0)
    return pts[-1][1]


def cloud_base_agl(temp_c, dew_c):
    """Lifting condensation level ~125 m per degree of dew-point depression."""
    if temp_c is None or dew_c is None:
        return None
    return max(0.0, 125.0 * (temp_c - dew_c))


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
def evaluate_hour(h: dict, drone: DroneProfile, height_m: float, lim: Limits, kp=None) -> HourVerdict:
    reasons, level = [], "GO"

    def bump(lvl, why):
        nonlocal level
        reasons.append(why)
        if RANK[lvl] > RANK[level]:
            level = lvl

    w = wind_at_height(h.get("wind_speed_10m"), h.get("wind_speed_80m"), h.get("wind_speed_120m"), height_m) or 0.0
    w10 = h.get("wind_speed_10m") or 0.0
    g10 = h.get("wind_gusts_10m") or w10
    # gusts aloft: scale 10 m gust by the shear ratio, never below the 10 m gust
    gust = max(g10, g10 * (w / w10)) if w10 > 0.5 else max(g10, w)
    L = drone.max_wind_ms
    if w > lim.wind_nogo_frac * L or gust > L:
        bump("NO-GO", f"wind {w:.1f} m/s, gusts {gust:.1f} m/s at {height_m:.0f} m vs {L:.1f} m/s rating")
    elif w > lim.wind_go_frac * L or gust > lim.wind_nogo_frac * L:
        bump("CAUTION", f"wind {w:.1f} m/s, gusts {gust:.1f} m/s: battery drain and drift; keep the drone upwind")

    rain = h.get("precipitation") or 0.0
    rp = h.get("precipitation_probability") or 0.0
    if rain >= lim.rain_mm_nogo and not drone.rain_ok:
        bump("NO-GO", f"rain forecast ({rain:.1f} mm)")
    elif rp >= lim.rain_prob_caution:
        bump("CAUTION", f"{rp:.0f}% chance of rain")

    vis = h.get("visibility")
    if vis is not None:
        if vis < lim.vis_nogo_m:
            bump("NO-GO", f"visibility {vis/1000:.1f} km: fog/haze, line of sight not possible")
        elif vis < lim.vis_caution_m:
            bump("CAUTION", f"visibility {vis/1000:.1f} km: haze; stay close")

    base = cloud_base_agl(h.get("temperature_2m"), h.get("dew_point_2m"))
    low = h.get("cloud_cover_low") or 0.0
    if base is not None and low >= 50:
        if base < height_m + lim.cloud_base_margin_m:
            bump("NO-GO", f"low cloud (~{base:.0f} m above ground) below your planned {height_m:.0f} m")
        elif base < height_m + lim.cloud_base_caution_m:
            bump("CAUTION", f"low cloud base ~{base:.0f} m above ground, close to planned height")

    code = h.get("weather_code")
    cape = h.get("cape") or 0.0
    if code in THUNDER:
        bump("NO-GO", "thunderstorm forecast")
    elif cape >= lim.cape_caution:
        bump("CAUTION", f"unstable air (CAPE {cape:.0f} J/kg): storms can build quickly")

    t = h.get("temperature_2m")
    if t is not None:
        if t < drone.min_temp_c or t > drone.max_temp_c:
            bump("NO-GO", f"temperature {t:.0f} °C outside the drone's operating range")
        elif t < lim.temp_caution_low:
            bump("CAUTION", f"cold ({t:.0f} °C): warm batteries before take-off, expect shorter flights")
        elif t > lim.temp_caution_high:
            bump("CAUTION", f"hot ({t:.0f} °C): batteries and motors run hot; shorter flights")

    if kp is not None:
        if kp >= lim.kp_nogo:
            bump("NO-GO", f"severe geomagnetic storm (Kp {kp:.0f}): GPS unreliable")
        elif kp >= lim.kp_caution:
            bump("CAUTION", f"geomagnetic storm (Kp {kp:.0f}): GPS may drift; be ready to fly manually")

    day = bool(h.get("is_day", 1))
    if not day:
        bump("CAUTION", "dark: check the rules and permissions that apply to night flying, and use lights")

    if not reasons:
        reasons.append("calm, dry, good visibility")
    return HourVerdict(h["time"], level, reasons, round(w, 1), round(gust, 1), rain, rp,
                       vis, None if base is None else round(base), low, t, kp, day)


def hours_from(fc: dict):
    hr = fc["hourly"]
    for i, t in enumerate(hr["time"]):
        yield {k: (v[i] if isinstance(v, list) else v) for k, v in hr.items()} | {"time": t}


def plan(fc: dict, drone: DroneProfile, height_m=120.0, lim: Limits | None = None, kp: dict | None = None):
    lim = lim or Limits()
    out = []
    for h in hours_from(fc):
        t_ist = dt.datetime.fromisoformat(h["time"])
        t_utc = t_ist - dt.timedelta(hours=5, minutes=30)
        out.append(evaluate_hour(h, drone, height_m, lim, kp_at(kp or {}, t_utc)))
    return out


def best_windows(verdicts, min_hours=1, daylight_only=True, top=3):
    """Contiguous GO stretches, longest and soonest first."""
    wins, cur = [], []
    for v in verdicts:
        ok = v.verdict == "GO" and (v.is_day or not daylight_only)
        if ok:
            cur.append(v)
        elif cur:
            wins.append(cur); cur = []
    if cur:
        wins.append(cur)
    wins = [w for w in wins if len(w) >= min_hours]
    wins.sort(key=lambda w: (-len(w), w[0].time_ist))
    return [{"start": w[0].time_ist, "end": w[-1].time_ist, "hours": len(w),
             "max_wind_ms": max(x.wind_ms for x in w)} for w in wins[:top]]


def at_time(verdicts, when: dt.datetime):
    target = when.strftime("%Y-%m-%dT%H:00")
    for v in verdicts:
        if v.time_ist == target:
            return v
    return None


# ---------------------------------------------------------------------------
# Synthetic forecast (offline demo and tests)
# ---------------------------------------------------------------------------
def synthetic_forecast(start: dt.datetime, hours=72) -> dict:
    """A winter-on-the-Deccan scenario: dawn fog, breezy afternoons, and a
    thunderstorm on day 2 afternoon."""
    keys = HOURLY_VARS
    hr = {k: [] for k in keys}
    hr["time"] = []
    for i in range(hours):
        t = start + dt.timedelta(hours=i)
        hh, day = t.hour, i // 24
        is_day = 1 if 7 <= hh <= 17 else 0
        temp = 16 + 11 * max(0.0, math.sin(math.pi * (hh - 7) / 12)) if 7 <= hh <= 19 else 16 - 0.3 * ((hh - 19) % 24 if hh >= 19 else hh + 5) / 2
        fog = 3 <= hh <= 8 and day != 1
        dew = temp - (0.4 if fog else 6 + 4 * is_day)
        vis = 700.0 if fog and hh <= 7 else (3500.0 if fog else 18000.0)
        w10 = 2.0 + 4.5 * max(0.0, math.sin(math.pi * (hh - 10) / 10)) if 10 <= hh <= 20 else 1.5
        storm = day == 1 and 14 <= hh <= 17
        rain = 4.0 if storm else 0.0
        hr["time"].append(t.strftime("%Y-%m-%dT%H:00"))
        vals = {
            "temperature_2m": round(temp, 1), "dew_point_2m": round(dew, 1),
            "relative_humidity_2m": 98 if fog else 60, "precipitation_probability": 80 if storm else (45 if day == 1 and 12 <= hh <= 13 else 5),
            "precipitation": rain, "weather_code": 95 if storm else (45 if fog else 1),
            "cloud_cover": 95 if (fog or storm) else 20, "cloud_cover_low": 95 if fog else (60 if storm else 5),
            "visibility": vis, "wind_speed_10m": round(w10 * (2.2 if storm else 1), 1),
            "wind_speed_80m": round(w10 * 1.35 * (2.2 if storm else 1), 1),
            "wind_speed_120m": round(w10 * 1.45 * (2.2 if storm else 1), 1),
            "wind_direction_120m": 80, "wind_gusts_10m": round(w10 * (1.6 if not storm else 3.0), 1),
            "cape": 1800 if day == 1 and 11 <= hh <= 17 else 200, "is_day": is_day,
        }
        for k in keys:
            hr[k].append(vals[k])
    return {"hourly": hr, "timezone": "Asia/Kolkata"}
