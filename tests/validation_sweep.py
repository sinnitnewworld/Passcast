# Broken-cloud validation sweep. Usage: python tests/validation_sweep.py 0   (no cirrus) | 1 (cirrus above)
import datetime as dt, numpy as np, passcast.demo as D, math, contextlib, io, sys
from scipy.ndimage import gaussian_filter
from passcast.config import Thresholds, Pass
from passcast.pipeline import run
from passcast.dem import synthetic_himalaya
cirrus_on = sys.argv[1]=='1'
th=Thresholds(window_half_width_km=18)
dem0=synthetic_himalaya(lat0=34.0, lon0=77.0, half_km=45.0, res_m=90.0)
passes=[Pass(n,la,lo,round(z)) for (n,la,lo,z) in dem0.passes]
ok=tot=cons=0
for thr in [0.0,0.3,0.7,1.0,1.4]:
  for seed in [1,2,3]:
    t=D.build_truth(dem0)
    if not cirrus_on: t['cirrus'][:]=False
    LON,LAT=dem0.mesh(); lat0,lon0=dem0.lat.mean(),dem0.lon.mean()
    X=(LON-lon0)*111*math.cos(math.radians(lat0)); Y=(LAT-lat0)*111
    n,la,lo,zc=dem0.passes[2]; xc=(lo-lon0)*111*math.cos(math.radians(lat0)); yc=(la-lat0)*111
    k=t['kind']==3
    t['cloud'][k]=False; t['cth'][k]=np.nan; t['base'][k]=np.nan; t['kind'][k]=0
    bl=gaussian_filter(np.random.default_rng(3+seed).standard_normal(dem0.z.shape),20); bl/=bl.std()
    m=(np.hypot(X-xc,Y-yc)<8)&(bl>thr)
    t['cloud']|=m; t['cth'][m]=zc+900; t['base'][m]=zc-400; t['kind'][m]=3
    t['snow']=(dem0.z>5000)&~t['cloud']
    when=dt.datetime(2026,11,26,6,0)
    sc=D.simulate_scene(dem0,t,when,seed=seed)
    ts=D.truth_scores(dem0,t,passes,th)
    with contextlib.redirect_stdout(io.StringIO()):
        res=run(sc,passes,lambda p,h: dem0.window(p.lat,p.lon,h),th,now=when)
    r=res[2].result; tr=ts[r.name]
    tot+=1; ok+= (tr[0]==r.score); cons += (r.score<=tr[0])
    print(f"thr {thr:.1f} s{seed}: truth {tr[0]} ({tr[1]:.2f})  pred {r.score} ({r.obscured_fraction:.2f}) conf {r.confidence_label}")
print('agreement',ok,'/',tot,' never-optimistic',cons,'/',tot)
