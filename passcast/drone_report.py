"""Mobile-first HTML card for the drone planner."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path


def write_drone_card(path, site_name, lat, lon, drone, height_m, verdicts, planned, windows, live=None):
    data = {
        "site": site_name, "lat": lat, "lon": lon, "drone": drone.name,
        "max_wind": drone.max_wind_ms, "height": height_m,
        "planned": planned.time_ist if planned else None,
        "hours": [asdict(v) for v in verdicts], "windows": windows, "live": live,
    }
    Path(path).write_text(CARD.replace("/*__DATA__*/null", json.dumps(data)), encoding="utf-8")
    return path


CARD = r"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Drone flight check</title>
<style>
:root{--bg:#0d1117;--p:#151b23;--l:#263040;--t:#e6edf3;--m:#8b98a9;--GO:#1f9d55;--CAUTION:#e3a008;--NOGO:#d64545}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--t);font:15px/1.45 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
.w{max-width:760px;margin:0 auto;padding:16px}
h1{font-size:19px;margin:0}.s{color:var(--m);font-size:13px}
.v{border-radius:14px;padding:16px;margin:14px 0;display:flex;gap:14px;align-items:center}
.v .b{font-size:26px;font-weight:800;letter-spacing:.5px}
.v ul{margin:4px 0 0 18px;padding:0}
.g{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}@media(max-width:560px){.g{grid-template-columns:repeat(2,1fr)}}
.g div{background:var(--p);border:1px solid var(--l);border-radius:10px;padding:8px 10px}.g b{display:block;font-size:17px}.g span{color:var(--m);font-size:12px}
h2{font-size:14px;color:var(--m);margin:20px 0 8px;font-weight:600}
.tl{display:flex;gap:2px;overflow-x:auto;padding-bottom:6px}
.c{min-width:30px;flex:1;border-radius:4px;height:46px;cursor:pointer;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;font-size:10px;color:#fff;padding-bottom:3px;position:relative}
.c.n{opacity:.55}.c.sel{outline:2px solid #fff}
.day{font-size:11px;color:var(--m);display:flex;gap:2px;overflow-x:auto}
.win{background:var(--p);border:1px solid var(--l);border-radius:10px;padding:10px;margin-bottom:8px}
.note{color:var(--m);font-size:12.5px;border-top:1px solid var(--l);margin-top:18px;padding-top:10px}
.live{background:var(--p);border:1px solid var(--l);border-radius:10px;padding:10px;margin-top:8px;font-size:13.5px}
</style></head><body><div class="w">
<h1 id="h"></h1><div class="s" id="sub"></div>
<div class="v" id="v"></div>
<div class="g" id="g"></div>
<div id="live"></div>
<h2>Next hours <span class="s">(tap an hour)</span></h2>
<div class="tl" id="tl"></div>
<h2>Best windows</h2><div id="wins"></div>
<div class="note">Forecast for planning, not a guarantee: weather can change faster than models predict, especially in hills and during the monsoon. Airspace is not checked: verify green/yellow/red zones on the Digital Sky platform before every flight. Wind ratings are manufacturer figures; check your manual.</div>
</div><script>
const D=/*__DATA__*/null;const $=s=>document.querySelector(s);
const col=v=>v=="GO"?"var(--GO)":v=="CAUTION"?"var(--CAUTION)":"var(--NOGO)";
$("#h").textContent=D.site;$("#sub").textContent=`${D.drone} · rated ${D.max_wind} m/s · planned height ${D.height} m · ${D.lat.toFixed(3)}, ${D.lon.toFixed(3)}`;
function fmtT(t){const d=new Date(t);return d.toLocaleDateString("en-IN",{weekday:"short",day:"numeric",month:"short"})+", "+d.toLocaleTimeString("en-IN",{hour:"2-digit",minute:"2-digit"})}
let sel=D.hours.findIndex(h=>h.time_ist==D.planned);if(sel<0)sel=0;
function show(i){const h=D.hours[i];sel=i;
 $("#v").style.background=col(h.verdict);$("#v").innerHTML=`<div><div class="b">${h.verdict}</div><div>${fmtT(h.time_ist)}${h.time_ist==D.planned?" · your planned time":""}</div><ul>${h.reasons.map(r=>`<li>${r}</li>`).join("")}</ul></div>`;
 const t=[["Wind at "+D.height+" m",h.wind_ms+" m/s"],["Gusts",h.gust_ms+" m/s"],["Rain",h.rain_mm+" mm · "+Math.round(h.rain_prob)+"%"],["Visibility",h.visibility_m==null?"–":(h.visibility_m/1000).toFixed(1)+" km"],["Cloud base",h.cloud_base_agl_m==null?"–":(h.low_cloud_pct<50?"no low cloud":"~"+h.cloud_base_agl_m+" m")],["Low cloud",Math.round(h.low_cloud_pct)+"%"],["Temperature",h.temp_c==null?"–":Math.round(h.temp_c)+" °C"],["Kp (GPS)",h.kp==null?"–":h.kp.toFixed(1)]];
 $("#g").innerHTML=t.map(([k,v])=>`<div><b>${v}</b><span>${k}</span></div>`).join("");
 document.querySelectorAll(".c").forEach((c,j)=>{c.classList.toggle("sel",j==i);if(j==i)c.scrollIntoView({block:"nearest",inline:"center"})})}
$("#tl").innerHTML=D.hours.map((h,i)=>{const d=new Date(h.time_ist);return `<div class="c ${h.is_day?"":"n"}" style="background:${col(h.verdict)}" title="${fmtT(h.time_ist)}" data-i="${i}">${d.getHours()==0?'<b>'+d.getDate()+'</b>':""}${d.getHours()}</div>`}).join("");
document.querySelectorAll(".c").forEach(c=>c.onclick=()=>show(+c.dataset.i));
$("#wins").innerHTML=D.windows.length?D.windows.map(w=>`<div class="win"><b>${fmtT(w.start)}</b> → ${new Date(w.end).getHours()+1}:00 · ${w.hours} h of GO · max wind ${w.max_wind_ms} m/s</div>`).join(""):'<div class="win">No daylight GO window in the forecast period.</div>';
if(D.live)$("#live").innerHTML=`<div class="live"><b>Satellite right now:</b> ${D.live}</div>`;
show(sel);
</script></body></html>"""
