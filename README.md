# PassCast: flyability across India, from satellite imagery and forecasts

PassCast answers iDEX DISC-14 problem #59: an automated decision tool for helicopter and fixed-wing operations in the Himalayan belt, based on cloud conditions over passes and valleys.

For every pass it outputs a score:

| Score | Meaning |
|---|---|
| **1** | Clear: the flight corridor is free of cloud |
| **0.5** | Partially clear: some of the corridor is obscured, or the data can't confirm it's clear |
| **0** | Not clear: most of the corridor is inside cloud or under a low ceiling |

Each score comes with a confidence level (HIGH / MEDIUM / LOW), plain-language reasons, data-quality caveats, PNG maps and an offline HTML dashboard. The dashboard has a 2D map, a 3D terrain view, and a draw-a-region tool that reports cloud amount for any valley.

PassCast started as the Himalayan answer to #59. It now covers **any site in India**: passes, helipads, airstrips, valleys and drone spots, each with a regional profile. It also includes a **drone flight planner** (see below).

> **Decision support only.** Satellites see cloud *tops*. Cloud base, visibility under cloud, wind, turbulence and icing are not observed. The pilot or aviation met officer decides.

---

## How the problem statement maps to the tool

| DISC-14 #59 requirement | Where it's handled |
|---|---|
| Pass weather as 1 / 0.5 / 0 | `scoring.py`: corridor obscuration → score, plus confidence |
| Satellite cloud image on a DEM finer than 100 m | `dem.py` (Copernicus GLO-30, 30 m; or CartoDEM / any GeoTIFF) + `downscale.py` |
| Interpolation technique suited to hilly areas | `downscale.py`: parallax correction → Clough–Tocher C1 cubic interpolation → terrain conditioning by cloud-top height → cloud-edge terrain-height refinement for valley stratus |
| Cloud amount for any valley or selected region | `region` command and the dashboard's draw tool (percent, oktas, terrain-in-cloud, cloud top) |
| Works with Himawari, Meteosat, MODIS, INSAT-3DR | `satellite.py`: one common scene format via `satpy` readers; INSAT-3DS also configured |

## What makes it work in mountains

Ordinary cloud masks fail in the Himalaya. PassCast handles six mountain-specific problems:

1. **Cold ridges look like cloud.** A 5,500 m ridge is about 35 K colder than the valley floor. Every infrared value is therefore converted to a *sea-level-equivalent* temperature using the terrain under the pixel.
2. **Snow looks like cloud.** Snow is separated from cloud with the snow index (NDSI, using the 1.6 µm channel). Snow must also be close to the clear-sky temperature of its terrain, because ice cloud has a high NDSI too.
3. **Parallax.** A cloud top at 9 km seen from INSAT appears about 7 km away from its true position. Both cloud tops and the terrain itself are moved back to their true location.
4. **Peaks poking through a cloud sea.** A terrain cell can only be inside cloud if it lies below the cloud top. For valley stratus, the top height is refined from the terrain height where the cloud deck meets the slopes.
5. **Cloud base can't be seen from space.** Thick cloud is assumed to reach the terrain unless observed cloud-base data is supplied (`--cloud-base`).
6. **Thin cirrus hiding a lower layer.** Detected by daytime brightness and handled conservatively. At night it's flagged LOW confidence, and a clear score is capped at 0.5.

## Validation so far (synthetic data, known truth)

`passcast demo` builds Himalayan-style terrain with three passes and places known clouds on it: valley stratus, deep convection, broken cumulus under cirrus, and snow. It then simulates a 4 km INSAT-3DR image (with parallax, sensor blur and noise) and runs the real pipeline on that image.

* Day and night demos: **3/3 passes correct.**
* Sweep of 30 broken-cloud cases at a borderline pass, with and without cirrus above:
  * exact score in 19/30 cases
  * **never optimistic (30/30)**: every error is one step more cautious than the truth
  * 9 of the 11 mismatches carry a MEDIUM confidence flag

**This is not operational validation.** The next step is to run against real INSAT-3DR / Himawari imagery and compare with pilot reports, METARs and webcam images for named passes.

## Live: search anywhere in India

Every run publishes two pages:
* `index.html`: the **search page**. Search any place, type coordinates, use your location or tap the map, and get a satellite score (1 / 0.5 / 0) with confidence and reasons.
  * Each run builds a 1 km cloud-and-terrain grid of all India (29 tiles of 5° × 5°). The page loads only the tile it needs and scores it in the browser, using the same rules as the Python code (`national.score_cells`).
  * Settings such as snow detection and fog mode are chosen per 5° tile, not per point. This is an approximation.
* **Route mode** is a rule-based flight-planning aid. The engine is in `web_national/planner.js`; tests are in `tests/js/planner.test.js`.
  * **Flight rules.** Choose one set:
    * **Civil VFR:** AIP India ENR 1.2 and ICAO Annex 2. Day only (20 min before sunrise to 20 min after sunset). VMC: 5 km visibility, 8 km at and above 10,000 ft, and 1,500 m horizontally and 1,000 ft vertically from cloud; below 3,000 ft AMSL or 1,000 ft above terrain, clear of cloud with the surface in sight. Not above FL150. VFR semicircular levels when more than 3,000 ft above the ground. Terrain clearance of at least 500 ft (default 1,000 ft) within the chosen corridor.
    * **Civil IFR:** 2,000 ft above the highest terrain within 10 NM, the stricter of DGCA's 1,500 ft within 10 NM and ICAO's 2,000 ft within 8 km over mountains. IFR semicircular levels. Cloud is allowed, but flight in cloud above the forecast freezing level fails unless the aircraft is cleared for icing.
    * **Military / State aircraft:** the user enters the unit's SOP values (terrain clearance, distance from cloud, level system, night). The defaults are the civil values. The tool does not claim to follow Air Force or Army orders, which are not public.
  * **Aircraft:** presets carry sourced public figures (service ceiling, climb rate, cruise speed, HOGE) and must be confirmed against the flight manual; you can edit every value.
  * **Vertical profile:** dynamic programming over 500 ft levels along 1 km samples. Climbs are limited by the aircraft's climb rate at that altitude (falling linearly to 100 ft/min at the ceiling) and the planned climb rate; descents use the planned descent rate; both use the forecast ground speed. Orbit climbs are allowed over the departure and, as a last resort, en route. Level flight is only at correct cruising levels. Reversing a climb into a descent is penalised, so the profile never saw-tooths or dives into a valley only to climb out again.
  * **Checks:** cold-temperature altimeter correction (ICAO Doc 8168, 4% per 10 °C); oxygen time above 10,000 and 13,000 ft (ICAO Annex 6); winds at ridge level of 25 kt or more (mountain-wave caution); density altitude against HOGE at both ends; age of the satellite picture against the departure time; satellite data quality.
  * **Verdict:** each rule is shown as PASS, FAIL, CAUTION, NOT CHECKED or NOTE, with its source. 0 (NO-GO) if any rule fails; 0.5 (CAUTION) if anything needs judgement or could not be checked; 1 (GO) only when every check passes.
  * **Not checked, and listed on every plan:** airspace (prohibited, restricted and danger areas), NOTAMs, Air Defence Clearance, official IMD met, obstacles and wires, fuel, weight and balance, and landing sites.
  * **Low-level mode (fly the valleys)** uses 90 m terrain tiles for the mountain belt (`terrain90/`, built from Copernicus GLO-90 for every degree cell above 2,000 m, north of 26°N).
    * **Path search:** a least-cost search on the 90 m grid. The minimum altitude at each cell is the highest ground within the wall distance, plus the terrain-data margin (default 300 ft), plus the height above ground. Cost rises with that altitude, so the path keeps to valley floors and passes. Cloud whose estimated base is at or below the flight level blocks a cell.
    * **Climbs:** limited by the aircraft's climb rate at the low-level speed.
    * **Turn-back room:** a 180° turn at 30° bank, plus the wall distance, is checked at every point, and the stretches without room are named so the pilot knows the commit point.
    * **Other outputs:** valley width at the flight altitude, waypoints and a GPX export.
    * **Limits, stated on every plan:** satellites cannot confirm a narrow valley clear under cloud (4 km pixel); wires, ropeways and settlements are not in the data; valley winds are not modelled.
  * **Route inputs:** the valley-route search uses the same rule set; you can also import a GPX/KML track. The forecast is Open-Meteo (a model), clearly labelled as not an approved met source.
* Location input accepts place names, coordinates (decimal or degrees/minutes/seconds) and full Google Maps links. Apartment names are often missing from OpenStreetMap, so paste coordinates for those.
* `sites.html`: the **key sites** at full 30 m detail.

Command: `passcast national --satellite himawari --out site`. Terrain comes from Copernicus GLO-90; it is built once and then cached.

## Regional profiles (automatic)

| Profile | Where | What changes |
|---|---|---|
| himalaya | J&K, Ladakh, HP, Uttarakhand; anything above 2,500 m in the north | snow/cloud separation on |
| northeast | Sikkim to Mizoram below 2,500 m | thicker low stratus; no snow test |
| western_ghats | Ghats crest, Kerala to Maharashtra | monsoon stratus banked against the slopes |
| plains | Indo-Gangetic and central plains | **winter fog mode**: more sensitive night fog test, thin fog layers |
| islands | Andaman & Nicobar, Lakshadweep | maritime settings |
| peninsula | everything else | general settings |

Each site's profile is shown in its caveats. Add your own sites with `kind` = `pass`, `helipad`, `airstrip`, `valley` or `drone_spot` in a `--passes` JSON file.

## Drone flight planner

"I want to fly at Nandi Hills at 6:30 on Saturday with my Avata 2. Will it be OK?"

```bash
passcast drone --site "Nandi Hills" --at "2026-11-28 06:30" --drone avata2
passcast drone --lat 12.97 --lon 77.59 --name "Cubbon Park" --drone mini --height 60
passcast drone --demo --drone avata2          # offline demo with a synthetic forecast
```

The planner gives **GO / CAUTION / NO-GO** for the planned hour, a 72-hour colour timeline, and the best daylight windows. It also writes a phone-friendly HTML card (`out/drone_check.html`).

It checks:
* **wind** at your flight height against the drone's rated wind resistance, plus gusts
* **rain** and chance of rain
* **visibility**, so you can keep the drone in visual line of sight (fog and haze)
* **cloud base** against your planned height
* **thunderstorm** risk
* **temperature**, for battery performance
* **Kp index**: geomagnetic storms make GPS drift
* **daylight**

Drone profiles: `mini`, `avata2`, `air3`, `mavic3`, `atom2`, `fpv_light`, `survey`. Override the wind rating with `--max-wind`.

Data sources:
* hourly forecast: Open-Meteo (free, no key; free tier is for non-commercial use, so a commercial launch needs their paid plan)
* Kp forecast: NOAA SWPC

Add `--live-satellite himawari` to also check whether there is cloud over the spot right now.

> Airspace is **not** checked. Verify green/yellow/red zones on Digital Sky before every flight.

## FlyCheck web app (phone-friendly)

`web/index.html` is a single-file web app version of the drone planner. In it you can:
* search a place by name, or type coordinates like `13.37, 77.68`
* tap **Use my location**
* tap or drag a pin on the map
* save favourite spots

It calls the forecast service straight from the browser, so no server is needed.

**Put it on your phone (free, about 2 minutes):** open app.netlify.com/drop on a laptop and drag the `web` folder onto it. You get an https link. Open that link on your phone and use *Add to Home screen*. (Your browser only shares location with https sites, so the hosted link is needed for *Use my location*.)

Place search uses OpenStreetMap's Nominatim, with Open-Meteo geocoding as a fallback. Both free services are fine for personal use. A public launch with many users needs a paid geocoder and Open-Meteo's commercial plan.

## Install

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
pytest -q                                               # 19 tests
passcast demo                                           # writes out_demo/passcast_dashboard.html
```

## Run on real satellite data

**Himawari-9** downloads automatically from NOAA's public AWS bucket, no login needed. It's best for the eastern Himalaya; over Ladakh the viewing angle is above 70°, so confidence is reduced automatically.
```bash
passcast run --satellite himawari --only "Nathu La,Sela Pass"
passcast run --satellite himawari --time 2026-11-26T05:00
```

**INSAT-3DR / 3DS**: the best view of the western Himalaya.
1. Register (free) at the MOSDAC portal (mosdac.gov.in). In the satellite catalogue, choose INSAT-3DR Imager L1B (the "STD" HDF5 files).
2. Download the latest file into a folder, for example `./insat/`.
```bash
passcast run --satellite insat3dr --files ./insat/
```

**Meteosat IODC (SEVIRI)**: download the native `.nat` file from the EUMETSAT Data Store (free account), then:
```bash
passcast run --satellite meteosat_iodc --files ./seviri/
```

**MODIS**: download MOD021KM / MYD021KM from NASA LAADS DAAC (free Earthdata login) and `pip install pyhdf`, then:
```bash
passcast run --satellite modis --files ./modis/
```

**Cloud amount for any valley:**
```bash
passcast region --satellite insat3dr --files ./insat/ --polygon "75.40,34.20;75.60,34.20;75.60,34.35;75.40,34.35"
```

### Options
* `--passes my_passes.json`: your own pass list (see `examples/passes.json`). Supports a corridor radius and approach-route waypoints. **The built-in coordinates are approximate public locations; replace them with surveyed values.**
* `--dem ./cartodem/`: use local GeoTIFFs (for example CartoDEM from Bhuvan) instead of downloading Copernicus tiles.
* `--cloud-base examples/cloud_base.json`: observed cloud base per pass (from met observations or pilot reports), in m AMSL. This replaces the conservative assumption.
* `--config examples/thresholds.json`: tune every threshold (score cut-offs, minimum ceiling, snow index limits, processing resolution).

## Outputs

* `passcast_<time>.json`: machine-readable scores for integration with other systems
* `<Pass>.png`: map of each pass with cloud on hillshade and the corridor circle
* `passcast_<time>.html`: self-contained dashboard that works offline, including the 3D view

## Project layout
```
passcast/
  config.py      pass catalogue, satellites, thresholds
  dem.py         Copernicus/GeoTIFF DEM, synthetic terrain
  satellite.py   satpy readers, Himawari downloader, view geometry, parallax
  cloudmask.py   terrain-adjusted cloud mask, snow test, cirrus, night fog, cloud-top height
  downscale.py   satellite pixels -> DEM grid, terrain conditioning, layer classes
  scoring.py     1 / 0.5 / 0 scores, confidence, region cloud amount
  regions.py     automatic regional profiles across India
  drone.py       drone planner: forecast, Kp, GO/CAUTION/NO-GO, best windows
  drone_report.py  phone-friendly drone card
  pipeline.py    end-to-end per pass
  report.py      JSON, PNG, HTML dashboard
  demo.py        synthetic scenario with known truth
  cli.py         command line
tests/           unit + end-to-end tests
```

## Known limitations and next steps

1. **Real-data calibration** of thresholds against IMD/IAF observations: the most important next step.
2. **Resolution.** A 2–4 km geostationary pixel is wider than many passes. MODIS (1 km) passes and, later, higher-resolution sensors would sharpen the picture.
3. **Weather-model data** (temperature and dew point → cloud base; winds) would replace the climatological fallback and the conservative base assumption.
4. **Nowcasting.** Tracking cloud motion over the last hour would let the tool forecast scores 1–3 hours ahead.
5. **Accuracy statistics.** Scores and confidence should be checked over a full season of matched observations before any operational trial.

Third-party: three.js (MIT licence, `passcast/vendor/three.LICENSE`) is embedded in the dashboard for offline 3D.
