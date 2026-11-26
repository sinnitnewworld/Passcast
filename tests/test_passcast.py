import datetime as dt
import math

import numpy as np
import pytest

from passcast.config import Thresholds, Pass, SATELLITES
from passcast.satellite import geo_view_angles, parallax_correct, solar_zenith, SatScene
from passcast.cloudmask import cloud_mask
from passcast.demo import make_demo, truth_scores
from passcast.pipeline import run, process_pass
from passcast.scoring import region_cloud_amount


def test_view_angles_subpoint_and_india():
    vza, _ = geo_view_angles(np.array([0.0]), np.array([74.0]), 74.0)
    assert vza[0] == pytest.approx(0, abs=0.01)
    # Ladakh from INSAT-3DR: moderate; from Himawari: very oblique
    v_insat, az = geo_view_angles(np.array([34.0]), np.array([77.0]), 74.0)
    v_hima, _ = geo_view_angles(np.array([34.0]), np.array([77.0]), 140.7)
    assert 35 < v_insat[0] < 45
    assert v_hima[0] > 70
    assert 180 < az[0] < 200          # satellite is south, slightly west


def test_parallax_moves_toward_satellite():
    la, lo = parallax_correct(np.array([34.0]), np.array([77.0]), np.array([9000.0]),
                              np.array([40.0]), np.array([180.0]))
    shift_km = (34.0 - la[0]) * 111
    assert shift_km == pytest.approx(9 * math.tan(math.radians(40)), rel=0.02)
    assert lo[0] == pytest.approx(77.0, abs=1e-6)


def test_solar_zenith_noon_vs_night():
    noon = solar_zenith(np.array([30.0]), np.array([77.0]), dt.datetime(2026, 6, 21, 6, 50))
    night = solar_zenith(np.array([30.0]), np.array([77.0]), dt.datetime(2026, 6, 21, 18, 50))
    assert noon[0] < 15 and night[0] > 100


def _scene(**ch):
    n = ch.pop("n", 1)
    lat = np.full((n, n), 34.0); lon = np.full((n, n), 77.0)
    base = dict(ir108=None, ir120=None, vis064=None, nir16=None, ir039=None)
    base.update(ch)
    return SatScene(lat, lon, dt.datetime(2026, 11, 26, 6), "t", base,
                    np.full((n, n), 50.0), np.full((n, n), 40.0), np.full((n, n), 185.0))


def test_cold_ridge_is_not_cloud_but_cold_cloud_is():
    th = Thresholds()
    z = np.array([[2000.0, 5500.0, 2000.0, 5500.0] * 5] * 20)
    ir = 296 - 6.5e-3 * z                       # clear terrain at the lapse rate
    ir[0, 0] = 296 - 6.5e-3 * 9000              # one deep cloud over a valley
    sc = _scene(ir108=ir.astype("f4"), ir120=(ir - 0.6).astype("f4"),
                vis064=np.full(z.shape, 0.12, "f4"), nir16=np.full(z.shape, 0.2, "f4"), n=20)
    sc.channels["vis064"][0, 0] = 0.8; sc.channels["nir16"][0, 0] = 0.45
    cm = cloud_mask(sc, z, th)
    assert cm.prob[0, 1] < 0.3                  # 5,500 m ridge: cold but clear
    assert cm.prob[0, 0] > 0.8
    assert cm.cth_m[0, 0] == pytest.approx(9000, abs=600)


def test_snow_not_cloud_but_ice_cloud_is():
    th = Thresholds()
    z = np.full((20, 20), 5200.0)
    ir = (296 - 6.5e-3 * z - 3).astype("f4")    # snow is a bit colder
    vis = np.full(z.shape, 0.65, "f4"); nir = np.full(z.shape, 0.07, "f4")
    ir[0, 0] = 296 - 6.5e-3 * 9500; vis[0, 0] = 0.85; nir[0, 0] = 0.30   # ice cloud: high NDSI too
    sc = _scene(ir108=ir, ir120=ir - 0.5, vis064=vis, nir16=nir, n=20)
    cm = cloud_mask(sc, z, th)
    assert cm.prob[5, 5] < 0.4 and cm.snow[5, 5] > 0.6
    assert cm.prob[0, 0] > 0.8 and cm.snow[0, 0] < 0.2


@pytest.fixture(scope="module")
def day_demo():
    th = Thresholds(window_half_width_km=18)
    dem, passes, truth, scene, when = make_demo()
    prods = run(scene, passes, lambda p, h: dem.window(p.lat, p.lon, h), th, now=when)
    return dem, passes, truth, prods, th


def test_demo_scores_match_truth(day_demo):
    dem, passes, truth, prods, th = day_demo
    ts = truth_scores(dem, truth, passes, th)
    for pr in prods:
        assert pr.result.score == ts[pr.result.name][0], pr.result.name


def test_never_optimistic(day_demo):
    dem, passes, truth, prods, th = day_demo
    ts = truth_scores(dem, truth, passes, th)
    for pr in prods:
        assert pr.result.score <= ts[pr.result.name][0]


def test_night_demo_conservative():
    th = Thresholds(window_half_width_km=18)
    dem, passes, truth, scene, when = make_demo(night=True)
    prods = run(scene, passes, lambda p, h: dem.window(p.lat, p.lon, h), th, now=when)
    ts = truth_scores(dem, truth, passes, th)
    for pr in prods:
        assert pr.result.score <= ts[pr.result.name][0], pr.result.name


def test_region_query(day_demo):
    dem, passes, truth, prods, th = day_demo
    f = prods[1].field   # pass B under deep convection
    p = passes[1]
    d = 0.03
    r = region_cloud_amount(f, [(p.lon - d, p.lat - d), (p.lon + d, p.lat - d),
                                (p.lon + d, p.lat + d), (p.lon - d, p.lat + d)])
    assert r["cloud_cover_oktas"] >= 7
    assert r["terrain_in_cloud_percent"] > 80


def test_satellite_catalogue_complete():
    for key in ("insat3dr", "meteosat_iodc", "himawari", "modis"):
        assert set(SATELLITES[key].channels) == {"vis064", "nir16", "ir039", "ir108", "ir120"}
