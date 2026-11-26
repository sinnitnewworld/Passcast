"""Configuration: pass catalogue, satellite definitions and decision thresholds.

Everything a met officer may want to tune lives here (or in a JSON override
file passed with --config), so the science modules stay free of magic numbers.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path


# ---------------------------------------------------------------------------
# Pass catalogue
# ---------------------------------------------------------------------------
@dataclass
class Pass:
    """Any place that must be flyable: a pass, helipad, airstrip, valley or drone spot."""
    name: str
    lat: float
    lon: float
    elevation_m: float
    region: str = ""
    kind: str = "pass"        # pass | helipad | airstrip | valley | drone_spot | route
    # Corridor radius around the pass point that must be flyable (m)
    corridor_radius_m: float = 3000.0
    # Optional approach route as [(lat, lon), ...]; buffered by approach_buffer_m
    approach: list = field(default_factory=list)
    approach_buffer_m: float = 1500.0


Site = Pass   # preferred name now that the tool covers all of India


# Publicly known locations used for demonstration. Coordinates are
# approximate (±1 km) and MUST be checked against the unit's own survey data
# before operational use. Operational users replace this list via --passes.
DEFAULT_PASSES = [
    Pass("Zoji La", 34.280, 75.470, 3528, "Ladakh"),
    Pass("Khardung La", 34.278, 77.604, 5359, "Ladakh"),
    Pass("Chang La", 34.050, 77.930, 5360, "Ladakh"),
    Pass("Tanglang La", 33.507, 77.770, 5328, "Ladakh"),
    Pass("Baralacha La", 32.760, 77.420, 4890, "Himachal Pradesh"),
    Pass("Rohtang Pass", 32.370, 77.250, 3978, "Himachal Pradesh"),
    Pass("Kunzum Pass", 32.397, 77.637, 4551, "Himachal Pradesh"),
    Pass("Nathu La", 27.386, 88.831, 4310, "Sikkim"),
    Pass("Sela Pass", 27.503, 92.103, 4170, "Arunachal Pradesh"),
    # Beyond the Himalaya (approximate public locations; verify before use)
    Pass("Kedarnath Helipad", 30.735, 79.067, 3583, "Uttarakhand", "helipad"),
    Pass("Shillong Plateau", 25.570, 91.880, 1500, "Meghalaya", "valley"),
    Pass("Agumbe Ghat", 13.505, 75.090, 650, "Karnataka", "valley", 4000.0),
    Pass("Nandi Hills", 13.370, 77.683, 1478, "Karnataka", "drone_spot", 2000.0),
    Pass("Lucknow Airport", 26.761, 80.889, 123, "Uttar Pradesh", "airstrip", 5000.0),
    Pass("Port Blair Airport", 11.641, 92.730, 5, "Andaman & Nicobar", "airstrip", 5000.0),
]


def load_passes(path: str | None) -> list[Pass]:
    if not path:
        return list(DEFAULT_PASSES)
    data = json.loads(Path(path).read_text())
    return [Pass(**p) for p in data]


# ---------------------------------------------------------------------------
# Satellites
# ---------------------------------------------------------------------------
@dataclass
class SatelliteDef:
    key: str
    name: str
    kind: str                 # "geo" or "polar"
    satpy_reader: str
    sub_lon: float | None     # geostationary sub-satellite longitude
    channels: dict            # logical name -> reader channel name
    nominal_res_km: float
    refresh_min: int


SATELLITES = {
    "insat3dr": SatelliteDef(
        "insat3dr", "INSAT-3DR Imager", "geo", "insat3d_img_l1b_h5", 74.0,
        {"vis064": "VIS", "nir16": "SWIR", "ir039": "MIR", "ir108": "TIR1", "ir120": "TIR2"},
        4.0, 15),
    # INSAT-3DS uses the same imager design; set sub_lon to the slot in the file
    # metadata if it differs in your data.
    "insat3ds": SatelliteDef(
        "insat3ds", "INSAT-3DS Imager", "geo", "insat3d_img_l1b_h5", 82.0,
        {"vis064": "VIS", "nir16": "SWIR", "ir039": "MIR", "ir108": "TIR1", "ir120": "TIR2"},
        4.0, 15),
    "meteosat_iodc": SatelliteDef(
        "meteosat_iodc", "Meteosat IODC (SEVIRI)", "geo", "seviri_l1b_native", 45.5,
        {"vis064": "VIS006", "nir16": "IR_016", "ir039": "IR_039", "ir108": "IR_108", "ir120": "IR_120"},
        3.0, 15),
    "himawari": SatelliteDef(
        "himawari", "Himawari-9 AHI", "geo", "ahi_hsd", 140.7,
        {"vis064": "B03", "nir16": "B05", "ir039": "B07", "ir108": "B13", "ir120": "B15"},
        2.0, 10),
    "modis": SatelliteDef(
        "modis", "MODIS (Terra/Aqua)", "polar", "modis_l1b", None,
        {"vis064": "1", "nir16": "6", "ir039": "20", "ir108": "31", "ir120": "32"},
        1.0, 0),
}


# ---------------------------------------------------------------------------
# Science / decision thresholds
# ---------------------------------------------------------------------------
@dataclass
class Thresholds:
    lapse_rate_k_per_km: float = 6.5
    # IR cloud test: deficit (K) of sea-level-equivalent BT vs clear reference
    ir_deficit_low: float = 6.0
    ir_deficit_high: float = 15.0
    clear_ref_percentile: float = 90.0
    # Snow discrimination (day)
    ndsi_snow_low: float = 0.30
    ndsi_snow_high: float = 0.50
    vis_cloud_low: float = 0.25
    vis_cloud_high: float = 0.45
    # Night fog / low stratus: BT11 - BT3.9 (K)
    fog_btd_low: float = 1.5
    fog_btd_high: float = 4.0
    night_sza_deg: float = 85.0
    # Terrain conditioning
    cloud_top_sigma_m: float = 150.0
    low_cloud_max_depth_m: float = 1200.0     # top - local terrain, to call it "low cloud"
    default_low_cloud_thickness_m: float = 800.0
    default_mid_cloud_thickness_m: float = 2000.0
    high_cloud_base_m: float = 6000.0
    min_ceiling_above_terrain_m: float = 300.0  # ~1000 ft
    # Pass scoring
    score_clear_max_obscured: float = 0.20
    score_partial_max_obscured: float = 0.60
    # Processing
    window_half_width_km: float = 25.0
    dem_resolution_m: float = 60.0
    parallax_correction: bool = True
    # Regional behaviour (set automatically by regions.py)
    snow_test: bool = True
    region_profile: str = "himalaya"
    clim_offset_k: float = 0.0

    def to_dict(self):
        return asdict(self)


def load_thresholds(path: str | None) -> Thresholds:  # noqa: E302
    t = Thresholds()
    if path:
        for k, v in json.loads(Path(path).read_text()).items():
            if hasattr(t, k):
                setattr(t, k, v)
    return t


# Climatological sea-level-equivalent clear-sky skin temperature (K) for the
# Himalayan belt by month. Only used when the scene itself has too few clear
# pixels to estimate the reference, and the result is flagged low confidence.
CLIM_SEA_LEVEL_SKIN_K = {
    1: 291, 2: 293, 3: 298, 4: 303, 5: 307, 6: 306,
    7: 302, 8: 301, 9: 300, 10: 298, 11: 294, 12: 291,
}
