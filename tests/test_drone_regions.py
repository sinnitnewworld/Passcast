import datetime as dt

from passcast.drone import (DRONES, Limits, plan, best_windows, at_time, evaluate_hour,
                            synthetic_forecast, parse_kp, wind_at_height, cloud_base_agl)
from passcast.regions import detect_region, thresholds_for
from passcast.config import Thresholds, Pass

START = dt.datetime(2026, 11, 27, 0, 0)


def _h(**kw):
    base = dict(time="2026-11-27T10:00", temperature_2m=22, dew_point_2m=10, precipitation=0,
                precipitation_probability=0, weather_code=1, cloud_cover_low=0, visibility=20000,
                wind_speed_10m=2, wind_speed_80m=3, wind_speed_120m=3.5, wind_gusts_10m=3,
                cape=100, is_day=1)
    base.update(kw)
    return base


def test_calm_day_is_go():
    v = evaluate_hour(_h(), DRONES["mini"], 120, Limits())
    assert v.verdict == "GO"


def test_wind_limits_scale_with_drone():
    h = _h(wind_speed_10m=5, wind_speed_80m=7, wind_speed_120m=7.5, wind_gusts_10m=7)
    assert evaluate_hour(h, DRONES["fpv_light"], 120, Limits()).verdict == "NO-GO"
    assert evaluate_hour(h, DRONES["mavic3"], 120, Limits()).verdict in ("GO", "CAUTION")


def test_fog_rain_storm_kp():
    L, d = Limits(), DRONES["mini"]
    assert evaluate_hour(_h(visibility=600), d, 120, L).verdict == "NO-GO"
    assert evaluate_hour(_h(precipitation=1.2), d, 120, L).verdict == "NO-GO"
    assert evaluate_hour(_h(weather_code=95), d, 120, L).verdict == "NO-GO"
    assert evaluate_hour(_h(), d, 120, L, kp=5.3).verdict == "CAUTION"
    low = _h(temperature_2m=15, dew_point_2m=14.5, cloud_cover_low=90)
    assert evaluate_hour(low, d, 120, L).verdict == "NO-GO"
    assert evaluate_hour(low, d, 30, L).verdict in ("CAUTION", "NO-GO")


def test_rain_ok_drone():
    assert evaluate_hour(_h(precipitation=1.2), DRONES["survey"], 120, Limits()).verdict == "GO"


def test_helpers():
    assert wind_at_height(2, 4, 5, 100) == 4.5
    assert cloud_base_agl(20, 16) == 500


def test_plan_and_windows_on_synthetic_forecast():
    fc = synthetic_forecast(START)
    vs = plan(fc, DRONES["avata2"], 120)
    assert len(vs) == 72
    assert at_time(vs, dt.datetime(2026, 11, 27, 6)).verdict == "NO-GO"      # dawn fog
    assert at_time(vs, dt.datetime(2026, 11, 28, 15)).verdict == "NO-GO"     # storm
    wins = best_windows(vs)
    assert wins and all(w["hours"] >= 1 for w in wins)
    for w in wins:
        assert at_time(vs, dt.datetime.fromisoformat(w["start"])).verdict == "GO"


def test_kp_parser_both_formats():
    old = [["time_tag", "kp", "observed", "noaa_scale"], ["2026-11-27 00:00:00", "5.33", "predicted", "G1"]]
    new = [{"time_tag": "2026-11-27T00:00:00", "kp": 5.33, "observed": "predicted"}]
    for raw in (old, new):
        kp = parse_kp(raw)
        assert list(kp.values()) == [5.33]


def test_regions():
    assert detect_region(34.28, 75.47, 3528) == "himalaya"
    assert detect_region(26.76, 80.89, 123) == "plains"
    assert detect_region(13.50, 75.09, 650) == "western_ghats"
    assert detect_region(13.37, 77.68, 1478) == "peninsula"
    assert detect_region(11.64, 92.73, 5) == "islands"
    assert detect_region(25.57, 91.88, 1500) == "northeast"
    th = thresholds_for(Pass("x", 26.76, 80.89, 123), Thresholds())
    assert th.snow_test is False and th.fog_btd_low < Thresholds().fog_btd_low


def test_national_tile_matches_site_scores():
    import numpy as np, tempfile
    from scipy.interpolate import RegularGridInterpolator
    from passcast.demo import make_demo
    from passcast.national import run_national, N, tile_axes, key, unpack, score_cells
    dem, passes, truth, scene, when = make_demo()
    lat, lon = tile_axes(30, 75)
    f = RegularGridInterpolator((dem.lat, dem.lon), dem.z, bounds_error=False, fill_value=0)
    LA, LO = np.meshgrid(lat, lon, indexing="ij")
    z = f(np.column_stack([LA.ravel(), LO.ravel()])).reshape(N, N).astype("float32")
    with tempfile.TemporaryDirectory() as d:
        man = run_national(scene, "insat3dr", d, Thresholds(), terrain={key(30, 75): z}, tiles=[(30, 75)])
        rec = unpack(open(f"{d}/run/N30E075.bin.gz", "rb").read())
    got = [score_cells(rec, p.lat, p.lon, 30, 75, 3000, man)["score"] for p in passes]
    assert got == [1.0, 0.0, 0.5]


def test_route_rating_weakest_stretch():
    import numpy as np, tempfile
    from scipy.interpolate import RegularGridInterpolator
    from passcast.demo import make_demo
    from passcast.national import run_national, N, tile_axes, key, unpack, score_route
    dem, passes, truth, scene, when = make_demo()
    lat, lon = tile_axes(30, 75)
    f = RegularGridInterpolator((dem.lat, dem.lon), dem.z, bounds_error=False, fill_value=0)
    LA, LO = np.meshgrid(lat, lon, indexing="ij")
    z = f(np.column_stack([LA.ravel(), LO.ravel()])).reshape(N, N).astype("float32")
    with tempfile.TemporaryDirectory() as d:
        man = run_national(scene, "insat3dr", d, Thresholds(), terrain={key(30, 75): z}, tiles=[(30, 75)])
        recs = {key(30, 75): unpack(open(f"{d}/run/N30E075.bin.gz", "rb").read())}
    A, B, C = [(p.lat, p.lon) for p in passes]
    assert score_route(recs, [A, (A[0] + 0.03, A[1] - 0.05)], 2000, man)["score"] == 1.0   # short hop in clear air
    r = score_route(recs, [A, B], 2000, man)                                                 # ends inside the storm
    assert r["score"] == 0.0 and r["blocked"] >= 1


def test_terrain90_roundtrip():
    import numpy as np
    from passcast.national import encode90, decode90
    rng = np.random.default_rng(0)
    z = (3000 + np.cumsum(rng.integers(-30, 31, (1200, 1200)), axis=1)).clip(-400, 8800).astype("int16")
    assert (decode90(encode90(z)) == z).all()
