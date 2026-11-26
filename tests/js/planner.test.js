// node tests for the flight-planning engine (run: node tests/js/planner.test.js)
const P = require("../../passcast/web_national/planner.js");
const assert = require("assert");
let n = 0; const t = (name, fn) => { fn(); n++; console.log("ok -", name); };
const FT = 0.3048, res = 0.01;

// synthetic world: valleys at 3,000 m, an E-W ridge at lat 34.0 peaking 5,200 m with a 4,300 m pass at lon 77.0
function world(opts = {}) {
  return (lat, lon) => {
    const ridge = Math.exp(-(((lat - 34.0) / 0.05) ** 2)), pass = Math.exp(-(((lon - 77.0) / 0.03) ** 2));
    const z = 3000 + ridge * (2200 - 900 * pass);
    const c = { zx: z + 40, z, nodata: false, cloud: false, top: 0, base: 0, unc: false };
    if (opts.cloud && opts.cloud(lat, lon)) Object.assign(c, { cloud: true, top: opts.top || 6500, base: opts.base || 0 });
    return c;
  };
}
const ac = { ...P.AIRCRAFT.find(a => a.id === "dhruv") };
const U = (o = {}) => Object.assign({ mode: "mil", clearFt: 1000, corridorM: 2000, milCloudVertFt: 1000, milCloudHorizM: 1500, milLevels: "vfr", milNight: false,
  climbFpm: 1000, descFpm: 500, tas: 120, oxygen: true, plannedFt: null }, o);
const route = [{ lat: 33.7, lon: 77.0 }, { lat: 34.3, lon: 77.0 }];

t("sunrise in Delhi is ~07:00 IST in late November", () => {
  const s = P.sunTimes(28.6, 77.2, Date.UTC(2026, 10, 26, 6)); const ist = new Date(s.rise + 5.5 * 3600e3);
  assert(ist.getUTCHours() === 6 || ist.getUTCHours() === 7, ist.toISOString());
});
t("semicircular levels", () => {
  assert(P.levelOK(13500, 90, "vfr") && !P.levelOK(14500, 90, "vfr") && P.levelOK(14500, 270, "vfr"));
  assert(P.levelOK(13000, 90, "ifr") && !P.levelOK(13500, 90, "ifr") && P.levelOK(14000, 200, "ifr"));
  assert(P.levelOK(14500, 2, "vfr") && P.levelOK(13500, 2, "vfr"));  // near 000: both accepted (flagged)
});

const plan = (u, w = world(), stops = route, extra = {}) => P.plan(Object.assign({ stops, getCell: w, res, u, ac, wx: null, imageTime: null }, extra));

t("profile clears the ridge with the required clearance and respects climb/descent rates", () => {
  const r = plan(U());
  assert(!r.fail, JSON.stringify(r.fail));
  const S = r.S;
  for (let i = 1; i < S.length - 1; i++) { const p = S[i];
    assert(P.ft2m(p.alt) >= p.zR + p.clear - 1, `terrain at km ${p.km}: ${p.alt} ft vs ${(p.zR + p.clear) / FT}`); }
  const orb = new Set(r.prof.events.map(e => e.i));
  for (let i = 1; i < S.length; i++) { if (orb.has(i)) continue; const dft = S[i].alt - S[i - 1].alt, min = (S[i].km - S[i - 1].km) / r.gsKmh[i] * 60;
    if (dft > 0) assert(dft / min <= Math.max(1000, P.rocAt(ac, S[i].alt)) * 1.01 + 1, `climb ${dft / min} ft/min at km ${S[i].km}`);
    else assert(-dft / min <= 500 * 1.01 + 1, `descent ${-dft / min} ft/min at km ${S[i].km}`); }
  assert(P.verdict(r.checks) <= 0.5);  // forecast missing -> never 1
});

t("no 14,000 ft dive-and-climb: descents never drop through more than the rate allows", () => {
  const r = plan(U());
  const orb = new Set(r.prof.events.map(e => e.i));
  const maxDrop = Math.max(...r.S.slice(1).map((p, i) => orb.has(i + 1) ? 0 : r.S[i].alt - p.alt));
  assert(maxDrop <= 500 / 1 + 1, "max drop per km " + maxDrop);
});

t("level flight uses VFR semicircular levels when high above ground", () => {
  const r = plan(U());
  const bad = r.S.filter(p => p.phase === "level" && !p.levelOK);
  assert(bad.length === 0, bad.length + " km at wrong-parity level");
});

t("civil VFR is capped at FL150: a 17,000 ft ridge is a NO-GO", () => {
  const hi = (lat, lon) => { const c = world()(lat, lon); c.z = c.z * 1.25; c.zx = c.z + 40; return c; };
  const r = plan(U({ mode: "vfr" }), hi);
  assert(r.fail || r.checks.some(c => c.status === "FAIL"));
  assert.strictEqual(P.verdict(r.checks), 0);
});

t("cloud on the pass with 1,000 ft separation blocks VFR; the planner says why", () => {
  const w = world({ cloud: (la, lo) => Math.abs(la - 34) < .06, top: 7200 });
  const r = plan(U({ mode: "vfr" }), w);
  assert(r.fail && /cloud|terrain/.test(r.fail.why), JSON.stringify(r.fail));
  assert.strictEqual(P.verdict(r.checks), 0);
});

t("IFR allows cloud but enforces 2,000 ft / 10 NM and icing", () => {
  const w = world({ cloud: (la, lo) => Math.abs(la - 34) < .06, top: 7200 });
  const wx = { pts: [{ km: 30, t2m: 5, vis: 20000, fzl: 4000, lev: [] }] };
  const r = plan(U({ mode: "ifr", iceCleared: false }), w, route, { wx });
  assert(r.fail || r.checks.some(c => c.id === "ice" && c.status === "FAIL"), "icing should fail");
  const r2 = plan(U({ mode: "ifr", iceCleared: true }), w, route, { wx });
  assert(!r2.fail, JSON.stringify(r2.fail));
  for (let i = 1; i < r2.S.length - 1; i++) { const p = r2.S[i]; assert(P.ft2m(p.alt) - r2.corrAt(p.alt) >= p.zR + p.clear - 1); }
  assert(r2.S[Math.floor(r2.S.length / 2)].clear > P.ft2m(1999), "mid-route clearance is 2,000 ft");
});

t("planned altitude too low fails terrain", () => {
  const r = plan(U({ plannedFt: 14500 }));
  assert(r.checks.some(c => c.id === "terrain" && c.status === "FAIL"));
});

t("Chetak-type ceiling cannot cross the ridge", () => {
  const save = { ...ac }; Object.assign(ac, P.AIRCRAFT.find(a => a.id === "chetak"));
  const r = plan(U()); Object.assign(ac, save);
  assert(r.fail && /terrain/.test(r.fail.why), JSON.stringify(r.fail));
});

t("night VFR fails, day VFR passes the daylight rule", () => {
  const night = Date.UTC(2026, 10, 26, 16, 0), day = Date.UTC(2026, 10, 26, 5, 0);
  const lowW = (la, lo) => ({ zx: 300, z: 280, nodata: false, cloud: false, top: 0, base: 0 });
  const st = [{ lat: 26.8, lon: 80.9 }, { lat: 27.2, lon: 81.3 }];
  const a = plan(U({ mode: "vfr" }), lowW, st, { depTime: night }), b = plan(U({ mode: "vfr" }), lowW, st, { depTime: day });
  assert.strictEqual(a.checks.find(c => c.id === "day").status, "FAIL");
  assert.strictEqual(b.checks.find(c => c.id === "day").status, "PASS");
});

t("oxygen: above 13,000 ft without oxygen is a FAIL", () => {
  const r = plan(U({ oxygen: false }));
  assert.strictEqual(r.checks.find(c => c.id === "oxy").status, "FAIL");
});

t("cold-temperature correction raises the indicated altitude needed", () => {
  const wx = { pts: [{ km: 0, t2m: -15, vis: 20000, fzl: 2000, lev: [] }] };
  const warm = plan(U(), world(), route, { wx: { pts: [{ km: 0, t2m: 20, vis: 20000, fzl: 5000, lev: [] }] } });
  const cold = plan(U(), world(), route, { wx });
  const top = r => Math.max(...r.S.map(p => p.alt));
  assert(top(cold) >= top(warm), `cold ${top(cold)} vs warm ${top(warm)}`);
});
console.log(`${n} tests passed`);
