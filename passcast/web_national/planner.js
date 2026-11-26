/* PassCast flight-planning engine.
 *
 * Pure functions, no DOM: used by index.html and by tests (node).
 * Every limit applied here comes from a named rule (RULE_SOURCES) or from the
 * aircraft data the user confirms. Anything this engine cannot check is
 * returned in `notChecked`, never silently assumed.
 */
(function (root) {
"use strict";
const FT = 0.3048, NM = 1852, D = Math.PI / 180;
const m2ft = m => m / FT, ft2m = f => f * FT;

/* ---------------- sources ---------------- */
const SRC = {
  VFR_AIP: "AIP India ENR 1.2 (Visual Flight Rules)",
  VMC: "AIP India ENR 1.2 / ICAO Annex 2 Table 3-1 (VMC minima)",
  VFR_HT: "ICAO Annex 2 §4.6 (minimum heights, VFR)",
  LEVELS: "ICAO Annex 2 Appendix 3 (table of cruising levels, semicircular)",
  IFR_MFA: "DGCA CAR on minimum flight altitudes / ICAO Annex 2 §5.1.2 (IFR minimum levels)",
  OXY: "ICAO Annex 6 Part III (supplemental oxygen; cf. EASA CAT.IDE.H.240)",
  COLD: "ICAO Doc 8168 Vol I (cold-temperature altimeter correction, 4% per 10 °C rule)",
  WAVE: "Mountain-flying rule of thumb (FAA AIM 7-5-6 / AC 00-57): strong winds at ridge level",
  DA: "Density altitude rule of thumb: +120 ft per °C above ISA",
  ADC: "AIP India ENR 1.12 (Air Defence Clearance) and ENR 5.1 (prohibited, restricted, danger areas)",
  SOP: "Unit SOP values entered by the user",
  AC: "Aircraft data entered or confirmed by the user (flight manual)",
};

/* Typical published figures. They are starting points only: the UI asks the
   user to confirm them against the aircraft's flight manual (RFM/ODM). */
const AIRCRAFT = [
  { id: "cheetah", name: "Cheetah / Lama type (SA 315B)", ceil: 17700, roc: 1080, tas: 100, hoge: 15100,
    src: "Published SA 315B figures: service ceiling 17,715 ft; max climb 1,080 ft/min; HOGE 15,100 ft (ISA, 1,950 kg)." },
  { id: "chetak", name: "Chetak / Alouette III type (SA 316B)", ceil: 10500, roc: 850, tas: 100, hoge: null,
    src: "Published SA 316B figures disagree (10,500 ft is the most common service ceiling); max climb 850 ft/min. Use your RFM." },
  { id: "dhruv", name: "ALH Dhruv Mk III", ceil: 21300, roc: 2030, tas: 125, hoge: null,
    src: "MoD (DDP) Dhruv Mk III data: service ceiling 6,500 m; max climb 620 m/min; max cruise 250 km/h." },
  { id: "mi17", name: "Mi-17V-5", ceil: 19685, roc: 1575, tas: 120, hoge: null,
    src: "Rosoboronexport Mi-17V-5: service ceiling 6,000 m; published climb about 8 m/s; cruise about 230 km/h." },
  { id: "h125", name: "Airbus H125 (civil)", ceil: 23000, roc: 1959, tas: 112, hoge: 12700,
    src: "Airbus H125: max operating altitude 23,000 ft; climb 1,959 ft/min; HOGE 12,700 ft at MTOW, ISA; recommended cruise 112 kt." },
  { id: "custom", name: "Other aircraft (enter from flight manual)", ceil: 12000, roc: 1000, tas: 100, hoge: null,
    src: "Values entered by you." },
];

/* ---------------- flight rules ---------------- */
// u: user inputs {mode, clearFt, corridorM, milCloudVertFt, milCloudHorizM, milLevels, milNight}
function rulesFor(u) {
  if (u.mode === "ifr") return {
    mode: "ifr", label: "Civil IFR", clearM: ft2m(2000), radiusM: 10 * NM,
    cloudVertM: 0, cloudHorizM: 0, cloudAllowed: true, vfrMaxM: null, levels: "ifr", night: true,
    srcClear: SRC.IFR_MFA,
    clearText: "2,000 ft above the highest terrain within 10 NM (the stricter of DGCA's 1,500 ft within 10 NM and ICAO's 2,000 ft within 8 km over mountains)",
  };
  if (u.mode === "mil") return {
    mode: "mil", label: "Military / State aircraft (unit SOP)", clearM: ft2m(u.clearFt), radiusM: u.corridorM,
    cloudVertM: ft2m(u.milCloudVertFt), cloudHorizM: u.milCloudHorizM, cloudAllowed: false, vfrMaxM: null,
    levels: u.milLevels || "vfr", night: !!u.milNight, srcClear: SRC.SOP,
    clearText: `${Math.round(u.clearFt).toLocaleString()} ft above the highest terrain within ${(u.corridorM / 1000).toFixed(1)} km (your unit SOP value)`,
  };
  const clearFt = Math.max(500, u.clearFt);
  return {
    mode: "vfr", label: "Civil VFR", clearM: ft2m(clearFt), radiusM: u.corridorM,
    cloudVertM: ft2m(1000), cloudHorizM: 1500, cloudAllowed: false, vfrMaxM: ft2m(15000), levels: "vfr", night: false,
    srcClear: SRC.VFR_HT,
    clearText: `${clearFt.toLocaleString()} ft above the highest terrain within ${(u.corridorM / 1000).toFixed(1)} km (legal minimum 500 ft above ground, 1,000 ft within 600 m over settlements)`,
  };
}

/* ---------------- geometry / time ---------------- */
function hav(a, b) { const R = 6371, dl = (b[0] - a[0]) * D, dn = (b[1] - a[1]) * D,
  x = Math.sin(dl / 2) ** 2 + Math.cos(a[0] * D) * Math.cos(b[0] * D) * Math.sin(dn / 2) ** 2; return 2 * R * Math.asin(Math.sqrt(x)); }
function bearing(a, b) { const p1 = a[0] * D, p2 = b[0] * D, dl = (b[1] - a[1]) * D;
  const y = Math.sin(dl) * Math.cos(p2), x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
  return (Math.atan2(y, x) / D + 360) % 360; }

// Sunrise/sunset (UTC ms) for the UTC date of `t`: standard sunrise equation, ~1 min accuracy.
function sunTimes(lat, lon, t) {
  const day = Math.floor(t / 86400000), jd = day + 2440587.5 + 0.5, n = Math.round(jd - 2451545 + 0.0008);
  const Js = n - lon / 360, M = (357.5291 + 0.98560028 * Js) % 360, Mr = M * D;
  const C = 1.9148 * Math.sin(Mr) + 0.02 * Math.sin(2 * Mr) + 0.0003 * Math.sin(3 * Mr);
  const lam = ((M + C + 180 + 102.9372) % 360) * D;
  const Jt = 2451545 + Js + 0.0053 * Math.sin(Mr) - 0.0069 * Math.sin(2 * lam);
  const sd = Math.sin(lam) * Math.sin(23.4397 * D), cd = Math.cos(Math.asin(sd));
  const cw = (Math.sin(-0.833 * D) - Math.sin(lat * D) * sd) / (Math.cos(lat * D) * cd);
  if (cw > 1 || cw < -1) return null;
  const w = Math.acos(cw) / D, toMs = J => (J - 2440587.5) * 86400000;
  return { rise: toMs(Jt - w / 360), set: toMs(Jt + w / 360) };
}

/* ---------------- cruising levels (Annex 2 App 3) ---------------- */
// true if `ft` is a correct cruising level for true track `trk`. Magnetic variation in
// India is a few degrees, so tracks within 5° of 000/180 accept both and are flagged.
function levelOK(ft, trk, kind) {
  if (kind === "any") return Math.abs(ft % 500) < 1;
  let th;
  if (kind === "vfr") { if (Math.abs(((ft - 500) % 1000 + 1000) % 1000) > 1) return false; th = Math.round((ft - 500) / 1000); }
  else { if (Math.abs(ft % 1000) > 1) return false; th = Math.round(ft / 1000); }
  const near = trk < 5 || trk > 355 || Math.abs(trk - 180) < 5;
  if (near) return true;
  return trk < 180 ? th % 2 === 1 : th % 2 === 0;
}
const nearBoundary = trk => trk < 5 || trk > 355 || Math.abs(trk - 180) < 5;

/* ---------------- performance ---------------- */
// max climb (ft/min) at altitude: linear from sea-level value to 100 ft/min at the ceiling
function rocAt(ac, hft) { return Math.max(100, ac.roc - (ac.roc - 100) * Math.max(0, hft) / ac.ceil); }
const isaC = hm => 15 - 0.0065 * hm;

/* ---------------- weather lookups ---------------- */
// wx: {pts:[{km, t2m, vis, fzl, lev:[{z,t,ws,wd}]}]} or null
function wxNear(wx, km) { if (!wx || !wx.pts || !wx.pts.length) return null; let b = wx.pts[0];
  for (const p of wx.pts) if (Math.abs(p.km - km) < Math.abs(b.km - km)) b = p; return b; }
function levAt(p, hm) { if (!p || !p.lev || !p.lev.length) return null; const L = p.lev.filter(l => isFinite(l.z)).sort((a, b) => a.z - b.z);
  if (!L.length) return null; if (hm <= L[0].z) return L[0]; if (hm >= L[L.length - 1].z) return L[L.length - 1];
  for (let i = 0; i < L.length - 1; i++) if (hm >= L[i].z && hm <= L[i + 1].z) { const f = (hm - L[i].z) / (L[i + 1].z - L[i].z);
    const u = a => -Math.sin(a.wd * D) * a.ws, v = a => -Math.cos(a.wd * D) * a.ws, uu = u(L[i]) + (u(L[i + 1]) - u(L[i])) * f, vv = v(L[i]) + (v(L[i + 1]) - v(L[i])) * f;
    return { z: hm, t: L[i].t + (L[i + 1].t - L[i].t) * f, ws: Math.hypot(uu, vv), wd: (Math.atan2(-uu, -vv) / D + 360) % 360 }; }
  return null; }
function groundSpeedKt(tas, trk, lev) { if (!lev) return tas; const a = (lev.wd - trk) * D, hw = lev.ws * Math.cos(a), xw = lev.ws * Math.sin(a);
  return Math.max(20, Math.sqrt(Math.max(0, tas * tas - xw * xw)) - hw); }

/* ---------------- route sampling ---------------- */
function sampleRoute(stops) {
  const S = []; let dist = 0;
  for (let l = 0; l < stops.length - 1; l++) { const a = [stops[l].lat, stops[l].lon], b = [stops[l + 1].lat, stops[l + 1].lon], d = hav(a, b), n = Math.max(1, Math.ceil(d)), trk = bearing(a, b);
    for (let k = l ? 1 : 0; k <= n; k++) { const f = k / n; S.push({ lat: a[0] + (b[0] - a[0]) * f, lon: a[1] + (b[1] - a[1]) * f, km: dist + d * f, leg: l, trk }); }
    dist += d; }
  return { S, dist };
}

/* ---------------- raster of the route area ----------------
   getCell(lat,lon) -> {zx, z, nodata, cloud, top, base, unc} | null (heights in m). */
function buildRaster(getCell, S, padKm, res) {
  const la = S.map(p => p.lat), lo = S.map(p => p.lon), pad = padKm / 111;
  const la0 = Math.min(...la) - pad, la1 = Math.max(...la) + pad, lo0 = Math.min(...lo) - pad / Math.cos(la0 * D), lo1 = Math.max(...lo) + pad / Math.cos(la0 * D);
  const ny = Math.max(1, Math.ceil((la1 - la0) / res)), nx = Math.max(1, Math.ceil((lo1 - lo0) / res)), n = nx * ny;
  const R = { la0, lo0, res, nx, ny, zx: new Float32Array(n), z: new Float32Array(n), nd: new Uint8Array(n), top: new Float32Array(n), base: new Float32Array(n), cloud: new Uint8Array(n) };
  for (let i = 0; i < ny; i++) for (let j = 0; j < nx; j++) { const k = i * nx + j, c = getCell(la0 + (i + .5) * res, lo0 + (j + .5) * res);
    if (!c || c.nodata) { R.nd[k] = 1; continue; }
    R.zx[k] = c.zx; R.z[k] = c.z;
    // uncertain cloud heights (>500 m): widen the layer by 500 m each way
    if (c.cloud) { R.cloud[k] = 1; R.top[k] = c.top > 0 ? c.top + (c.unc ? 500 : 0) : Infinity; R.base[k] = c.base > 0 ? Math.max(0, c.base - (c.unc ? 500 : 0)) : 0; } }
  return R;
}
function cellsWithin(R, lat, lon, rM) {
  const i0 = (lat - R.la0) / R.res - .5, j0 = (lon - R.lo0) / R.res - .5, ri = rM / 111000 / R.res, rj = ri / Math.cos(lat * D), out = [];
  for (let i = Math.max(0, Math.floor(i0 - ri)); i <= Math.min(R.ny - 1, Math.ceil(i0 + ri)); i++)
    for (let j = Math.max(0, Math.floor(j0 - rj)); j <= Math.min(R.nx - 1, Math.ceil(j0 + rj)); j++)
      if (ri < .5 || ((i - i0) / ri) ** 2 + ((j - j0) / rj) ** 2 <= 1) out.push(i * R.nx + j);
  if (!out.length) out.push(Math.min(R.ny - 1, Math.max(0, Math.round(i0))) * R.nx + Math.min(R.nx - 1, Math.max(0, Math.round(j0))));
  return out;
}

/* ---------------- per-sample environment ---------------- */
function prepare(S, dist, R, rules) {
  const ramp = Math.max(3, rules.radiusM / 1000);           // km over which departure/arrival protection grows
  for (const p of S) {
    const dEnd = Math.min(p.km, dist - p.km), f = Math.min(1, dEnd / ramp);
    p.ramp = f < 1;
    const cells = cellsWithin(R, p.lat, p.lon, Math.max(300, rules.radiusM * f)), good = cells.filter(c => !R.nd[c]);
    const here = cellsWithin(R, p.lat, p.lon, 0)[0];
    p.nd = good.length < cells.length * .5 || R.nd[here];
    p.zR = good.reduce((a, c) => Math.max(a, R.zx[c]), 0);
    p.zg = R.nd[here] ? p.zR : R.z[here];
    p.zcell = R.nd[here] ? p.zR : R.zx[here];
    p.clear = rules.clearM * f;
    const lay = c => R.cloud[c] && !R.nd[c] ? [R.base[c], R.top[c]] : null;
    p.cl = cellsWithin(R, p.lat, p.lon, Math.max(rules.cloudHorizM, 500)).map(lay).filter(Boolean);
    p.cl0 = cellsWithin(R, p.lat, p.lon, 500).map(lay).filter(Boolean);
    p.cloudy = p.cl0.length > 0;
  }
}

/* ---------------- the constraint at one sample ---------------- */
const BAD = { TERRAIN: 1, CEIL: 2, VFRMAX: 4, CLOUD: 8, ICE: 16, NODATA: 32, SURFACE: 64 };
function makeChecker(S, rules, ac, u, wx, corrFn) {
  const ceilM = ft2m(ac.ceil);
  // returns bitmask of violated constraints for indicated altitude hft at sample i; also sets flags
  return function check(i, hft, info) {
    const p = S[i], h = ft2m(hft) - corrFn(hft);        // true altitude after cold correction
    let bad = 0;
    if (p.nd) bad |= BAD.NODATA;
    if (h < p.zR + p.clear - 1) bad |= BAD.TERRAIN;
    if (ft2m(hft) > ceilM + 1) bad |= BAD.CEIL;
    if (rules.vfrMaxM != null && ft2m(hft) > rules.vfrMaxM + 1) bad |= BAD.VFRMAX;
    const low = h <= Math.max(ft2m(3000), p.zg + ft2m(1000));
    if (!rules.cloudAllowed) {
      if (low) {
        // at or below 3,000 ft AMSL / 1,000 ft above terrain: clear of cloud and in sight of the surface
        for (const [b, t] of p.cl0) { if (h >= b && h <= t) bad |= BAD.CLOUD; else if (t < h) bad |= BAD.SURFACE; }
      } else {
        for (const [b, t] of p.cl) if (h >= b - rules.cloudVertM && h <= t + rules.cloudVertM) bad |= BAD.CLOUD;
        if (info) info.onTop = p.cl0.some(([b, t]) => t + rules.cloudVertM <= h);
      }
    } else {
      const inCloud = p.cl0.some(([b, t]) => h >= b && h <= t);
      if (info) info.inCloud = inCloud;
      if (inCloud) { const w = wxNear(wx, p.km);
        if (w && isFinite(w.fzl)) { if (h >= w.fzl && !u.iceCleared) bad |= BAD.ICE; }
        else if (info) info.iceUnknown = true; }
    }
    return bad;
  };
}

/* ---------------- vertical profile optimiser ----------------
   Dynamic programming over 500 ft levels along the 1 km samples.
   - level flight only at correct cruising levels (Annex 2 App 3) when more than
     3,000 ft above the ground (VFR) or always (IFR);
   - climbs/descents limited by the aircraft's climb rate at that altitude and the
     planned descent rate, at the ground speed of the sample;
   - orbit climbs allowed over the departure point and anywhere clear (valley climb);
   - costs: level changes, oxygen altitudes, VFR above cloud, wrong-parity levels. */
function optimise(S, rules, ac, u, check, gsKmh) {
  const N = S.length, step = 500;
  const lo = Math.max(0, Math.floor(m2ft(Math.min(...S.map(p => p.zg))) / step) - 1);
  const hiFt = Math.min(ac.ceil, rules.vfrMaxM != null ? m2ft(rules.vfrMaxM) : Infinity);
  const hi = Math.floor(hiFt / step), nL = hi - lo + 1;
  if (nL <= 0) return { fail: { i: 0, km: 0, why: "aircraft ceiling below the terrain" } };
  for (const [i, nm] of [[0, "departure"], [N - 1, "destination"]]) if (m2ft(S[i].zcell) > hiFt)
    return { fail: { i, km: S[i].km, code: "endhigh", why: `the ${nm} (about ${Math.round(m2ft(S[i].zcell)).toLocaleString()} ft) is higher than the highest altitude allowed here (${Math.round(hiFt).toLocaleString()} ft${rules.vfrMaxM != null && hiFt <= 15000 ? ": civil VFR may not fly above FL150" : ": the aircraft limit"})` } };
  const ftOf = L => (lo + L) * step, idx = (i, L) => i * nL + L;
  const ok = new Uint8Array(N * nL), why = new Uint8Array(N * nL), top = new Uint8Array(N * nL);
  for (let i = 0; i < N; i++) for (let L = 0; L < nL; L++) { const inf = {}; const b = check(i, ftOf(L), inf); why[idx(i, L)] = b; ok[idx(i, L)] = b === 0; top[idx(i, L)] = inf.onTop ? 1 : 0; }
  const legal = (i, L) => { const f = ftOf(L), p = S[i];
    if (rules.levels !== "ifr" && ft2m(f) <= p.zg + ft2m(3000)) return true;   // Annex 2 §4.7: VFR levels apply above 3,000 ft AGL
    return levelOK(f, p.trk, rules.levels); };
  const kmCost = (i, L) => { const f = ftOf(L); let c = 1 + .004 * L;   // per km: prefer lower, avoid oxygen altitudes and flight above cloud
    if (f > 10000) c += .3; if (f > 13000) c += 1;
    if (top[idx(i, L)]) c += 2; return c; };
  // state = (sample, level, last vertical move d: 0 level, 1 climbing, 2 descending)
  const sid = (i, L, d) => (i * nL + L) * 3 + d, NS = N * nL * 3;
  const dist = new Float64Array(NS).fill(Infinity), prev = new Int32Array(NS).fill(-2), kind = new Uint8Array(NS);
  const relax = (from, to, c, k) => { if (c < dist[to]) { dist[to] = c; prev[to] = from; kind[to] = k; } };
  const REV = 25;                                  // reversing climb <-> descent: no saw-tooth profiles
  // start: on the ground at A; first airborne level is the lowest level at or above the cell's peak
  const gA = Math.ceil(m2ft(S[0].zcell) / step) - lo, g0 = Math.max(0, gA);
  for (let L = g0; L < nL; L++) { if (!ok[idx(0, L)]) break; const s0 = sid(0, L, L > g0 ? 1 : 0); dist[s0] = (L - g0) * 3; prev[s0] = -1; kind[s0] = 3; }
  const kUp = (i, L) => { const r = Math.min(u.climbFpm, rocAt(ac, ftOf(L))); return Math.max(1, Math.ceil(step / (r * 60 / gsKmh[i]))); };
  const kDn = i => Math.max(1, Math.ceil(step / (u.descFpm * 60 / gsKmh[i])));
  for (let i = 0; i < N; i++) {
    // orbit climb at this sample (needs the level above clear here); over the departure it is normal,
    // en route it is a last resort
    for (let L = 0; L < nL - 1; L++) for (let d = 0; d < 3; d++) { const a = sid(i, L, d); if (dist[a] === Infinity || !ok[idx(i, L + 1)]) continue;
      relax(a, sid(i, L + 1, 1), dist[a] + (i === 0 ? 3 : 15) + (d === 2 ? REV : 0), 4); }
    if (i === N - 1) break;
    for (let L = 0; L < nL; L++) for (let d = 0; d < 3; d++) { const a = sid(i, L, d), d0 = dist[a]; if (d0 === Infinity) continue;
      // level flight: only at a correct cruising level for the track (hard rule)
      if (ok[idx(i + 1, L)] && legal(i + 1, L)) relax(a, sid(i + 1, L, 0), d0 + kmCost(i + 1, L), 0);
      // climb one level over k samples: lower level clear of terrain all the way, both clear of cloud
      if (L + 1 < nL) { const k = kUp(i, L), j1 = Math.min(N - 1, i + k); let good = true;
        for (let j = i + 1; j <= j1 && good; j++) good = (j < j1 ? ok[idx(j, L)] : true) && ok[idx(j, L + 1)];
        if (good) { let c = d0 + 2 + (d === 2 ? REV : 0); for (let j = i + 1; j <= j1; j++) c += kmCost(j, L); relax(a, sid(j1, L + 1, 1), c, 1); } }
      // descend one level over k samples: the lower level must be clear the whole way
      if (L > 0) { const k = kDn(i), j1 = Math.min(N - 1, i + k); let good = true;
        for (let j = i + 1; j <= j1 && good; j++) good = ok[idx(j, L - 1)] && (j < j1 ? ok[idx(j, L)] : true);
        if (good) { let c = d0 + 2 + (d === 1 ? REV : 0); for (let j = i + 1; j <= j1; j++) c += kmCost(j, L); relax(a, sid(j1, L - 1, 2), c, 2); } }
    }
  }
  // finish: descend over B to the ground, every level on the way clear of cloud
  const gB = Math.max(0, Math.ceil(m2ft(S[N - 1].zcell) / step) - lo); let best = -1, bc = Infinity;
  for (let L = gB; L < nL; L++) { let clear = true;
    for (let l = gB; l <= L; l++) if (why[idx(N - 1, l)] & (BAD.CLOUD | BAD.ICE)) { clear = false; break; }
    if (!clear) continue;
    for (let d = 0; d < 3; d++) { const e = sid(N - 1, L, d); if (dist[e] === Infinity) continue; const c = dist[e] + (L - gB) * 3 + (d === 1 && L > gB ? REV : 0); if (c < bc) { bc = c; best = e; } } }
  const dl = new Float64Array(N * nL).fill(Infinity); for (let q = 0; q < NS; q++) { const k = (q / 3) | 0; if (dist[q] < dl[k]) dl[k] = dist[q]; }
  if (best < 0) return { fail: diagnose(S, ok, why, dl, nL, ftOf, idx) };
  // reconstruct
  const states = []; for (let s = best; s >= 0; s = prev[s]) { const q = (s / 3) | 0; states.push({ i: Math.floor(q / nL), L: q % nL, kind: kind[s] }); }
  states.reverse();
  const alt = new Float64Array(N).fill(NaN), events = [];
  const first = states[0]; if (first.L > g0) events.push({ type: "orbit-up", i: 0, from: ftOf(g0), to: ftOf(first.L) });
  alt[first.i] = ftOf(first.L);
  for (let s = 1; s < states.length; s++) { const a = states[s - 1], b = states[s];
    if (b.kind === 4) { events.push({ type: "orbit-up", i: b.i, from: ftOf(a.L), to: ftOf(b.L) }); alt[b.i] = ftOf(b.L); continue; }
    for (let j = a.i + 1; j <= b.i; j++) alt[j] = ftOf(a.L) + (ftOf(b.L) - ftOf(a.L)) * (j - a.i) / (b.i - a.i); }
  const last = states[states.length - 1];
  if (last.L > gB + 1) events.push({ type: "orbit-down", i: N - 1, from: ftOf(last.L), to: ftOf(gB) });
  const ev = []; for (const e of events) { const p = ev[ev.length - 1]; if (p && p.type === e.type && p.i === e.i) p.to = e.to; else ev.push(e); }
  return { alt: Array.from(alt), events: ev, lo, nL, ftOf };
}

function diagnose(S, ok, why, dist, nL, ftOf, idx) {
  const N = S.length;
  for (let i = 0; i < N; i++) { let any = false, w = 0xff;
    for (let L = 0; L < nL; L++) { if (ok[idx(i, L)]) { any = true; break; } w &= why[idx(i, L)]; }
    if (!any) { let all = 0; for (let L = 0; L < nL; L++) all |= why[idx(i, L)];
      const r = [];
      if (w & BAD.NODATA) r.push("no usable satellite data");
      if (all & BAD.TERRAIN && !(all & (BAD.CLOUD | BAD.SURFACE | BAD.ICE))) r.push("terrain plus required clearance is above every altitude this aircraft/rule set allows");
      else { if (all & BAD.TERRAIN) r.push("lower levels: too close to terrain"); if (all & (BAD.CLOUD | BAD.SURFACE)) r.push("higher levels: in cloud or without the required cloud separation"); if (all & BAD.ICE) r.push("in cloud above the freezing level (icing)"); }
      return { i, km: S[i].km, why: r.join("; ") || "no level satisfies all limits" }; } }
  let far = 0; for (let i = 0; i < N; i++) for (let L = 0; L < nL; L++) if (dist[idx(i, L)] < Infinity) far = i;
  return { i: far, km: S[far].km, why: "the aircraft cannot climb or descend fast enough between the usable levels here, even with an orbit climb" };
}

/* ---------------- fixed-altitude profile (user's planned altitude) ---------------- */
function fixedProfile(S, ac, u, hft, gsKmh) {
  const N = S.length, alt = new Array(N), gA = m2ft(S[0].zcell), gB = m2ft(S[N - 1].zcell);
  let up = gA; alt[0] = gA;
  for (let i = 1; i < N; i++) { const r = Math.min(u.climbFpm, rocAt(ac, up)) * 60 / gsKmh[i] * (S[i].km - S[i - 1].km); up = Math.min(hft, up + r); alt[i] = up; }
  let dn = gB; alt[N - 1] = Math.min(alt[N - 1], gB);
  for (let i = N - 2; i >= 0; i--) { dn = Math.min(hft, dn + u.descFpm * 60 / gsKmh[i] * (S[i + 1].km - S[i].km)); alt[i] = Math.min(alt[i], dn); }
  return { alt, events: [] };
}

/* ---------------- the whole plan ---------------- */
function plan(opts) {
  const { stops, getCell, res, u, wx, imageTime } = opts;
  const ac = Object.assign({}, opts.ac);
  const rules = rulesFor(u);
  const { S, dist } = sampleRoute(stops);
  if (S.length < 2) return { error: "Add at least two stops." };
  if (S.length > 1500) return { error: `Route is ${Math.round(dist)} km; keep routes under 1,500 km.` };
  const R = buildRaster(getCell, S, Math.max(rules.radiusM, rules.cloudHorizM) / 1000 + 2, res);
  prepare(S, dist, R, rules);

  // weather-derived quantities
  const wA = wxNear(wx, 0), refLev = p => levAt(wxNear(wx, p.km), ft2m(Math.min(ac.ceil, 10000)));
  const gsKmh = S.map(p => groundSpeedKt(u.tas, p.trk, refLev(p)) * 1.852);
  // Doc 8168: altimeter (QNH from the departure) over-reads in cold air: true = indicated − corr
  const dev = wA && isFinite(wA.t2m) ? wA.t2m - isaC(S[0].zg) : null;
  const corr = hft => dev != null && dev < 0 ? Math.max(0, (ft2m(hft) - S[0].zg)) * 0.004 * (-dev) : 0;
  const check = makeChecker(S, rules, ac, u, wx, corr);

  let prof, planned = u.plannedFt != null;
  if (planned) prof = fixedProfile(S, ac, u, u.plannedFt, gsKmh);
  else { prof = optimise(S, rules, ac, u, check, gsKmh); }

  const out = { S, dist, rules, ac, u, R, prof, planned, dev, corrAt: corr, gsKmh };
  // evaluate the profile
  let t = 0; const times = [0];
  for (let i = 1; i < S.length; i++) { t += (S[i].km - S[i - 1].km) / gsKmh[i] * 60; times.push(t); }
  const orbitMin = (prof.events || []).reduce((a, e) => a + Math.abs(e.to - e.from) / (e.type === "orbit-up" ? Math.min(u.climbFpm, rocAt(ac, (e.to + e.from) / 2)) : u.descFpm), 0);
  out.flightMin = t + orbitMin; out.times = times;
  if (prof.fail) { out.fail = prof.fail; }
  const alt = prof.alt || S.map(() => NaN);
  S.forEach((p, i) => { p.alt = alt[i];
    if (!isFinite(alt[i])) { p.bad = -1; return; }
    const inf = {}; const airborne = i > 0 && i < S.length - 1;
    p.bad = airborne ? check(i, alt[i], inf) : (check(i, alt[i], inf) & (BAD.CLOUD | BAD.ICE | BAD.NODATA));
    p.onTop = !!inf.onTop; p.inCloud = !!inf.inCloud; p.iceUnknown = !!inf.iceUnknown;
    const pIdx = levelPhase(alt, i); p.phase = pIdx;
    p.levelOK = pIdx !== "level" || (rules.levels !== "ifr" && ft2m(alt[i]) <= p.zg + ft2m(3000)) || levelOK(Math.round(alt[i] / 100) * 100, p.trk, rules.levels);
    p.trueM = ft2m(alt[i]) - corr(alt[i]);
  });
  out.checks = evaluate(out, wx, imageTime, opts.depTime);
  return out;
}
function levelPhase(alt, i) { const a = alt[i - 1] ?? alt[i], b = alt[i + 1] ?? alt[i];
  if (Math.abs(b - alt[i]) < 1 && Math.abs(alt[i] - a) < 1) return "level"; return b > a ? "climb" : "descent"; }

/* ---------------- rule-by-rule evaluation ---------------- */
// status: PASS | FAIL | CAUTION | INFO | NOT_CHECKED
function evaluate(P, wx, imageTime, depTime) {
  const { S, rules, ac, u, prof, dist } = P, C = [];
  const add = (id, status, rule, detail, src) => C.push({ id, status, rule, detail, src });
  const air = S.filter((p, i) => i > 0 && i < S.length - 1 && isFinite(p.alt));
  const runs = mask => { const r = []; let cur = null; for (const p of S) { if (isFinite(p.alt) && p.bad > 0 && (p.bad & mask)) { if (cur && p.km - cur.b <= 1.5) cur.b = p.km; else { cur = { a: p.km, b: p.km }; r.push(cur); } } } return r; };
  const fmtRuns = r => r.slice(0, 4).map(x => Math.round(x.a) === Math.round(x.b) ? `km ${Math.round(x.a)}` : `km ${Math.round(x.a)}–${Math.round(x.b)}`).join(", ") + (r.length > 4 ? ` and ${r.length - 4} more` : "");
  const f = n => Math.round(n).toLocaleString();

  if (P.fail) add("profile", "FAIL", "A flyable vertical profile exists", `No profile satisfies every limit. First problem at km ${Math.round(P.fail.km)}: ${P.fail.why}.`, SRC.AC);

  const nd = S.filter(p => p.nd).length / S.length;
  add("data", nd > .2 ? "FAIL" : nd > 0 ? "CAUTION" : "PASS", "Satellite and terrain data cover the route",
    nd > 0 ? `${Math.round(nd * 100)}% of the route has no usable satellite data.` : "Full coverage.", "PassCast data");

  if (!P.fail || P.planned) {
    const t = runs(BAD.TERRAIN);
    add("terrain", t.length ? "FAIL" : "PASS", "Terrain clearance", t.length ? `Below the required clearance at ${fmtRuns(t)}. Required: ${rules.clearText}.` : `Profile keeps ${rules.clearText}.`, rules.srcClear);
    const c = runs(BAD.CEIL);
    add("ceiling", c.length ? "FAIL" : "PASS", "Within the aircraft's maximum operating altitude", c.length ? `Above ${f(ac.ceil)} ft at ${fmtRuns(c)}.` : `Highest point of the profile ${f(Math.max(...air.map(p => p.alt), 0))} ft; limit ${f(ac.ceil)} ft.`, SRC.AC);
    if (rules.vfrMaxM != null) { const v = runs(BAD.VFRMAX);
      add("vfrmax", v.length ? "FAIL" : "PASS", "VFR not above FL150", v.length ? `Above FL150 at ${fmtRuns(v)}: file IFR or obtain authorisation.` : "Profile stays at or below FL150 (altitude used as an approximation of flight level).", SRC.VFR_AIP); }
    if (!rules.cloudAllowed) {
      const k = runs(BAD.CLOUD | BAD.SURFACE);
      add("vmc", k.length ? "FAIL" : "PASS", rules.mode === "mil" ? "Cloud separation (unit SOP)" : "VMC distance from cloud",
        k.length ? `Cloud separation not met at ${fmtRuns(k)}.` : rules.mode === "mil" ? `Keeps ${f(m2ft(rules.cloudVertM))} ft vertically and ${f(rules.cloudHorizM)} m horizontally from detected cloud.` :
          "Keeps 1,000 ft vertically and 1,500 m horizontally from detected cloud; clear of cloud and in sight of the surface below 3,000 ft AMSL / 1,000 ft above terrain.",
        rules.mode === "mil" ? SRC.SOP : SRC.VMC);
      const top = S.filter(p => p.onTop).length;
      if (top) add("ontop", "CAUTION", "Flight above a cloud layer", `About ${top} km would be flown above cloud with no surface reference below. Check that this is permitted for your operation and that a safe descent is available.`, rules.mode === "mil" ? SRC.SOP : SRC.VFR_AIP);
    } else {
      const ic = runs(BAD.ICE), unk = S.some(p => p.iceUnknown);
      add("ice", ic.length ? "FAIL" : unk ? "NOT_CHECKED" : "PASS", "Icing (in cloud above the freezing level)",
        ic.length ? `In cloud above the freezing level at ${fmtRuns(ic)}; aircraft not marked as cleared for icing.` : unk ? "Profile enters cloud but the freezing level is not available." : "No in-cloud flight above the forecast freezing level.", "Forecast freezing level (Open-Meteo)");
      add("ifrroute", "INFO", "Published IFR minimum altitudes", "This is a grid minimum altitude. On ATS routes fly at or above the published MEA/MOCA in AIP India ENR 3.", SRC.IFR_MFA);
    }
  }

  const noProfile = P.fail && !P.planned;
  if (noProfile) { evaluateTail(); return C; }
  // cruising levels
  const wrong = S.filter(p => isFinite(p.alt) && p.phase === "level" && !p.levelOK);
  const nearB = S.some(p => nearBoundary(p.trk));
  if (rules.levels !== "any") add("levels", wrong.length ? "CAUTION" : nearB ? "CAUTION" : "PASS", "Cruising level matches direction of flight",
    wrong.length ? `About ${wrong.length} km in level flight at a level not matching the track (semicircular rule).` :
      nearB ? "Part of the route tracks within 5° of north or south: confirm the parity with the magnetic track on your chart." :
        `${rules.levels === "ifr" ? "IFR" : "VFR"} semicircular levels used (track 000–179°: odd${rules.levels === "vfr" ? " +500" : ""}; 180–359°: even${rules.levels === "vfr" ? " +500" : ""}).`, SRC.LEVELS);

  // climb/descent performance
  if (P.planned) { const gA = m2ft(S[0].zcell); add("perf", "INFO", "Climb and descent", `Profile climbs at up to ${f(u.climbFpm)} ft/min (limited by the aircraft's climb at altitude) and descends at ${f(u.descFpm)} ft/min.`, SRC.AC); }
  else if (prof && prof.events) { const o = prof.events.filter(e => e.type === "orbit-up" && e.i > 0);
    add("perf", "PASS", "Climbs and descents within aircraft performance", `Climb rate never above ${f(u.climbFpm)} ft/min or the aircraft's estimated maximum at that altitude; descents at ${f(u.descFpm)} ft/min.${o.length ? ` Includes ${o.length} orbit climb(s) before high ground.` : ""}`, SRC.AC); }

  // oxygen
  const tAbove = th => { let m = 0; for (let i = 1; i < S.length; i++) if (S[i].alt > th) m += (S[i].km - S[i - 1].km) / P.gsKmh[i] * 60; return m; };
  const t10 = tAbove(10000), t13 = tAbove(13000);
  if (t13 > 0) add("oxy", u.oxygen ? "PASS" : "FAIL", "Supplemental oxygen", `${Math.round(t13)} min above 13,000 ft: oxygen needed for every occupant for that time${u.oxygen ? " (marked as carried)" : "; not marked as carried"}.`, SRC.OXY);
  else if (t10 > 30) add("oxy", u.oxygen ? "PASS" : "CAUTION", "Supplemental oxygen", `${Math.round(t10)} min between 10,000 and 13,000 ft: crew oxygen needed beyond 30 min.`, SRC.OXY);
  else add("oxy", "PASS", "Supplemental oxygen", t10 > 0 ? `${Math.round(t10)} min above 10,000 ft (under 30 min).` : "Profile stays at or below 10,000 ft.", SRC.OXY);

  evaluateTail(); return C;

  function evaluateTail() {
  // daylight
  if (!depTime) add("day", rules.night ? "INFO" : "NOT_CHECKED", "Daylight", "No departure time given.", SRC.VFR_AIP);
  else { const arr = depTime + (noProfile ? 0 : P.flightMin) * 60000, a = sunTimes(S[0].lat, S[0].lon, depTime + 5.5 * 3600000), b = sunTimes(S[S.length - 1].lat, S[S.length - 1].lon, arr + 5.5 * 3600000);
    if (!a || !b) add("day", "NOT_CHECKED", "Daylight", "Could not compute sunrise/sunset.", SRC.VFR_AIP);
    else { const early = a.rise - 20 * 60000, late = b.set + 20 * 60000, ok = depTime >= early && arr <= late;
      const ist = ms => new Date(ms + 5.5 * 3600000).toISOString().slice(11, 16);
      add("day", ok ? "PASS" : rules.night ? "INFO" : "FAIL", rules.night ? "Daylight (night flying permitted under these rules)" : rules.mode === "mil" ? "Daylight (night flying not marked as authorised)" : "VFR by day only",
        `Departure ${ist(depTime)} IST, arrival about ${ist(arr)} IST. Window: ${ist(early)} to ${ist(late)} IST (20 min before sunrise to 20 min after sunset).${!ok && rules.night ? " Part of the flight is at night: night procedures apply." : ""}`, rules.mode === "vfr" ? SRC.VFR_AIP : rules.mode === "mil" ? SRC.SOP : "Sunrise/sunset"); } }

  // weather from forecast
  if (!wx || !wx.pts || !wx.pts.length) add("wx", "NOT_CHECKED", "Visibility, wind, temperature and freezing level", "Forecast not available, so visibility, winds aloft, cold-temperature correction, icing and density altitude are not checked.", "Forecast (Open-Meteo)");
  else {
    if (rules.mode !== "ifr") { let worst = null;
      for (const p of S) { const w = wxNear(wx, p.km); if (!w || !isFinite(w.vis)) continue; const need = isFinite(p.alt) && p.alt < 10000 ? 5000 : 8000;
        if (!worst || w.vis - need < worst.v - worst.need) worst = { v: w.vis, need, km: p.km }; }
      if (worst) add("vis", worst.v >= worst.need ? "PASS" : "FAIL", "VMC flight visibility", `Lowest forecast visibility on the route ${(worst.v / 1000).toFixed(1)} km near km ${Math.round(worst.km)}; required ${worst.need / 1000} km at that altitude. Model surface visibility, not an observation.`, SRC.VMC); }
    const fz = Math.min(...wx.pts.map(p => p.fzl).filter(isFinite));
    if (isFinite(fz)) { const coldCloud = S.filter(p => isFinite(p.alt) && p.trueM >= fz && p.cl.length).length;
      if (!rules.cloudAllowed) add("fz", coldCloud ? "CAUTION" : "INFO", "Freezing level", `Forecast freezing level about ${f(m2ft(fz))} ft.${coldCloud ? ` About ${coldCloud} km above it near cloud: any inadvertent cloud entry means icing.` : ""}`, "Forecast (Open-Meteo)"); }
    if (P.dev != null) add("cold", P.dev < 0 ? "INFO" : "PASS", "Cold-temperature altimeter error", P.dev < 0 ? `Departure ${Math.round(-P.dev)} °C colder than ISA: true altitude is lower than indicated (about ${f(m2ft(P.corrAt(10000 + m2ft(S[0].zg))))} ft at 10,000 ft above the departure). Terrain clearance here already allows for it.` : "Not colder than ISA at the departure: no correction needed.", SRC.COLD);
    // mountain wave: strong wind at ridge level over high relief
    let wave = null;
    for (const p of S) { const w = levAt(wxNear(wx, p.km), p.zR + 300); if (!w) continue; const relief = p.zR - p.zg;
      if (w.ws >= 25 && relief > 900 && (!wave || w.ws > wave.ws)) wave = { ws: w.ws, wd: w.wd, km: p.km }; }
    add("wave", wave ? "CAUTION" : "PASS", "Wind over ridges (mountain-wave turbulence)", wave ? `Forecast ${Math.round(wave.ws)} kt from ${Math.round(wave.wd)}° at ridge level near km ${Math.round(wave.km)}: expect turbulence and strong downdrafts on lee slopes.` : "No forecast wind of 25 kt or more at ridge level.", SRC.WAVE);
    // hover performance at the two ends
    for (const [nm, p, i] of [["departure", S[0], 0], ["destination", S[S.length - 1], S.length - 1]]) { const w = wxNear(wx, p.km); if (!w || !isFinite(w.t2m)) continue;
      const pa = m2ft(p.zg), da = pa + 120 * (w.t2m - isaC(p.zg));
      if (ac.hoge) add("hover" + i, da > ac.hoge ? "CAUTION" : "PASS", `Hover performance at the ${nm}`, `Density altitude about ${f(da)} ft (elevation ${f(pa)} ft, ${Math.round(w.t2m)} °C); your HOGE ceiling ${f(ac.hoge)} ft.${da > ac.hoge ? " Above HOGE: only an in-ground-effect hover may be possible; check the RFM charts for your weight." : ""}`, SRC.DA);
      else add("hover" + i, "NOT_CHECKED", `Hover performance at the ${nm}`, `Density altitude about ${f(da)} ft. Enter the HOGE ceiling for your take-off weight to check it.`, SRC.DA); }
  }

  // satellite picture age at departure
  if (imageTime) { const ref = depTime || Date.now(), age = (ref - imageTime) / 60000;
    if (age < -30) add("age", "CAUTION", "Cloud picture is current for the departure", "The departure time is earlier than the satellite image: it is in the past. Set the time you will actually depart.", "PassCast satellite data");
    else add("age", age > 60 ? "CAUTION" : "PASS", "Cloud picture is current for the departure",
      `The satellite image is ${Math.max(0, Math.round(age))} min older than the ${depTime ? "departure" : "current"} time.${age > 60 ? " Satellites show cloud now, not later: re-check close to departure." : ""}`, "PassCast satellite data"); }
  }
}

/* ---------------- verdict ---------------- */
function verdict(checks) {
  if (checks.some(c => c.status === "FAIL")) return 0;
  if (checks.some(c => c.status === "CAUTION" || c.status === "NOT_CHECKED")) return .5;
  return 1;
}

/* What the user must still check: this engine has no data for these. */
function notChecked(rules) {
  const L = [
    "Prohibited, restricted and danger areas, and NOTAMs (AIP India ENR 5.1, current NOTAMs).",
    "Air Defence Clearance and flight plan, where required (AIP India ENR 1.12).",
    "Official meteorological briefing: IMD METAR/TAF/SIGMET or the unit met officer. This tool's forecast is a model, not an approved source.",
    "Obstacles: masts, power lines and ropeways across valleys are not in the terrain data.",
    "Fuel and reserves, weight and balance, and the performance class for the day.",
    "Landing site suitability, approach and departure paths at each end.",
  ];
  if (rules.mode === "ifr") L.push("IFR approval of the aircraft and crew; published routes, MEAs and instrument procedures.");
  if (rules.mode === "mil") L.push("Your unit's orders: this tool cannot know Air Force or Army flying orders; the values you entered stand in for them.");
  return L;
}

/* ---------------- low-level (valley) planning on 90 m terrain ----------------
   The aircraft follows valleys at a set height above the ground:
     minimum altitude at a cell = highest terrain within the lateral clearance
                                  + terrain-data margin + height above ground
   - path: least-cost search on the 90 m grid (cost rises with that altitude, so
     the path keeps to valley floors and low passes; cloud that reaches the flight
     level blocks a cell);
   - profile: climbs start early enough for the aircraft's climb rate at low speed;
   - turn-back room: at each point, is there a circle of (turn radius + lateral
     clearance) clear of terrain at the flight altitude? Where not, the plan names
     the commit point. */
function planLow(opts) {
  const { stops, getZ, getCell, u, wx, imageTime, depTime } = opts, ac = Object.assign({}, opts.ac), rules = rulesFor(u);
  if (rules.mode === "ifr") return { error: "Low-level flying is a visual operation: choose Civil VFR or Military." };
  const res = 1 / 1200, marginM = ft2m(u.demMarginFt), aglM = ft2m(u.aglFt), latM = u.latM, vms = u.lowKt * 0.51444;
  const turnR = vms * vms / (9.81 * Math.tan(30 * D)), roomM = turnR + latM;
  const capM = Math.min(ft2m(ac.ceil), rules.vfrMaxM ?? Infinity);
  const main = stops.filter(s => !s.via); if (main.length < 2) return { error: "Add at least two stops." };
  const pts = [];
  for (let l = 0; l < main.length - 1; l++) {
    const a = main[l], b = main[l + 1], pad = 0.04 + roomM / 111000;
    const la0 = Math.min(a.lat, b.lat) - pad, la1 = Math.max(a.lat, b.lat) + pad, mid = (a.lat + b.lat) / 2;
    const lo0 = Math.min(a.lon, b.lon) - pad / Math.cos(mid * D), lo1 = Math.max(a.lon, b.lon) + pad / Math.cos(mid * D);
    const ny = Math.ceil((la1 - la0) / res), nx = Math.ceil((lo1 - lo0) / res), n = nx * ny;
    if (n > 3.2e6) return { error: `Leg ${a.name} → ${b.name} is too long for a low-level search. Keep each leg under about 100 km by adding stops.` };
    const z = new Float32Array(n); let miss = 0;
    for (let i = 0; i < ny; i++) for (let j = 0; j < nx; j++) { const v = getZ(la0 + (i + .5) * res, lo0 + (j + .5) * res); if (v == null) { z[i * nx + j] = NaN; miss++; } else z[i * nx + j] = v; }
    if (miss > n * .02) return { error: "No 90 m terrain for this area. Low-level mode covers the Himalaya and the Northeast (terrain above 2,000 m)." };
    const kx = Math.cos(mid * D) * 111320 * res, ky = 111320 * res;
    // separable max filter (rectangle: slightly conservative versus a circle)
    const maxf = (src, rm) => { const ri = Math.ceil(rm / ky), rj = Math.ceil(rm / kx), t = new Float32Array(n), o = new Float32Array(n);
      for (let i = 0; i < ny; i++) for (let j = 0; j < nx; j++) { let m = -1e9; for (let d = -rj; d <= rj; d++) { const jj = j + d; if (jj >= 0 && jj < nx) { const v = src[i * nx + jj]; if (v > m) m = v; } } t[i * nx + j] = m; }
      for (let i = 0; i < ny; i++) for (let j = 0; j < nx; j++) { let m = -1e9; for (let d = -ri; d <= ri; d++) { const ii = i + d; if (ii >= 0 && ii < ny) { const v = t[ii * nx + j]; if (v > m) m = v; } } o[i * nx + j] = m; }
      return o; };
    const zf = Float32Array.from(z, v => isNaN(v) ? 9000 : v), wall = maxf(zf, latM), room = maxf(zf, roomM);
    // cloud from the 1 km satellite cells
    const c0 = Math.floor(la0 / .01), d0 = Math.floor(lo0 / .01), cny = Math.ceil(la1 / .01) - c0 + 1, cnx = Math.ceil(lo1 / .01) - d0 + 1, cl = new Array(cny * cnx);
    for (let i = 0; i < cny; i++) for (let j = 0; j < cnx; j++) { const c = getCell((c0 + i + .5) * .01, (d0 + j + .5) * .01); cl[i * cnx + j] = c && !c.nodata && c.cloud ? { base: c.base > 0 ? c.base : 0, top: c.top > 0 ? c.top : Infinity } : c && !c.nodata ? null : "nd"; }
    const cloudAt = (la, lo) => { const i = Math.floor(la / .01) - c0, j = Math.floor(lo / .01) - d0; return i >= 0 && j >= 0 && i < cny && j < cnx ? cl[i * cnx + j] : "nd"; };
    const near = (la, lo) => { for (let di = -2; di <= 2; di++) for (let dj = -2; dj <= 2; dj++) { const c = cloudAt(la + di * .01, lo + dj * .01); if (c && c !== "nd") return true; } return false; };
    const H = new Float32Array(n), blk = new Uint8Array(n), under = new Uint8Array(n);
    for (let i = 0; i < ny; i++) for (let j = 0; j < nx; j++) { const k = i * nx + j, la = la0 + (i + .5) * res, lo = lo0 + (j + .5) * res;
      H[k] = wall[k] + marginM + aglM;
      if (isNaN(z[k])) { blk[k] = 1; continue; }
      const c = cloudAt(la, lo);
      if (c === "nd") { blk[k] = 2; continue; }
      if (c && c.base <= H[k]) { blk[k] = 3; continue; }      // cloud reaches the flight level: not clear of cloud
      if (H[k] > capM) { blk[k] = 1; continue; }
      if (c || near(la, lo)) under[k] = 1; }
    const cell = (la, lo) => [Math.min(ny - 1, Math.max(0, Math.floor((la - la0) / res))), Math.min(nx - 1, Math.max(0, Math.floor((lo - lo0) / res)))];
    const [si, sj] = cell(a.lat, a.lon), [gi, gj] = cell(b.lat, b.lon), s0 = si * nx + sj, goal = gi * nx + gj;
    const ref = Math.min(H[s0], H[goal]), g = new Float32Array(n).fill(Infinity), prev = new Int32Array(n).fill(-1), done = new Uint8Array(n), heap = [];
    const push = (f, x) => { heap.push([f, x]); let i = heap.length - 1; while (i > 0) { const p = (i - 1) >> 1; if (heap[p][0] <= heap[i][0]) break; [heap[p], heap[i]] = [heap[i], heap[p]]; i = p; } };
    const pop = () => { const top = heap[0], last = heap.pop(); if (heap.length) { heap[0] = last; let i = 0; for (;;) { const l2 = 2 * i + 1, r2 = l2 + 1; let m = i; if (l2 < heap.length && heap[l2][0] < heap[m][0]) m = l2; if (r2 < heap.length && heap[r2][0] < heap[m][0]) m = r2; if (m === i) break; [heap[m], heap[i]] = [heap[i], heap[m]]; i = m; } } return top; };
    const hst = (i, j) => Math.hypot((i - gi) * ky, (j - gj) * kx);
    g[s0] = 0; push(hst(si, sj), s0); let found = false, it = 0; const why = [0, 0, 0, 0];
    while (heap.length && it++ < 8e6) { const [, x] = pop(); if (done[x]) continue; done[x] = 1; if (x === goal) { found = true; break; } const i = (x / nx) | 0, j = x % nx;
      for (let di = -1; di <= 1; di++) for (let dj = -1; dj <= 1; dj++) { if (!di && !dj) continue; const ii = i + di, jj = j + dj; if (ii < 0 || jj < 0 || ii >= ny || jj >= nx) continue; const m = ii * nx + jj; if (done[m]) continue;
        if (blk[m] && m !== goal) { why[blk[m]]++; continue; }
        const d = Math.hypot(di * ky, dj * kx), c = d * (1 + 3 * Math.max(0, H[m] - ref) / 1000 + 2 * under[m]);
        if (g[x] + c < g[m]) { g[m] = g[x] + c; prev[m] = x; push(g[m] + hst(ii, jj), m); } } }
    if (!found) { const r = [];
      if (why[3]) r.push("cloud reaches the flight level in the valleys");
      if (why[1]) r.push(`the higher ground needs more than ${Math.round(m2ft(capM)).toLocaleString()} ft (${rules.vfrMaxM != null && rules.vfrMaxM < ft2m(ac.ceil) ? "FL150 for VFR" : "the aircraft limit"})`);
      if (why[2]) r.push("part of the area has no satellite data");
      const w = r.join("; and ") || "no connected valley";
      const tip = rules.mode === "vfr" && why[1] && rules.vfrMaxM < ft2m(ac.ceil) ? " Civil visual flights may not go above 15,000 ft (FL150). If this is a military helicopter, set Flight rules to \u201cMilitary / State aircraft\u201d." : why[3] ? " Satellites can't see under cloud, so a valley under cloud is never treated as clear." : "";
      return { error: `No low-level path from ${a.name} to ${b.name} at ${Math.round(u.aglFt)} ft above the ground with ${latM} m from the valley walls: ${w}.${tip}` }; }
    const cells = []; for (let x = goal; x !== -1; x = prev[x]) cells.push(x); cells.reverse();
    for (const x of (l ? cells.slice(1) : cells)) { const i = (x / nx) | 0, j = x % nx;
      pts.push({ lat: la0 + (i + .5) * res, lon: lo0 + (j + .5) * res, z: z[x], wall: wall[x], room: room[x] + marginM, Hreq: H[x], under: !!under[x], leg: l }); }
  }
  // distances, track
  let dist = 0; pts.forEach((p, i) => { if (i) dist += hav([pts[i - 1].lat, pts[i - 1].lon], [p.lat, p.lon]); p.km = dist; p.trk = i ? bearing([pts[i - 1].lat, pts[i - 1].lon], [p.lat, p.lon]) : 0; });
  // vertical profile with climb/descent limits at low-level speed
  const N = pts.length, H = pts.map(p => p.Hreq), stepS = i => i ? (pts[i].km - pts[i - 1].km) * 1000 / vms : 0;
  const climbM = (i, h) => Math.min(u.climbFpm, rocAt(ac, m2ft(h))) * FT / 60 * stepS(i), descM = i => u.descFpm * FT / 60 * stepS(i);
  for (let pass = 0; pass < 2; pass++) {
    for (let i = N - 2; i >= 0; i--) H[i] = Math.max(H[i], H[i + 1] - climbM(i + 1, H[i + 1]));
    for (let i = 1; i < N; i++) H[i] = Math.max(H[i], H[i - 1] - descM(i)); }
  const events = [];
  // departure: climbing from the ground at A, how high must we be before leaving? (orbit over A)
  let need = 0, acc = pts[0].z; for (let i = 1; i < N; i++) { acc += climbM(i, acc); need = Math.max(need, H[i] - acc); }
  if (need > 30) events.push({ type: "orbit-up", i: 0, from: Math.round(m2ft(pts[0].z) / 100) * 100, to: Math.ceil(m2ft(pts[0].z + need + aglM) / 100) * 100 });
  let needB = 0, accB = pts[N - 1].z; for (let i = N - 2; i >= 0; i--) { accB += descM(i + 1); needB = Math.max(needB, H[i] - accB); }
  if (needB > 30) events.push({ type: "orbit-down", i: N - 1, from: Math.ceil(m2ft(pts[N - 1].z + needB) / 100) * 100, to: Math.round(m2ft(pts[N - 1].z) / 100) * 100 });
  pts.forEach((p, i) => { p.H = H[i]; p.alt = m2ft(H[i]); p.agl = m2ft(H[i] - p.z); p.turn = p.room < H[i]; });
  // valley width at flight altitude (every ~0.5 km), from the terrain grid along the cross-track line
  const zAt = (la, lo) => { const v = getZ(la, lo); return v == null ? 9000 : v; };
  pts.forEach((p, i) => { if (i % 6 && i !== N - 1) return; const t = (p.trk + 90) * D; let w = 0;
    for (const sgn of [1, -1]) for (let k = 1; k <= 60; k++) { const dm = k * 45 * sgn, la = p.lat + dm * Math.cos(t) / 111320, lo = p.lon + dm * Math.sin(t) / (111320 * Math.cos(p.lat * D));
      if (zAt(la, lo) + marginM >= p.H) break; w += 45; }
    p.width = w; });
  const out = { low: true, pts, dist, rules, ac, u, events, turnR, roomM, marginM };
  // timing
  out.flightMin = dist * 1000 / vms / 60 + events.reduce((a, e) => a + Math.abs(e.to - e.from) / (e.type === "orbit-up" ? Math.min(u.climbFpm, rocAt(ac, e.to)) : u.descFpm), 0);
  out.checks = evaluateLow(out, wx, imageTime, depTime);
  return out;
}

function evaluateLow(P, wx, imageTime, depTime) {
  const { pts, rules, ac, u } = P, C = [], add = (id, status, rule, detail, src) => C.push({ id, status, rule, detail, src }), f = n => Math.round(n).toLocaleString();
  const runs = pred => { const r = []; let cur = null; for (const p of pts) { if (pred(p)) { if (cur && p.km - cur.b <= .3) cur.b = p.km; else { cur = { a: p.km, b: p.km }; r.push(cur); } } } return r; };
  const fr = r => r.slice(0, 5).map(x => x.b - x.a < .2 ? `km ${x.a.toFixed(1)}` : `km ${x.a.toFixed(1)}–${x.b.toFixed(1)}`).join(", ") + (r.length > 5 ? ` and ${r.length - 5} more` : "");
  const src = rules.mode === "mil" ? SRC.SOP : SRC.VFR_HT;
  add("lowterrain", "PASS", "Height above the ground and distance from the valley walls",
    `Flies at least ${f(u.aglFt)} ft above the ground, plus ${f(u.demMarginFt)} ft for terrain-data error, and keeps ${f(u.latM)} m from ground that rises to the same height. Lowest height above the valley floor on the route: ${f(Math.min(...pts.map(p => p.agl)))} ft.`, src);
  if (rules.mode === "vfr") add("lowlegal", u.aglFt >= 500 ? "PASS" : "FAIL", "VFR minimum height", u.aglFt >= 500 ? "500 ft above the ground or higher, as required away from settlements." : "Below 500 ft above the ground is not permitted for VFR without authorisation.", SRC.VFR_HT);
  add("settle", "NOT_CHECKED", "Settlements under the route", "Over villages and towns the VFR minimum is 1,000 ft above the highest obstacle within 600 m. Settlements are not in this data: check the route on a map.", SRC.VFR_HT);
  const hi = Math.max(...pts.map(p => p.H)), capM = Math.min(ft2m(ac.ceil), rules.vfrMaxM ?? Infinity);
  add("lowcap", hi <= capM + 1 ? "PASS" : "FAIL", rules.vfrMaxM != null ? "Within FL150 and the aircraft's limit" : "Within the aircraft's limit", `Highest altitude ${f(m2ft(hi))} ft.`, rules.vfrMaxM != null ? SRC.VFR_AIP : SRC.AC);
  const noTurn = runs(p => !p.turn).filter(r => r.b - r.a >= .3);
  add("turn", noTurn.length ? "CAUTION" : "PASS", "Room to turn back",
    noTurn.length ? `No room for a 180° turn at ${Math.round(u.lowKt)} kt (needs about ${f(2 * P.roomM)} m clear at your height) at ${fr(noTurn)}. Decide to continue or turn back before each of these stretches.` :
      `A 180° turn at ${Math.round(u.lowKt)} kt (about ${f(2 * P.turnR)} m across at 30° bank, plus ${f(u.latM)} m each side) fits along the whole route.`, "Turn radius at 30° bank: v² / (g·tan 30°)");
  const und = pts.filter(p => p.under).length, undKm = runs(p => p.under);
  add("lowcloud", und ? "CAUTION" : "PASS", "Cloud in or over the valleys",
    und ? `About ${f(und * .09)} km is under or beside cloud the satellite can't see beneath (pixel 4 km): the valley may be closed in at ${fr(undKm)}. Confirm with a met report or ceilometer before committing.` :
      "No cloud detected within 2 km of the route in the latest image. Fog or cloud smaller than the satellite's 4 km pixel can still be missed.", "PassCast satellite data");
  add("wires", "CAUTION", "Wires, cables and ropeways", "Not in any terrain data. Valleys often have power lines and ropeways crossing them at low height: brief them from local knowledge and charts.", "—");
  add("vwind", "NOT_CHECKED", "Valley winds and downdrafts", "Forecast models work at about 10 km and do not resolve winds inside valleys.", "—");
  const climbs = P.events.filter(e => e.type === "orbit-up");
  add("lowperf", "PASS", "Climbs within aircraft performance", `Climbs at no more than ${f(u.climbFpm)} ft/min (or the aircraft's estimated maximum at that height) at ${Math.round(u.lowKt)} kt.${climbs.length ? ` Needs a climb over the departure to ${f(climbs[0].to)} ft first.` : ""}`, SRC.AC);
  // oxygen
  let t10 = 0, t13 = 0; pts.forEach((p, i) => { if (!i) return; const m = (p.km - pts[i - 1].km) * 1000 / (u.lowKt * .51444) / 60; if (p.alt > 10000) t10 += m; if (p.alt > 13000) t13 += m; });
  if (t13 > 0) add("oxy", u.oxygen ? "PASS" : "FAIL", "Supplemental oxygen", `${Math.round(t13)} min above 13,000 ft: oxygen needed for everyone${u.oxygen ? " (marked as carried)" : "; not marked as carried"}.`, SRC.OXY);
  else if (t10 > 30) add("oxy", u.oxygen ? "PASS" : "CAUTION", "Supplemental oxygen", `${Math.round(t10)} min between 10,000 and 13,000 ft.`, SRC.OXY);
  // shared tail: daylight, forecast, picture age
  const fake = { S: pts.map(p => ({ ...p, zg: p.z, zR: p.wall, cl: [], trueM: p.H })), rules, ac, u, prof: null, dist: P.dist, fail: null, planned: true, flightMin: P.flightMin, gsKmh: pts.map(() => u.lowKt * 1.852), dev: null, corrAt: () => 0 };
  const tail = evaluate(fake, wx, imageTime, depTime).filter(c => ["day", "vis", "wx", "wave", "hover0", "hover" + (pts.length - 1), "age"].includes(c.id));
  return C.concat(tail);
}

/* ---------------- plain-language summary ---------------- */
const PLAIN = {
  profile: "No height to fly at keeps you clear of the ground, the cloud and the limits all the way.",
  data: "The satellite has no usable picture for part of the route.",
  terrain: "At your planned height you would be too close to the ground in places.",
  ceiling: "The route needs more height than this aircraft can reach.",
  vfrmax: "Civil visual flights may not go above 15,000 ft (FL150), and this route needs more.",
  vmc: "You would be in or too close to cloud.",
  ice: "You would be inside cloud where it is below freezing, so ice could form on the aircraft.",
  oxy: "You would be high enough to need oxygen, and it is not marked as carried.",
  day: "Civil visual flights are only allowed from 20 min before sunrise to 20 min after sunset.",
  vis: "The forecast visibility is below the minimum for visual flight.",
  lowlegal: "Civil visual flights must stay at least 500 ft above the ground.",
  lowcap: "The valleys still need more height than is allowed.",
};
function plainSummary(checks, P) {
  const f = checks.filter(c => c.status === "FAIL"), out = [];
  for (const c of f) { let t = PLAIN[c.id] || c.rule;
    if (c.id === "profile" && P && P.fail && P.fail.code === "endhigh") t = `The ${P.fail.why.replace(/^the /, "")}.`;
    out.push(t); }
  const tips = [];
  if (P && P.rules && P.rules.mode === "vfr" && f.some(c => ["vfrmax", "profile", "lowcap"].includes(c.id)) && P.fail && /FL150|15,000/.test(P.fail.why || ""))
    tips.push("For a military helicopter, set Flight rules to “Military / State aircraft”: the 15,000 ft limit is a civil rule.");
  if (f.some(c => c.id === "oxy")) tips.push("Tick “Oxygen carried for everyone on board” if you carry it.");
  if (f.some(c => c.id === "day")) tips.push("Change the departure time to daylight.");
  if (f.some(c => ["profile", "vmc", "lowcap"].includes(c.id)) && !tips.length) tips.push("Try “Suggest a valley route”, a different time, or the low-level option.");
  return { reasons: out, tips };
}

const GLOSSARY = [
  ["GO / CAUTION / NO-GO", "1 = every rule this tool checks is met. 0.5 = nothing broken, but something needs your judgement or couldn't be checked. 0 = at least one rule is broken."],
  ["VFR / IFR", "Visual flight (pilot looks outside, must stay clear of cloud) / instrument flight (may fly in cloud, needs approved aircraft, crew and routes)."],
  ["FL150", "Flight level 150, about 15,000 ft. Civil visual flights in India may not go higher."],
  ["VMC", "Weather good enough for visual flight: enough visibility and enough distance from cloud."],
  ["Terrain clearance", "How far above the highest ground near your track you must stay."],
  ["Above ground (AGL)", "Height above the ground directly below, not above sea level."],
  ["QNH", "Altimeter setting that makes it read height above sea level."],
  ["Semicircular rule", "Flying east you use odd thousands of feet, flying west even (plus 500 ft for visual flights), so traffic in opposite directions is separated."],
  ["Orbit climb", "Climbing in circles over one spot until high enough to continue."],
  ["Commit point", "The last place you can still turn back; after it the valley is too narrow to turn."],
  ["HOGE", "Hover out of ground effect: the highest place the helicopter can hover well above the ground at its weight."],
  ["Density altitude", "How high the air 'feels' to the engine and rotor; hot air makes it higher and performance worse."],
  ["Freezing level", "The height where the temperature drops to 0 °C; in cloud above it, ice can form."],
  ["NOTAM / ADC", "Notices of temporary airspace changes / Air Defence Clearance needed to fly in Indian air defence zones."],
];

const API = { plainSummary, GLOSSARY, planLow, evaluateLow, SRC, AIRCRAFT, BAD, rulesFor, hav, bearing, sunTimes, levelOK, nearBoundary, rocAt, sampleRoute, buildRaster, cellsWithin, prepare,
  makeChecker, optimise, fixedProfile, plan, evaluate, verdict, notChecked, levAt, wxNear, groundSpeedKt, m2ft, ft2m };
if (typeof module !== "undefined" && module.exports) module.exports = API; else root.Planner = API;
})(typeof window !== "undefined" ? window : globalThis);
