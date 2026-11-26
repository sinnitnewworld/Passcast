"""Outputs: JSON report, PNG maps and a self-contained HTML dashboard."""
from __future__ import annotations

import base64
import datetime as dt
import json
import math
from pathlib import Path

import numpy as np

from .downscale import CLASS_NAMES

SCORE_COLOURS = {1.0: "#1f9d55", 0.5: "#e3a008", 0.0: "#d64545"}


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating,)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, float) and not math.isfinite(o):
        return None
    if isinstance(o, np.integer):
        return int(o)
    return o


def write_json(products, scene, path, extra=None):
    data = {
        "product": "PassCast pass flyability",
        "generated_utc": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "image_time_utc": scene.time.isoformat() + "Z",
        "satellite": scene.platform,
        "scale": {"1": "clear", "0.5": "partially clear", "0": "not clear"},
        "passes": [p.result.to_dict() for p in products],
    }
    if extra:
        data.update(extra)
    Path(path).write_text(json.dumps(_clean(data), indent=2))
    return path


def write_pngs(products, outdir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle
    out = []
    for prod in products:
        f, r = prod.field, prod.result
        hs = f.dem.hillshade()
        ext = [f.dem.lon[0], f.dem.lon[-1], f.dem.lat[0], f.dem.lat[-1]]
        fig, ax = plt.subplots(figsize=(7, 6.4), dpi=130)
        ax.imshow(hs, cmap="gray", origin="lower", extent=ext, vmin=0, vmax=1)
        rgba = np.zeros(hs.shape + (4,))
        rgba[..., :3] = 1.0
        rgba[..., 3] = 0.15 * f.prob_column + 0.7 * f.prob_cell
        ax.imshow(rgba, origin="lower", extent=ext)
        ax.contour(f.dem.lon, f.dem.lat, f.dem.z, levels=np.arange(2000, 8000, 500),
                   colors="k", linewidths=0.3, alpha=0.4)
        rlon = r.lon if hasattr(r, "lon") else None
        k = 111000.0
        ax.add_patch(Circle((r.lon, r.lat), 3000 / (k * math.cos(math.radians(r.lat))),
                            fill=False, ec=SCORE_COLOURS[r.score], lw=2.2))
        ax.plot(r.lon, r.lat, marker="^", color=SCORE_COLOURS[r.score], ms=10, mec="k")
        ax.set_title(f"{r.name}  score {r.score:g} ({r.label})  conf {r.confidence_label}", fontsize=10)
        ax.set_xlabel("lon"); ax.set_ylabel("lat")
        p = Path(outdir) / f"{r.name.replace(' ', '_')}.png"
        fig.tight_layout(); fig.savefig(p); plt.close(fig)
        out.append(p)
    return out


def _b64(a, dtype):
    return base64.b64encode(np.ascontiguousarray(a.astype(dtype)).tobytes()).decode()


def _pack_field(prod, max_n=220):
    f = prod.field
    ny, nx = f.dem.z.shape
    s = max(1, int(math.ceil(max(ny, nx) / max_n)))
    sl = (slice(None, None, s), slice(None, None, s))
    z = f.dem.z[sl]
    cth = np.nan_to_num(f.cth_m[sl], nan=0)
    return {
        "ny": int(z.shape[0]), "nx": int(z.shape[1]),
        "lat0": float(f.dem.lat[0]), "lat1": float(f.dem.lat[::s][-1]),
        "lon0": float(f.dem.lon[0]), "lon1": float(f.dem.lon[::s][-1]),
        "zmin": float(z.min()), "zmax": float(z.max()),
        "z": _b64(np.clip(z, 0, 9000), "<u2"),
        "pcol": _b64(np.clip(f.prob_column[sl] * 255, 0, 255), "u1"),
        "pcell": _b64(np.clip(f.prob_cell[sl] * 255, 0, 255), "u1"),
        "cls": _b64(f.cls[sl], "u1"),
        "snow": _b64(np.clip(f.snow[sl] * 255, 0, 255), "u1"),
        "cth": _b64(np.clip(cth, 0, 20000), "<u2"),
    }


def write_dashboard(products, scene, path, title="PassCast", subtitle=""):
    payload = {
        "title": title,
        "subtitle": subtitle,
        "satellite": scene.platform,
        "image_time": scene.time.strftime("%Y-%m-%d %H:%M UTC"),
        "image_time_ist": (scene.time + dt.timedelta(hours=5, minutes=30)).strftime("%d %b %Y, %H:%M IST"),
        "classes": CLASS_NAMES,
        "passes": [dict(_clean(p.result.to_dict()), grid=_pack_field(p)) for p in products],
    }
    html = TEMPLATE.replace("/*__DATA__*/null", json.dumps(payload))
    vendor = Path(__file__).parent / "vendor" / "three.min.js"
    if vendor.exists():   # embed the 3D engine so the dashboard works fully offline
        loader = html[html.index("const s=document.createElement"):html.index("</script></body>")]
        html = html.replace(loader, "three(D.passes[cur]);\n")
        html = html.replace("</head>", "<script>" + vendor.read_text(encoding="utf-8") + "</script></head>", 1)
    Path(path).write_text(html, encoding="utf-8")
    return path


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PassCast — flyability across India</title>
<style>
:root{--bg:#0d1117;--panel:#151b23;--line:#263040;--txt:#e6edf3;--mute:#8b98a9;--g:#1f9d55;--a:#e3a008;--r:#d64545;--accent:#5aa9ff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.45 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
header{display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;padding:14px 20px;border-bottom:1px solid var(--line)}
h1{font-size:18px;margin:0;letter-spacing:.3px}h1 span{color:var(--mute);font-weight:400;font-size:13px;margin-left:8px}
.meta{color:var(--mute);font-size:12.5px;text-align:right}
.wrap{display:grid;grid-template-columns:340px 1fr;min-height:calc(100vh - 62px)}
@media(max-width:900px){.wrap{grid-template-columns:1fr}}
aside{border-right:1px solid var(--line);padding:12px;overflow:auto;max-height:calc(100vh - 62px)}
@media(max-width:900px){aside{max-height:none;border-right:0;border-bottom:1px solid var(--line)}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px;margin-bottom:10px;cursor:pointer;display:grid;grid-template-columns:58px 1fr;gap:12px;align-items:center}
.card.sel{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent) inset}
.badge{width:58px;height:58px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:22px;font-weight:700;color:#fff}
.nm{font-weight:600}.sub{color:var(--mute);font-size:12px}
.pill{display:inline-block;font-size:11px;padding:1px 7px;border-radius:10px;border:1px solid var(--line);margin-top:4px;color:var(--mute)}
main{padding:14px 18px;overflow:auto}
.top{display:flex;flex-wrap:wrap;gap:16px;align-items:flex-start}
.big{display:flex;gap:14px;align-items:center}.big .badge{width:76px;height:76px;font-size:28px}
.kv{display:grid;grid-template-columns:repeat(4,minmax(110px,1fr));gap:8px;flex:1;min-width:300px}
.kv div{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 10px}
.kv b{display:block;font-size:16px}.kv span{color:var(--mute);font-size:11.5px}
.views{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px}
@media(max-width:1200px){.views{grid-template-columns:1fr}}
.box{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px}
.box h3{margin:0 0 8px;font-size:13px;color:var(--mute);font-weight:600;display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}
canvas{width:100%;height:auto;display:block;border-radius:6px;background:#000}
#map{cursor:crosshair}
.seg button,.btn{background:#1c2430;border:1px solid var(--line);color:var(--txt);border-radius:6px;padding:3px 9px;font-size:12px;cursor:pointer}
.seg button.on{background:var(--accent);border-color:var(--accent);color:#04111f}
ul{margin:6px 0 0 18px;padding:0}li{margin:2px 0}
.legend{display:flex;gap:10px;flex-wrap:wrap;font-size:11.5px;color:var(--mute);margin-top:6px}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}
#q{font-size:12.5px;margin-top:6px;color:var(--txt);min-height:18px}
.note{color:var(--mute);font-size:12px;margin-top:16px;border-top:1px solid var(--line);padding-top:10px}
#three{width:100%;aspect-ratio:1/1;border-radius:6px;background:#06090d;position:relative}
#three .msg{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:var(--mute);font-size:12px;text-align:center;padding:20px}
</style></head><body>
<header><div><h1 id="t"></h1><div class="sub" id="st"></div></div><div class="meta" id="m"></div></header>
<div class="wrap"><aside id="list"></aside>
<main>
 <div class="top"><div class="big"><div class="badge" id="sb"></div><div><div class="nm" id="sn" style="font-size:18px"></div><div class="sub" id="sl"></div><span class="pill" id="sc"></span></div></div>
 <div class="kv" id="kv"></div></div>
 <div class="views">
  <div class="box"><h3>Map: satellite cloud draped on terrain <span class="seg" id="lay"><button data-l="cell" class="on">Terrain in cloud</button><button data-l="col">Cloud overhead</button><button data-l="cls">Layer class</button></span></h3>
   <canvas id="map" width="660" height="660"></canvas>
   <div class="legend" id="leg"></div>
   <div style="margin-top:8px;display:flex;gap:6px;align-items:center;flex-wrap:wrap"><button class="btn" id="dr">Draw region</button><button class="btn" id="clr">Clear</button><span class="sub">Click points on the map, then "Finish" to get cloud amount for any valley or area.</span></div>
   <div id="q"></div></div>
  <div class="box"><h3>3D view <span class="sub">drag to rotate · scroll to zoom</span></h3><div id="three"><div class="msg">Loading 3D engine…</div></div></div>
 </div>
 <div class="views"><div class="box"><h3>Why this score</h3><ul id="why"></ul></div><div class="box"><h3>Caveats &amp; data quality</h3><ul id="cav"></ul></div></div>
 <div class="note">Decision support only. The pilot / aviation met officer makes the go/no-go decision. Satellites observe cloud tops; cloud base, visibility below cloud, wind, turbulence and icing are not observed. Scores are conservative by design: when the data cannot rule out cloud, the pass is scored down.</div>
</main></div>
<script>
const D=/*__DATA__*/null;
const C={1:"#1f9d55",0.5:"#e3a008",0:"#d64545"};
const CLS=["#00000000","#7fb3ff","#ffffff","#b9a3ff"];
const $=s=>document.querySelector(s);
function dec(b64,T){const s=atob(b64),u=new Uint8Array(s.length);for(let i=0;i<s.length;i++)u[i]=s.charCodeAt(i);return new T(u.buffer)}
$("#t").innerHTML=D.title+"<span>flyability for passes, helipads, airstrips &amp; valleys</span>";$("#st").textContent=D.subtitle;
$("#m").innerHTML=D.satellite+"<br>Image: "+D.image_time+" ("+D.image_time_ist+")";
let cur=0,layer="cell",poly=[],drawing=false;
D.passes.forEach(p=>{const g=p.grid;g.Z=dec(g.z,Uint16Array);g.PC=dec(g.pcol,Uint8Array);g.PL=dec(g.pcell,Uint8Array);g.K=dec(g.cls,Uint8Array);g.S=dec(g.snow,Uint8Array);g.T=dec(g.cth,Uint16Array)});
function fmt(v,u){return v==null?"–":Math.round(v).toLocaleString()+(u||"")}
function list(){$("#list").innerHTML=D.passes.map((p,i)=>`<div class="card ${i==cur?"sel":""}" data-i="${i}"><div class="badge" style="background:${C[p.score]}">${p.score}</div><div><div class="nm">${p.name}</div><div class="sub">${p.region} · ${fmt(p.elevation_m," m")}</div><div class="sub">${p.label}</div><span class="pill">confidence ${p.confidence_label}</span></div></div>`).join("");
 document.querySelectorAll(".card").forEach(c=>c.onclick=()=>{cur=+c.dataset.i;poly=[];$("#q").textContent="";render()})}
function render(){list();const p=D.passes[cur];
 $("#sb").style.background=C[p.score];$("#sb").textContent=p.score;$("#sn").textContent=p.name;$("#sl").textContent=p.label+" · "+p.region;
 $("#sc").textContent="confidence "+p.confidence_label+" ("+Math.round(p.confidence*100)+"%)";
 const b=p.class_breakdown||{};
 $("#kv").innerHTML=[["Corridor obscured",Math.round(p.obscured_fraction*100)+"%"],["Pass elevation",fmt(p.elevation_m," m")],["Cloud top",fmt(p.cloud_top_m," m")],["Ceiling vs pass",p.ceiling_above_pass_m==null?"–":(p.ceiling_above_pass_m>0?"+":"")+fmt(p.ceiling_above_pass_m," m")],["Terrain in cloud",Math.round((b["in cloud"]||0)*100)+"%"],["Above cloud layer",Math.round((b["above cloud"]||0)*100)+"%"],["Below cloud layer",Math.round((b["below cloud"]||0)*100)+"%"],["Clear",Math.round((b["clear"]||0)*100)+"%"]].map(([k,v])=>`<div><b>${v}</b><span>${k}</span></div>`).join("");
 $("#why").innerHTML=p.reasons.map(r=>`<li>${r}</li>`).join("");$("#cav").innerHTML=p.caveats.map(r=>`<li>${r}</li>`).join("");
 legend();draw();three(p)}
function legend(){$("#leg").innerHTML=layer=="cls"?`<span><i class="sw" style="background:#ffffff"></i>in cloud</span><span><i class="sw" style="background:#b9a3ff"></i>below cloud (ceiling)</span><span><i class="sw" style="background:#7fb3ff"></i>above cloud layer</span><span><i class="sw" style="background:#444"></i>clear</span>`:`<span><i class="sw" style="background:#fff"></i>${layer=="cell"?"terrain inside cloud (probability)":"cloud anywhere overhead (probability)"}</span><span><i class="sw" style="border:2px solid #fff;background:none"></i>flight corridor</span>`}
function hs(g){if(g.H)return g.H;const{nx,ny,Z}=g,H=new Float32Array(nx*ny);const dx=(g.lon1-g.lon0)/(nx-1)*111000*Math.cos((g.lat0+g.lat1)/2*Math.PI/180),dy=(g.lat1-g.lat0)/(ny-1)*111000;
 const az=315*Math.PI/180,al=45*Math.PI/180;for(let j=0;j<ny;j++)for(let i=0;i<nx;i++){const a=Z[j*nx+Math.max(i-1,0)],b=Z[j*nx+Math.min(i+1,nx-1)],c=Z[Math.max(j-1,0)*nx+i],d=Z[Math.min(j+1,ny-1)*nx+i];
 const gx=(b-a)/(2*dx),gy=(d-c)/(2*dy),sl=Math.PI/2-Math.atan(Math.hypot(gx,gy)),asp=Math.atan2(-gx,gy);H[j*nx+i]=Math.max(0,Math.sin(al)*Math.sin(sl)+Math.cos(al)*Math.cos(sl)*Math.cos(az-asp))}return g.H=H}
function draw(){const p=D.passes[cur],g=p.grid,cv=$("#map"),cx=cv.getContext("2d"),W=cv.width,Hh=cv.height,{nx,ny}=g;
 const off=document.createElement("canvas");off.width=nx;off.height=ny;const oc=off.getContext("2d"),im=oc.createImageData(nx,ny),H=hs(g);
 for(let j=0;j<ny;j++)for(let i=0;i<nx;i++){const k=j*nx+i,o=((ny-1-j)*nx+i)*4,h=Math.pow(H[k],1.3)*225+18;let r=h,gg=h,b=h;const zf=(g.Z[k]-g.zmin)/(g.zmax-g.zmin+1);r=h*(.85+.15*zf);gg=h*(.82+.1*zf);b=h*(.78+.1*zf);
  if(g.S[k]>150){r=r*.8+235*.2;gg=gg*.8+245*.2;b=b*.8+255*.2}
  if(layer=="cls"){const c=g.K[k];if(c==2){r=255;gg=255;b=255}else if(c==3){r=r*.4+185*.6;gg=gg*.4+163*.6;b=b*.4+255*.6}else if(c==1){r=r*.5+127*.5;gg=gg*.5+179*.5;b=b*.5+255*.5}}
  else{const a=(layer=="cell"?g.PL[k]:g.PC[k])/255*.85;r=r*(1-a)+255*a;gg=gg*(1-a)+255*a;b=b*(1-a)+255*a}
  im.data[o]=r;im.data[o+1]=gg;im.data[o+2]=b;im.data[o+3]=255}
 oc.putImageData(im,0,0);cx.imageSmoothingEnabled=true;cx.drawImage(off,0,0,W,Hh);
 const X=lo=>(lo-g.lon0)/(g.lon1-g.lon0)*W,Y=la=>Hh-(la-g.lat0)/(g.lat1-g.lat0)*Hh;
 const rpx=3000/((g.lon1-g.lon0)*111000*Math.cos(p.lat*Math.PI/180))*W;
 cx.lineWidth=3;cx.strokeStyle=C[p.score];cx.beginPath();cx.arc(X(p.lon),Y(p.lat),rpx,0,7);cx.stroke();
 cx.fillStyle=C[p.score];cx.strokeStyle="#000";cx.lineWidth=1.5;cx.beginPath();const px=X(p.lon),py=Y(p.lat);cx.moveTo(px,py-11);cx.lineTo(px-9,py+7);cx.lineTo(px+9,py+7);cx.closePath();cx.fill();cx.stroke();
 cx.font="600 13px sans-serif";cx.fillStyle="#fff";cx.strokeStyle="#000";cx.lineWidth=3;cx.strokeText(p.name,px+12,py-8);cx.fillText(p.name,px+12,py-8);
 const km=10,kpx=km*1000/((g.lon1-g.lon0)*111000*Math.cos(p.lat*Math.PI/180))*W;cx.fillStyle="#fff";cx.fillRect(16,Hh-22,kpx,4);cx.font="11px sans-serif";cx.fillText(km+" km",16,Hh-28);
 if(poly.length){cx.strokeStyle="#5aa9ff";cx.fillStyle="rgba(90,169,255,.18)";cx.lineWidth=2;cx.beginPath();poly.forEach(([lo,la],i)=>i?cx.lineTo(X(lo),Y(la)):cx.moveTo(X(lo),Y(la)));if(!drawing)cx.closePath();cx.fill();cx.stroke();poly.forEach(([lo,la])=>{cx.beginPath();cx.arc(X(lo),Y(la),3.5,0,7);cx.fillStyle="#5aa9ff";cx.fill()})}}
$("#map").addEventListener("click",e=>{if(!drawing)return;const g=D.passes[cur].grid,cv=$("#map"),r=cv.getBoundingClientRect(),x=(e.clientX-r.left)/r.width,y=(e.clientY-r.top)/r.height;poly.push([g.lon0+x*(g.lon1-g.lon0),g.lat1-y*(g.lat1-g.lat0)]);draw()});
$("#dr").onclick=()=>{if(!drawing){drawing=true;poly=[];$("#dr").textContent="Finish";$("#q").textContent="Click points around the valley or area…";draw()}else{drawing=false;$("#dr").textContent="Draw region";query()}};
$("#clr").onclick=()=>{poly=[];drawing=false;$("#dr").textContent="Draw region";$("#q").textContent="";draw()};
function inside(x,y,P){let c=false;for(let i=0,j=P.length-1;i<P.length;j=i++){const[xi,yi]=P[i],[xj,yj]=P[j];if(((yi>y)!=(yj>y))&&(x<(xj-xi)*(y-yi)/(yj-yi)+xi))c=!c}return c}
function query(){const g=D.passes[cur].grid;if(poly.length<3){$("#q").textContent="Need at least 3 points.";draw();return}
 let n=0,cc=0,tc=0,k=[0,0,0,0],zmn=1e9,zmx=0,tops=[];for(let j=0;j<g.ny;j++){const la=g.lat0+j/(g.ny-1)*(g.lat1-g.lat0);for(let i=0;i<g.nx;i++){const lo=g.lon0+i/(g.nx-1)*(g.lon1-g.lon0);if(!inside(lo,la,poly))continue;const q=j*g.nx+i;n++;if(g.PC[q]>=128)cc++;if(g.PL[q]>=128)tc++;k[g.K[q]]++;zmn=Math.min(zmn,g.Z[q]);zmx=Math.max(zmx,g.Z[q]);if(g.T[q]>0)tops.push(g.T[q])}}
 if(!n){$("#q").textContent="Region contains no grid cells.";return}tops.sort((a,b)=>a-b);const f=cc/n;
 $("#q").innerHTML=`<b>Selected region:</b> cloud cover <b>${Math.round(f*100)}%</b> (${Math.round(f*8)}/8 oktas) · terrain in cloud <b>${Math.round(tc/n*100)}%</b> · median cloud top ${tops.length?Math.round(tops[tops.length>>1]).toLocaleString()+" m":"–"} · terrain ${zmn.toLocaleString()}–${zmx.toLocaleString()} m`;draw()}
document.querySelectorAll("#lay button").forEach(b=>b.onclick=()=>{document.querySelectorAll("#lay button").forEach(x=>x.classList.remove("on"));b.classList.add("on");layer=b.dataset.l;legend();draw()});
let T3=null;
function three(p){if(!window.THREE){return}const el=$("#three");el.innerHTML="";const g=p.grid,W=el.clientWidth||500;
 const R=new THREE.WebGLRenderer({antialias:true});R.setPixelRatio(Math.min(devicePixelRatio,2));R.setSize(W,W);el.appendChild(R.domElement);
 const S=new THREE.Scene();S.background=new THREE.Color(0x06090d);const cam=new THREE.PerspectiveCamera(40,1,.1,1000);
 const n=Math.min(g.nx,160),m=Math.min(g.ny,160),sx=(g.nx-1)/(n-1),sy=(g.ny-1)/(m-1);
 const geo=new THREE.PlaneGeometry(100,100*(g.lat1-g.lat0)/((g.lon1-g.lon0)*Math.cos(p.lat*Math.PI/180)),n-1,m-1);const pos=geo.attributes.position,col=new Float32Array(pos.count*3),H=hs(g);
 const ex=2.0, kmPerUnit=(g.lon1-g.lon0)*111*Math.cos(p.lat*Math.PI/180)/100;
 for(let v=0;v<pos.count;v++){const i=v%n,j=m-1-Math.floor(v/n),k=Math.round(j*sy)*g.nx+Math.round(i*sx);pos.setZ(v,(g.Z[k]-g.zmin)/1000/kmPerUnit*ex);
  const h=.25+.75*H[k],a=g.PL[k]/255;let r=h*.55,gg=h*.5,b=h*.42;if(g.S[k]>120){r=g2(r,h);gg=g2(gg,h);b=g2(b,h)}r=r*(1-a)+a;gg=gg*(1-a)+a;b=b*(1-a)+a;col.set([r,gg,b],v*3)}
 function g2(c,h){return c*.4+h*.6}
 geo.setAttribute("color",new THREE.BufferAttribute(col,3));geo.computeVertexNormals();
 const mesh=new THREE.Mesh(geo,new THREE.MeshLambertMaterial({vertexColors:true}));mesh.rotation.x=-Math.PI/2;S.add(mesh);
 if(p.cloud_top_m){const ct=(p.cloud_top_m-g.zmin)/1000/kmPerUnit*ex;if(ct>0){const pl=new THREE.Mesh(new THREE.PlaneGeometry(100,100),new THREE.MeshBasicMaterial({color:0xffffff,transparent:true,opacity:.12,side:THREE.DoubleSide}));pl.rotation.x=-Math.PI/2;pl.position.y=ct;S.add(pl)}}
 const mk=new THREE.Mesh(new THREE.ConeGeometry(1.4,4,12),new THREE.MeshBasicMaterial({color:C[p.score]}));const pz=((p.elevation_m-g.zmin)/1000/kmPerUnit*ex);
 mk.position.set((p.lon-(g.lon0+g.lon1)/2)/(g.lon1-g.lon0)*100,pz+3,-(p.lat-(g.lat0+g.lat1)/2)/(g.lat1-g.lat0)*100*(g.lat1-g.lat0)/((g.lon1-g.lon0)*Math.cos(p.lat*Math.PI/180)));mk.rotation.x=Math.PI;S.add(mk);
 S.add(new THREE.AmbientLight(0xffffff,.55));const dl=new THREE.DirectionalLight(0xffffff,.9);dl.position.set(-60,80,-40);S.add(dl);
 let th=.7,ph=.85,dist=150;function upd(){cam.position.set(dist*Math.sin(ph)*Math.sin(th),dist*Math.cos(ph),dist*Math.sin(ph)*Math.cos(th));cam.lookAt(0,5,0);R.render(S,cam)}
 let dn=null;R.domElement.onpointerdown=e=>dn=[e.clientX,e.clientY,th,ph];window.onpointerup=()=>dn=null;
 R.domElement.onpointermove=e=>{if(!dn)return;th=dn[2]-(e.clientX-dn[0])*.01;ph=Math.min(1.45,Math.max(.15,dn[3]-(e.clientY-dn[1])*.01));upd()};
 R.domElement.onwheel=e=>{e.preventDefault();dist=Math.min(400,Math.max(50,dist*(1+e.deltaY*.001)));upd()};upd()}
render();
const s=document.createElement("script");s.src="https://unpkg.com/three@0.160.0/build/three.min.js";s.onload=()=>three(D.passes[cur]);s.onerror=()=>{$("#three").innerHTML='<div class="msg">3D view needs an internet connection to load the 3D engine. The map and scores work offline.</div>'};document.head.appendChild(s);
</script></body></html>"""
