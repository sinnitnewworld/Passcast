"""Command line interface.

  passcast demo                         synthetic end-to-end run with known truth
  passcast run --satellite himawari     download latest Himawari slot, score passes
  passcast run --satellite insat3dr --files ./mosdac_download/
  passcast region --satellite ... --polygon "77.1,34.2;77.3,34.2;77.3,34.4"
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from .config import load_passes, load_thresholds, Pass, SATELLITES


def _outdir(p):
    d = Path(p)
    d.mkdir(parents=True, exist_ok=True)
    return d


def cmd_demo(a):
    from .demo import make_demo, truth_scores
    from .pipeline import run
    from .report import write_json, write_pngs, write_dashboard
    from .config import Thresholds
    th = load_thresholds(a.config)
    th.window_half_width_km = min(th.window_half_width_km, 18.0)
    print("Building synthetic Himalayan terrain and simulated INSAT-3DR image ...")
    dem, passes, truth, scene, when = make_demo(night=a.night)
    products = run(scene, passes, lambda p, h: dem.window(p.lat, p.lon, h), th,
                   now=when + dt.timedelta(minutes=12))
    truth_s = truth_scores(dem, truth, passes, th)
    print("\nValidation against known truth:")
    agree = 0
    for pr in products:
        t, f = truth_s[pr.result.name]
        ok = t == pr.result.score
        agree += ok
        print(f"  {pr.result.name:<18} truth {t:<3} (obscured {f:.0%})  model {pr.result.score:<3} "
              f"(obscured {pr.result.obscured_fraction:.0%})  {'OK' if ok else 'MISMATCH'}")
    out = _outdir(a.out)
    write_json(products, scene, out / "passcast_report.json",
               {"validation": {k: {"truth_score": v[0], "truth_obscured": round(v[1], 3)} for k, v in truth_s.items()}})
    write_pngs(products, out)
    write_dashboard(products, scene, out / "passcast_dashboard.html",
                    subtitle="DEMO — synthetic terrain and simulated satellite image with known truth")
    print(f"\n{agree}/{len(products)} passes match truth. Outputs in {out}/")


def cmd_run(a):
    from .satellite import read_with_satpy, fetch_himawari, latest_himawari_slot, files_in
    from .dem import dem_for_window
    from .pipeline import run
    from .report import write_json, write_pngs, write_dashboard

    th = load_thresholds(a.config)
    passes = load_passes(a.passes)
    if a.only:
        keep = {s.strip().lower() for s in a.only.split(",")}
        passes = [p for p in passes if p.name.lower() in keep]
    sat = a.satellite
    if sat not in SATELLITES:
        raise SystemExit(f"unknown satellite {sat}; choose from {', '.join(SATELLITES)}")

    if a.files:
        files = files_in(a.files)
    elif sat == "himawari":
        when = dt.datetime.strptime(a.time, "%Y-%m-%dT%H:%M") if a.time else latest_himawari_slot()
        print(f"Fetching Himawari-9 {when:%Y-%m-%d %H:%M} UTC ...")
        files = fetch_himawari(when)
    else:
        raise SystemExit(f"{SATELLITES[sat].name}: download the files (see README) and pass --files <folder>")
    if not files:
        raise SystemExit("no satellite files found")

    lons = [p.lon for p in passes]; lats = [p.lat for p in passes]
    bbox = (min(lons) - 1, min(lats) - 1, max(lons) + 1, max(lats) + 1)
    print(f"Reading {len(files)} file(s) with satpy ...")
    scene = read_with_satpy(sat, files, bbox)
    dem_paths = [str(p) for p in Path(a.dem).glob("*.tif")] if a.dem else None
    provider = lambda p, h: dem_for_window(p.lat, p.lon, h, th.dem_resolution_m, dem_paths)
    cloud_base = json.loads(Path(a.cloud_base).read_text()) if a.cloud_base else None
    print("Scoring passes ...")
    products = run(scene, passes, provider, th, cloud_base=cloud_base)
    out = _outdir(a.out)
    stamp = scene.time.strftime("%Y%m%d_%H%M")
    if not products:
        msg = f"No site could be processed from the {scene.platform} image of {scene.time:%Y-%m-%d %H:%M} UTC."
        (out / "index.html").write_text(f"<!DOCTYPE html><meta charset=utf-8><title>PassCast</title><p>{msg}</p>")
        raise SystemExit(msg)
    write_json(products, scene, out / f"passcast_{stamp}.json")
    write_pngs(products, out)
    sub = f"{len(products)} sites · {scene.platform} · updates automatically" if a.index else f"{len(products)} sites · {scene.platform}"
    write_dashboard(products, scene, out / f"passcast_{stamp}.html", subtitle=sub)
    if a.index:      # stable names for a hosted, auto-updating page
        import shutil
        shutil.copy(out / f"passcast_{stamp}.html", out / "sites.html")
        shutil.copy(out / f"passcast_{stamp}.json", out / "latest.json")
    print(f"Outputs in {out}/")


def cmd_national(a):
    """All-India 1 km tiles + the search-anywhere page."""
    import shutil
    from .satellite import read_with_satpy, fetch_himawari, latest_himawari_slot, files_in
    from .national import build_terrain, write_terrain, run_national
    th = load_thresholds(a.config)
    if a.files:
        files = files_in(a.files)
    elif a.satellite == "himawari":
        when = dt.datetime.strptime(a.time, "%Y-%m-%dT%H:%M") if a.time else latest_himawari_slot()
        print(f"Fetching Himawari-9 {when:%Y-%m-%d %H:%M} UTC ...")
        files = fetch_himawari(when)
    else:
        raise SystemExit("pass --files for this satellite")
    print("Reading satellite data for India ...")
    scene = read_with_satpy(a.satellite, files, (65.0, 5.0, 100.0, 40.0))
    out = _outdir(a.out)
    print("Terrain (cached after the first run) ...")
    terrain = build_terrain()
    write_terrain(terrain, out)
    print("Processing tiles ...")
    man = run_national(scene, a.satellite, out, th, terrain)
    web = Path(__file__).parent / "web_national"
    for f in ("index.html", "planner.js"):
        shutil.copy(web / f, out / f)
    print(f"{len(man['tiles'])} tiles in {man['processing_s']} s -> {out}/")


def cmd_region(a):
    from .satellite import read_with_satpy, files_in
    from .dem import dem_for_window
    from .pipeline import process_pass
    from .scoring import region_cloud_amount
    th = load_thresholds(a.config)
    poly = [tuple(map(float, xy.split(","))) for xy in a.polygon.split(";")]
    lon0 = sum(p[0] for p in poly) / len(poly); lat0 = sum(p[1] for p in poly) / len(poly)
    half = max(max(abs(p[0] - lon0) for p in poly) * 111, max(abs(p[1] - lat0) for p in poly) * 111) + 5
    th.window_half_width_km = max(th.window_half_width_km, half)
    files = files_in(a.files)
    scene = read_with_satpy(a.satellite, files, (lon0 - 1, lat0 - 1, lon0 + 1, lat0 + 1))
    dem = dem_for_window(lat0, lon0, th.window_half_width_km + 10, th.dem_resolution_m)
    prod = process_pass(scene, Pass("region", lat0, lon0, float(dem.sample([lat0], [lon0])[0])), dem, th)
    print(json.dumps(region_cloud_amount(prod.field, poly), indent=2, default=float))


def cmd_drone(a):
    from .drone import (DRONES, DroneProfile, Limits, fetch_forecast, fetch_kp, plan,
                        best_windows, at_time, synthetic_forecast)
    from .drone_report import write_drone_card
    import dataclasses
    if a.site:
        match = [p for p in load_passes(a.passes) if p.name.lower() == a.site.lower()]
        if not match:
            raise SystemExit(f"site '{a.site}' not in catalogue; use --lat/--lon")
        name, lat, lon = match[0].name, match[0].lat, match[0].lon
    elif a.lat is not None and a.lon is not None:
        name, lat, lon = a.name or f"{a.lat:.3f}, {a.lon:.3f}", a.lat, a.lon
    elif not a.demo:
        raise SystemExit("give --site or --lat/--lon")
    drone = DRONES[a.drone]
    if a.max_wind:
        drone = dataclasses.replace(drone, max_wind_ms=a.max_wind, name=drone.name + " (custom wind)")
    now_ist = dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)
    if a.demo:
        name, lat, lon = (name if (a.site or a.lat) else "Nandi Hills (demo forecast)"), (lat if a.site or a.lat else 13.370), (lon if a.site or a.lat else 77.683)
        start = now_ist.replace(minute=0, second=0, microsecond=0)
        fc, kp = synthetic_forecast(start), {}
    else:
        print(f"Fetching forecast for {name} ...")
        fc, kp = fetch_forecast(lat, lon, a.days), fetch_kp()
    verdicts = plan(fc, drone, a.height, Limits(), kp)
    when = dt.datetime.strptime(a.at, "%Y-%m-%d %H:%M") if a.at else now_ist + dt.timedelta(hours=1)
    when = (when + dt.timedelta(minutes=30)).replace(minute=0)
    v = at_time(verdicts, when)
    wins = best_windows(verdicts)
    live = None
    if a.live_satellite:
        live = _live_check(a, name, lat, lon)
    print(f"\n{name} · {drone.name} · {a.height:.0f} m")
    if v:
        print(f"Planned {v.time_ist.replace('T', ' ')} IST: {v.verdict}")
        for r in v.reasons:
            print(f"  - {r}")
    else:
        print("Planned time is outside the forecast period.")
    print("Best daylight windows:")
    for w in wins or [{"start": "none", "end": "", "hours": 0, "max_wind_ms": 0}]:
        print(f"  {w['start'].replace('T', ' ')} -> {w['end'][11:]}  ({w['hours']} h, wind <= {w['max_wind_ms']} m/s)")
    if live:
        print(f"Satellite now: {live}")
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_drone_card(out, name, lat, lon, drone, a.height, verdicts, v, wins, live)
    print(f"Card: {out}")


def _live_check(a, name, lat, lon):
    from .satellite import read_with_satpy, fetch_himawari, latest_himawari_slot, files_in
    from .dem import dem_for_window
    from .pipeline import process_pass
    from .scoring import region_cloud_amount
    th = load_thresholds(a.config)
    files = files_in(a.files) if a.files else fetch_himawari(latest_himawari_slot())
    scene = read_with_satpy(a.live_satellite, files, (lon - 1, lat - 1, lon + 1, lat + 1))
    dem = dem_for_window(lat, lon, th.window_half_width_km + 10, th.dem_resolution_m)
    z = float(dem.sample([lat], [lon])[0])
    prod = process_pass(scene, Pass(name, lat, lon, z, kind="drone_spot", corridor_radius_m=2000), dem, th)
    r = prod.result
    return (f"{r.label.lower()} within 2 km ({r.obscured_fraction:.0%} obscured, "
            f"confidence {r.confidence_label.lower()}), image {scene.time:%H:%M} UTC")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="passcast", description="Flyability across India from satellite cloud imagery, plus a drone flight planner")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo"); d.add_argument("--out", default="out_demo"); d.add_argument("--night", action="store_true")
    d.add_argument("--config")
    r = sub.add_parser("run")
    r.add_argument("--satellite", default="insat3dr", choices=list(SATELLITES))
    r.add_argument("--files", help="folder with satellite files (MOSDAC / EUMETSAT / LAADS downloads)")
    r.add_argument("--time", help="Himawari slot, YYYY-MM-DDTHH:MM UTC (default latest)")
    r.add_argument("--passes", help="JSON file with pass list"); r.add_argument("--only", help="comma-separated pass names")
    r.add_argument("--dem", help="folder of local DEM GeoTIFFs (default: Copernicus GLO-30 download)")
    r.add_argument("--cloud-base", help="JSON {pass name: cloud base m AMSL} from met observations")
    r.add_argument("--config", help="JSON threshold overrides"); r.add_argument("--out", default="out")
    r.add_argument("--index", action="store_true", help="also write sites.html + latest.json (for hosting)")
    g = sub.add_parser("region")
    g.add_argument("--satellite", required=True, choices=list(SATELLITES)); g.add_argument("--files", required=True)
    g.add_argument("--polygon", required=True, help='"lon,lat;lon,lat;..."'); g.add_argument("--config")
    dr = sub.add_parser("drone", help="plan a drone flight: GO / CAUTION / NO-GO for a time and place")
    dr.add_argument("--site", help="site name from the catalogue (e.g. 'Nandi Hills')")
    dr.add_argument("--lat", type=float); dr.add_argument("--lon", type=float); dr.add_argument("--name")
    dr.add_argument("--at", help='planned time IST, "YYYY-MM-DD HH:MM" (default: next hour)')
    from .drone import DRONES
    dr.add_argument("--drone", default="mini", choices=list(DRONES))
    dr.add_argument("--max-wind", type=float, help="override rated wind resistance (m/s)")
    dr.add_argument("--height", type=float, default=120.0, help="planned height above ground (m)")
    dr.add_argument("--days", type=int, default=3)
    dr.add_argument("--demo", action="store_true", help="use a synthetic forecast (offline)")
    dr.add_argument("--live-satellite", choices=list(SATELLITES), help="also check current cloud from satellite")
    dr.add_argument("--files"); dr.add_argument("--passes"); dr.add_argument("--config")
    dr.add_argument("--out", default="out/drone_check.html")
    na = sub.add_parser("national", help="all-India 1 km tiles + search-anywhere web page")
    na.add_argument("--satellite", default="himawari", choices=list(SATELLITES))
    na.add_argument("--files"); na.add_argument("--time"); na.add_argument("--config")
    na.add_argument("--out", default="site")
    a = ap.parse_args(argv)
    {"demo": cmd_demo, "run": cmd_run, "region": cmd_region, "drone": cmd_drone, "national": cmd_national}[a.cmd](a)


if __name__ == "__main__":
    main()
