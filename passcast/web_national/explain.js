/* Plain-words explanations for every technical term, check and setting in PassCast.
   Every term found in the page's text becomes a tappable word that opens a short
   explanation. Written for someone who has never flown. */
(function (root) {
"use strict";

// [pattern, title, explanation]. Longer phrases first so they win over shorter ones.
const TERMS = [
  [/Civil VFR|VFR/g, "VFR (visual flight rules)", "Flying by looking out of the window. The pilot must be able to see the ground and keep away from cloud. Most helicopter flights are like this."],
  [/Civil IFR|IFR/g, "IFR (instrument flight rules)", "Flying by the aircraft's instruments, so you are allowed to go into cloud. Needs a specially equipped aircraft, a pilot trained for it and fixed, published routes."],
  [/Military \/ State aircraft|State aircraft/g, "Military / State aircraft", "Aircraft of the armed forces and government. They follow their own unit orders (SOPs) instead of the civilian rulebook, which is why you enter those limits yourself."],
  [/unit SOP values|unit SOP|SOP/g, "SOP (standard operating procedures)", "The written flying rules of a particular Army or Air Force unit. They are not public, so you type in your unit's numbers."],
  [/FL ?150|flight level 150/gi, "FL150", "“Flight level 150”: about 15,000 feet above sea level. In India, civilian flights that fly by looking outside may not go higher than this."],
  [/flight levels?/gi, "Flight level", "A height read off a standard pressure setting, written without the last two zeros: FL150 is about 15,000 ft."],
  [/VMC minima|VMC/g, "VMC", "Weather good enough to fly by looking outside: you can see far enough (5 km, or 8 km above 10,000 ft) and you stay far enough from cloud (1.5 km sideways, 1,000 ft above or below)."],
  [/semicircular rule|semicircular levels|semicircular/gi, "Semicircular rule", "A rule that keeps aircraft flying in opposite directions at different heights. Heading roughly east you fly at odd thousands of feet (3,000, 5,000, …), heading west at even thousands; visual flights add 500 ft."],
  [/cruising levels?/gi, "Cruising level", "The steady height you fly at between climbs and descents. Which ones you may use depends on your direction (see semicircular rule)."],
  [/terrain clearance/gi, "Terrain clearance", "How far above the highest ground near your path you must stay, for example 1,000 ft."],
  [/minimum altitude/gi, "Minimum altitude", "The lowest height you may fly at a given point: the highest ground nearby plus the required clearance."],
  [/terrain corridor|corridor/gi, "Corridor", "A strip of land on each side of your line. The tool checks the highest ground and the cloud inside this strip, because you never fly exactly on the line."],
  [/above the ground|AGL/g, "Above the ground", "Height measured from the ground directly underneath you, not from sea level."],
  [/AMSL|above sea level/g, "Above sea level", "Height measured from the level of the sea. Mountain heights and aircraft altitudes are usually given this way."],
  [/QNH/g, "QNH", "The setting on the aircraft's height meter (altimeter) that makes it show height above sea level."],
  [/ISA/g, "ISA", "The “standard” textbook temperature for each height (15 °C at sea level, about 2 °C colder every 1,000 ft). Real air is often warmer or colder."],
  [/cold-temperature (?:altimeter )?(?:error|correction)|true altitude/gi, "Cold-air altimeter error", "In very cold air the height meter reads higher than you really are. The tool adds the difference to the safety margin so you are not lower than you think."],
  [/HOGE ceiling|HOGE/g, "HOGE (hover out of ground effect)", "The highest place where the helicopter can hover in mid-air, well above the ground, at its current weight. Above it, it can only hover very close to the ground or not at all."],
  [/in-ground-effect hover|in-ground-effect/gi, "Hover in ground effect", "Hovering very close to the ground, where the air pushed down by the rotor gives extra lift. Easier than hovering high up."],
  [/density altitude/gi, "Density altitude", "How high the air “feels” to the engine and rotor. Hot or high places have thin air, so the helicopter performs as if it were even higher up."],
  [/freezing level/gi, "Freezing level", "The height where the air temperature drops to 0 °C. Inside cloud above this height, ice can build up on the aircraft."],
  [/icing/gi, "Icing", "Ice forming on the aircraft when it flies through cloud below freezing. It adds weight and spoils lift; most helicopters are not allowed to fly in it."],
  [/mountain-wave turbulence|mountain-wave|mountain wave/gi, "Mountain wave", "Strong wind blowing over a ridge makes the air rise and sink in waves on the far side, causing bumpy air and strong downward currents."],
  [/downdrafts?/gi, "Downdraft", "Air moving downwards. Near mountains it can push a helicopter down faster than it can climb."],
  [/orbit climb|orbit/gi, "Orbit climb", "Climbing in circles over one spot until high enough to continue, instead of climbing while moving forward."],
  [/commit point/gi, "Commit point", "The last place where you can still turn back. After it the valley is too narrow to turn around, so you must continue."],
  [/turn-back room|room to turn back|180° turn/gi, "Room to turn back", "Whether the valley is wide enough at your height for the helicopter to turn around (a U-turn) if the weather ahead closes in."],
  [/30° bank/g, "30° bank", "Tilting the aircraft 30 degrees to turn. A normal, comfortable turn; it sets how much space a U-turn needs."],
  [/NOTAMs?/g, "NOTAM", "“Notice to air missions”: official short-term notices about closed airspace, military activity, obstacles and similar."],
  [/Air Defence Clearance|ADC/g, "Air Defence Clearance (ADC)", "Permission from the Air Force to fly in India's air defence zones. Without it an aircraft can be treated as a threat."],
  [/prohibited, restricted and danger areas|restricted airspace/gi, "Prohibited / restricted areas", "Parts of the sky where flying is banned or limited, for example over sensitive sites or near the border."],
  [/AIP India|AIP/g, "AIP", "Aeronautical Information Publication: India's official book of flying rules, airports and airspace, published by the Airports Authority of India."],
  [/ENR \d+(?:\.\d+)?/g, "ENR section", "A chapter of the AIP that covers flying between airports (“en route”)."],
  [/ICAO Annex \d+(?: Part III)?|ICAO Doc 8168(?: Vol I)?|ICAO/g, "ICAO", "The UN agency for civil aviation. Its “Annexes” and “Docs” are the worldwide rulebooks that India's rules follow."],
  [/DGCA CAR|DGCA/g, "DGCA", "Directorate General of Civil Aviation: India's civil aviation regulator. A CAR is one of its rule documents."],
  [/EASA CAT\.IDE\.H\.240|EASA/g, "EASA", "The European aviation regulator, quoted here only as a comparison for the same rule."],
  [/FAA AIM 7-5-6|AC 00-57|FAA/g, "FAA", "The US aviation regulator. Its mountain-flying guidance is used here as a rule of thumb."],
  [/flight manual|RFM|ODM/g, "Flight manual", "The official book for each aircraft type with its limits and performance charts. Its numbers always beat the ones in this tool."],
  [/service ceiling|max(?:imum)? operating altitude|aircraft limit/gi, "Maximum altitude", "The highest the aircraft can (or may) go. Real performance is lower when it is heavy or the air is warm."],
  [/climb rate|max climb|ft\/min/gi, "Climb / descent rate", "How many feet per minute the aircraft goes up or down. A helicopter climbs more slowly the higher it is."],
  [/true airspeed|TAS/g, "True airspeed", "How fast the aircraft moves through the air. Wind makes speed over the ground faster or slower."],
  [/ground speed/gi, "Ground speed", "Speed over the ground: airspeed plus or minus the wind."],
  [/\bkt\b|knots?/g, "Knot (kt)", "Aviation speed unit: one nautical mile per hour, about 1.85 km/h. 70 kt is about 130 km/h."],
  [/\bNM\b|nautical miles?/g, "Nautical mile (NM)", "Aviation distance unit, 1.852 km. 10 NM is about 18.5 km."],
  [/supplemental oxygen|oxygen/gi, "Oxygen", "Above about 10,000 ft the air is thin enough to affect thinking; above 13,000 ft everyone on board must breathe extra oxygen."],
  [/MEA\/MOCA|MEA|MOCA/g, "MEA / MOCA", "Published safe heights for official air routes. Instrument flights must stay at or above them."],
  [/ATS routes?/g, "Air routes", "Official “roads in the sky” with fixed waypoints, used mainly by airliners and instrument flights."],
  [/satellite pixel|pixel/gi, "Satellite pixel", "The smallest patch the weather satellite can see as one dot, about 4 km across. Anything smaller, like cloud inside a narrow valley, is blurred."],
  [/ceilometers?/gi, "Ceilometer", "A ground instrument that shines a laser up to measure how high the bottom of the cloud is."],
  [/cloud base/gi, "Cloud base", "The height of the bottom of the cloud. Satellites only see the top, so the base is estimated."],
  [/cloud top/gi, "Cloud top", "The height of the top of the cloud, which the satellite measures from its temperature."],
  [/above a cloud layer|above cloud/gi, "Flying above cloud", "Flying over a layer of cloud. You can't see the ground below, and if you need to come down you may have to go through the cloud."],
  [/terrain-data margin|terrain-data error|terrain data/gi, "Terrain data", "A digital map of ground heights from satellite radar. It can be off by tens of metres on steep slopes, so the tool adds a safety margin."],
  [/Copernicus(?: GLO-90| DEM)?|GLO-90|90 m terrain|DEM/g, "Terrain map (DEM)", "Digital elevation model: a map of ground heights in squares (here 90 m). Made by the European Copernicus programme."],
  [/Open-Meteo/g, "Open-Meteo", "A free weather-forecast service built on computer weather models. Useful, but not an official met office briefing."],
  [/IMD|METAR|TAF|SIGMET/g, "Official weather", "IMD is India's weather department. METAR = current airport weather report, TAF = airport forecast, SIGMET = warning of dangerous weather."],
  [/Himawari|INSAT(?:-3DR|-3DS)?/g, "Weather satellite", "Satellites 36,000 km up that photograph clouds every 10–30 minutes. INSAT is India's; Himawari is Japan's."],
  [/IST/g, "IST", "Indian Standard Time."],
  [/°T/g, "°T (true track)", "Direction of travel in degrees from true north: 0 = north, 90 = east, 180 = south, 270 = west."],
  [/GPX|KML/g, "GPX / KML", "File formats for routes and waypoints that most navigation apps and GPS devices can read."],
  [/NO-GO/g, "NO-GO (0)", "At least one rule is broken. Don't fly this plan as it is."],
  [/CAUTION/g, "CAUTION (0.5)", "No rule is broken, but something needs your judgement or could not be checked."],
  [/\bGO\b/g, "GO (1)", "Every rule this tool can check is met. You still have to check the items it lists as not checked."],
  [/NOT CHECKED/g, "NOT CHECKED", "The tool has no data for this item. You must check it yourself."],
];

// what each checklist item means, in plain words
const CHECK = {
  profile: "Can the tool find a height to fly at that stays clear of the ground, out of the cloud and within the limits all the way?",
  data: "Does the satellite have a usable picture of the whole route?",
  terrain: "Do you stay high enough above the hills and mountains near your path?",
  ceiling: "Does the route need more height than this aircraft can reach?",
  vfrmax: "Civilian visual flights may not go above about 15,000 ft. Does the route stay below that?",
  vmc: "Do you stay far enough away from cloud to keep seeing where you're going?",
  ontop: "Would you be flying over a layer of cloud with the ground hidden below?",
  ice: "Would you be inside cloud where it's below freezing, so ice could form on the aircraft?",
  ifrroute: "On official air routes, instrument flights must also follow the published safe heights.",
  levels: "Are you flying at the right heights for your direction, so you don't meet traffic coming the other way at the same height?",
  perf: "Are the climbs and descents gentle enough for this aircraft to actually fly?",
  oxy: "Will you be high enough to need extra oxygen, and is it on board?",
  day: "Civilian visual flights are only allowed in daylight. Does the flight finish before dark?",
  wx: "The weather forecast couldn't be loaded, so these weather items weren't checked.",
  vis: "Will you be able to see far enough (5 km, or 8 km when high up)?",
  fz: "Where does it get cold enough for ice? If you accidentally enter cloud above this height, ice can form.",
  cold: "In very cold air the height meter reads too high. This shows how much, and that it's already allowed for.",
  wave: "Is the wind strong enough over the ridges to cause bumpy air and strong sinking air behind them?",
  hover: "Can the helicopter hover at the landing or take-off spot, given the height and temperature there?",
  age: "How old will the satellite cloud picture be at your departure time? Cloud changes; re-check close to take-off.",
  quality: "How much can the satellite picture be trusted here (slanted view, night, thin cloud and so on)?",
  dem: "How detailed is the ground-height map along your route?",
  lowterrain: "Are you always at your chosen height above the ground, and far enough from the valley walls?",
  lowlegal: "Civilian flights must stay at least 500 ft above the ground. Are you above that?",
  settle: "Over villages and towns you must fly higher (1,000 ft). The tool doesn't know where they are.",
  lowcap: "Do the valleys stay below the height limit for these rules and this aircraft?",
  turn: "Is the valley wide enough to do a U-turn if the weather ahead gets bad? Where it isn't, you must decide before you go in.",
  lowcloud: "Is there cloud in or over the valleys? The satellite can't see under cloud inside a narrow valley.",
  wires: "Power lines and ropeways across valleys are not on any map this tool uses.",
  vwind: "Winds inside valleys can be strong and change quickly; forecasts can't see them at this scale.",
  lowperf: "Can the aircraft climb fast enough at low speed to follow the valley floor up to the passes?",
};

// plain help under each setting
const HELP = {
  fr: "Which rulebook applies: civilian flying by looking outside (VFR), civilian flying on instruments (IFR), or military.",
  dep: "When you plan to take off, in Indian time. Used for daylight, forecast and how old the cloud picture will be.",
  ac: "The helicopter type. This fills in its typical limits, which you can correct below.",
  alt: "Leave empty and the tool works out the heights for you. Or type the height you want to fly at, in feet above sea level.",
  clr: "How far above the highest ground near your path to stay. More is safer.",
  oxy: "Tick if everyone on board has oxygen. It's needed when flying high.",
  rw: "How wide a strip on each side of your line is checked for high ground.",
  ceil: "The highest this aircraft can or may go.",
  roc: "How fast it can climb, in feet per minute, near sea level. It climbs more slowly higher up.",
  climb: "How fast you plan to climb, in feet per minute.",
  desc: "How fast you plan to come down, in feet per minute. 500 is comfortable for passengers.",
  tas: "Normal cruising speed through the air, in knots (1 knot ≈ 1.85 km/h).",
  hoge: "The highest place this helicopter can hover in mid-air at today's weight (from its flight manual). Optional.",
  scv: "How far above or below cloud your unit's rules require you to stay.",
  sch: "How far sideways from cloud your unit's rules require you to stay.",
  slv: "Which set of heights you fly at for each direction.",
  snt: "Tick if your unit allows this flight at night.",
  ice: "Tick only if the aircraft is approved to fly in icing conditions.",
  low: "Fly low along the valleys between the peaks instead of high over them.",
  agl: "How high above the ground directly below you want to stay. 500 ft is the civilian minimum.",
  latm: "How far to stay from valley walls and slopes at your height.",
  lkt: "Your speed while flying low. Slower needs less room to turn around.",
  dmg: "Extra height added because the ground-height map can be wrong by tens of metres on steep slopes.",
};

function esc(s) { return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
// escape plain text and turn known terms into tappable words (each term keeps its own case rule;
// earlier, longer phrases win because matches are swapped for placeholders as we go)
function words(text) {
  let s = esc(text); const found = [];
  TERMS.forEach(([re], i) => { s = s.replace(new RegExp(re.source, re.flags), m => { found.push([i, m]); return `\u0000${found.length - 1}\u0001`; }); });
  return s.replace(/\u0000(\d+)\u0001/g, (_, k) => `<button type="button" class="term" data-t="${found[k][0]}">${found[k][1]}</button>`);
}
// apply to text nodes inside an element (labels, legends, results)
function linkify(el) {
  const skip = "button,select,option,script,style,svg,.term,input,textarea,a,h1,#tip";
  const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT, { acceptNode: n => n.parentElement && !n.parentElement.closest(skip) && n.nodeValue.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT });
  const nodes = []; while (walk.nextNode()) nodes.push(walk.currentNode);
  for (const n of nodes) { const h = words(n.nodeValue); if (h === esc(n.nodeValue)) continue; const span = document.createElement("span"); span.innerHTML = h; n.replaceWith(span); }
}
function show(i) {
  let tip = document.getElementById("tip");
  if (!tip) { tip = document.createElement("div"); tip.id = "tip"; tip.setAttribute("role", "dialog"); document.body.appendChild(tip); }
  const t = TERMS[i]; tip.innerHTML = `<b>${esc(t[1])}</b><p>${esc(t[2])}</p><button type="button" class="tipx">Got it</button>`; tip.hidden = false;
  tip.querySelector(".tipx").onclick = () => tip.hidden = true;
}
if (typeof document !== "undefined") document.addEventListener("click", e => { const b = e.target.closest(".term"); if (b) { e.preventDefault(); e.stopPropagation(); show(+b.dataset.t); return; }
  const tip = document.getElementById("tip"); if (tip && !tip.hidden && !e.target.closest("#tip")) tip.hidden = true; }, true);
function addHelp() {
  for (const [id, h] of Object.entries(HELP)) { const el = document.getElementById(id); if (!el) continue; const lab = el.closest("label"); if (!lab || lab.querySelector(".help")) continue;
    const s = document.createElement("small"); s.className = "help"; s.textContent = h; lab.appendChild(s); }
}
// watch results and controls: every time new text appears, link its terms
function start() {
  addHelp(); ["routeCtl", "out", "intro"].forEach(id => { const el = document.getElementById(id); if (el) linkify(el); });
  document.querySelectorAll(".legend").forEach(linkify);
  const out = document.getElementById("out"); if (!out) return;
  const obs = new MutationObserver(() => { obs.disconnect(); linkify(out); obs.observe(out, { childList: true, subtree: true }); });
  obs.observe(out, { childList: true, subtree: true });
}
const API = { TERMS, CHECK, HELP, words, linkify, start };
if (typeof module !== "undefined" && module.exports) module.exports = API; else { root.Explain = API; if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start(); }
})(typeof window !== "undefined" ? window : globalThis);
