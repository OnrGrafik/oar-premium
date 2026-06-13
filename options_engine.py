"""
Options Engine — OAR Premium v2
═══════════════════════════════════════════════════════════════════
Vercel options-desk'in Python'a taşınmış hali. Vercel'e GEREK YOK.
Deribit'e DOĞRUDAN bağlanır (inverse BTC/ETH options).

Hesaplananlar:
  • GEX per strike (Black-Scholes gamma)
  • Call Wall, Put Wall (max gamma strike)
  • Max Pain (option writer ağırlık merkezi)
  • Zero Gamma (Brent kök bulma — gamma flip)
  • Expected Move band (ATM IV)
  • Opsiyon CVD (call/put buy/sell delta-yönlü)
  • Vade dilimli: 0-7g / 8-45g / 45g+ / genel

Kaynak: Hull 11e, Wikipedia Greeks (Vercel gex.js ile birebir formül)
"""
import math, asyncio, httpx, time
from datetime import datetime, timezone

DERIBIT = "https://www.deribit.com/api/v2/public"
HDR = {"Accept": "application/json", "User-Agent": "OAR-Premium/2.0"}

# ─── Black-Scholes (r=q=0, inverse) ───────────────────────────────
def _npdf(x): return math.exp(-0.5*x*x)/math.sqrt(2*math.pi)
def _ncdf(x):
    s=-1 if x<0 else 1; x=abs(x)
    t=1/(1+0.2316419*x)
    p=t*(0.319381530+t*(-0.356563782+t*(1.781477937+t*(-1.821255978+t*1.330274429))))
    return 0.5+s*(0.5-_npdf(x)*p)
def _gamma(S,K,T,sig):
    if T<=0 or sig<=0 or S<=0 or K<=0: return 0
    d1=(math.log(S/K)+0.5*sig*sig*T)/(sig*math.sqrt(T))
    return _npdf(d1)/(S*sig*math.sqrt(T))

# ─── Deribit ──────────────────────────────────────────────────────
async def _drb(cl, method, params=None):
    qs="&".join(f"{k}={v}" for k,v in (params or {}).items())
    url=f"{DERIBIT}/{method}"+(f"?{qs}" if qs else "")
    try:
        r=await cl.get(url, headers=HDR)
        if r.status_code!=200: return None
        return r.json().get("result")
    except Exception:
        return None

async def _spot(cl, currency="BTC"):
    d=await _drb(cl,"get_index_price",{"index_name":f"{currency.lower()}_usd"})
    return float(d.get("index_price",0)) if d else 0

def _expiry_label(ts, now):
    gun=(ts-now)/(86400*1000)
    if gun<=7: return "0-7d"
    if gun<=45: return "8-45d"
    return "45d+"

async def _tum_opsiyonlar(cl, spot, currency="BTC"):
    inst=await _drb(cl,"get_instruments",{"currency":currency,"kind":"option","expired":"false"})
    if not inst: return []
    now=int(time.time()*1000)
    aktif=[i for i in inst if i["expiration_timestamp"]>now]
    opts=[]
    BATCH=25
    for i in range(0,len(aktif),BATCH):
        batch=aktif[i:i+BATCH]
        tickers=await asyncio.gather(*[_drb(cl,"ticker",{"instrument_name":x["instrument_name"]}) for x in batch],return_exceptions=True)
        for inst_data,tk in zip(batch,tickers):
            if not isinstance(tk,dict): continue
            oi=tk.get("open_interest",0)
            iv=(tk.get("mark_iv",0) or 0)/100
            if not oi or not iv: continue
            ts=inst_data["expiration_timestamp"]
            T=max((ts-now)/(365.25*24*3600*1000),0.0001)
            K=inst_data["strike"]
            typ="call" if inst_data["option_type"]=="call" else "put"
            g=_gamma(spot,K,T,iv)
            gex=oi*g*spot*spot*0.01*(1 if typ=="call" else -1)
            opts.append({"strike":K,"type":typ,"oi":oi,"iv":iv,"gex":gex,
                         "expiryTs":ts,"expiryLabel":_expiry_label(ts,now),"T":T})
        await asyncio.sleep(0.05)
    return opts

def _aggregate(opts, expiry_filter="all"):
    labels=["0-7d","8-45d","45d+"] if expiry_filter=="all" else [expiry_filter]
    m={}
    for o in opts:
        if o["expiryLabel"] not in labels: continue
        k=o["strike"]
        if k not in m: m[k]={"strike":k,"callGex":0,"putGex":0,"callOI":0,"putOI":0,"totalOI":0}
        x=m[k]; x["totalOI"]+=o["oi"]
        if o["type"]=="call": x["callGex"]+=o["gex"]; x["callOI"]+=o["oi"]
        else: x["putGex"]+=o["gex"]; x["putOI"]+=o["oi"]
    return sorted(m.values(),key=lambda a:a["strike"])

def _max_pain(opts):
    if not opts: return None
    em={}
    for o in opts:
        em.setdefault(o["expiryTs"],{"ts":o["expiryTs"],"opts":[],"oi":0})
        em[o["expiryTs"]]["opts"].append(o); em[o["expiryTs"]]["oi"]+=o["oi"]
    exps=sorted(em.values(),key=lambda a:a["ts"])
    if not exps: return None
    target=exps[0]
    for e in exps:
        if datetime.fromtimestamp(e["ts"]/1000,tz=timezone.utc).weekday()==4 and e["oi"]>0:
            target=e; break
    sm={}
    for o in target["opts"]:
        sm.setdefault(o["strike"],{"strike":o["strike"],"callOI":0,"putOI":0})
        if o["type"]=="call": sm[o["strike"]]["callOI"]+=o["oi"]
        else: sm[o["strike"]]["putOI"]+=o["oi"]
    strikes=list(sm.values()); minp=float('inf'); mp=None
    for K in strikes:
        pain=0
        for s in strikes:
            if s["strike"]>K["strike"]: pain+=(s["strike"]-K["strike"])*s["callOI"]
            if K["strike"]>s["strike"]: pain+=(K["strike"]-s["strike"])*s["putOI"]
        if pain<minp: minp=pain; mp=K["strike"]
    return mp

def _net_gamma_at(S,opts,now):
    total=0
    for o in opts:
        if not o["iv"] or not o["strike"]: continue
        T=max((o["expiryTs"]-now)/(365.25*24*3600*1000),0.0001)
        g=_npdf((math.log(S/o["strike"])+0.5*o["iv"]**2*T)/(o["iv"]*math.sqrt(T)))/(S*o["iv"]*math.sqrt(T))
        total+=o["oi"]*g*S*S*0.01*(1 if o["type"]=="call" else -1)
    return total

def _zero_gamma(opts, spot):
    if not opts: return None
    now=int(time.time()*1000)
    f=lambda S:_net_gamma_at(S,opts,now)
    lo,hi=spot*0.7,spot*1.3; steps=80; step=(hi-lo)/steps
    brackets=[]; prev=f(lo)
    for i in range(1,steps+1):
        S=lo+i*step; cur=f(S)
        if prev*cur<0: brackets.append((S-step,S,prev>0))
        prev=cur
    if not brackets: return None
    def bisect(a,b):
        for _ in range(50):
            mid=(a+b)/2
            if abs(b-a)<1: return mid
            if f(a)*f(mid)<0: b=mid
            else: a=mid
        return (a+b)/2
    crossings=[(round(bisect(a,b)),fp) for a,b,fp in brackets]
    flipup=[c for c in crossings if c[1]]
    if flipup:
        above=[c for c in flipup if c[0]>spot]
        return above[0][0] if above else flipup[-1][0]
    return min(crossings,key=lambda c:abs(c[0]-spot))[0]

def _find_levels(strikes, spot, opts):
    cw=pw=None; maxCG=maxPG=0
    for s in strikes:
        if s["callGex"]>maxCG: maxCG=s["callGex"]; cw=s["strike"]
        if abs(s["putGex"])>maxPG: maxPG=abs(s["putGex"]); pw=s["strike"]
    mp=_max_pain(opts); zg=_zero_gamma(opts,spot)
    pct=lambda v:round((v-spot)/spot*100,2) if v else None
    return {"call_wall":cw,"put_wall":pw,"max_pain":mp,"zero_gamma":zg,
            "CW":cw,"PW":pw,"ZG":zg,"maxPain":mp,
            "call_wall_pct":pct(cw),"put_wall_pct":pct(pw),"zero_gamma_pct":pct(zg)}

# ─── ANA: alarm-levels (vade dilimli) ─────────────────────────────
async def alarm_levels(currency="BTC"):
    async with httpx.AsyncClient(timeout=30) as cl:
        spot=await _spot(cl,currency)
        if not spot: return {"error":"spot alınamadı"}
        opts=await _tum_opsiyonlar(cl,spot,currency)
        if not opts: return {"error":"opsiyon verisi yok"}
    out={"spot":spot,"tarih":datetime.now(timezone.utc).isoformat()}
    for dilim in ["0-7d","8-45d","45d+","all"]:
        ad={"0-7d":"kisa","8-45d":"orta","45d+":"uzun","all":"genel"}[dilim]
        strikes=_aggregate(opts,dilim)
        out[ad]=_find_levels(strikes,spot,[o for o in opts if dilim=="all" or o["expiryLabel"]==dilim])
    out["genel"]=out.get("genel",{})
    return out

# ─── Opsiyon CVD ──────────────────────────────────────────────────
async def opsiyon_cvd(currency="BTC"):
    async with httpx.AsyncClient(timeout=20) as cl:
        now=int(time.time()*1000); basla=now-3*86400*1000
        trades=await _drb(cl,"get_last_trades_by_currency_and_time",
            {"currency":currency,"kind":"option","start_timestamp":basla,"end_timestamp":now,"count":1000})
        if not trades or "trades" not in trades: return {"error":"trade yok","data":[]}
        tl=sorted(trades["trades"],key=lambda x:x["timestamp"])
        cvd=0; seri=[]
        for t in tl:
            nm=t["instrument_name"]; tip="C" if nm.endswith("-C") else "P"
            buy=t["direction"]=="buy"; amt=t["amount"]
            if tip=="C": cvd+=amt if buy else -amt
            else: cvd+=-amt if buy else amt
            seri.append({"ts":t["timestamp"],"cvd":round(cvd,2)})
    medyan=sorted([s["cvd"] for s in seri])[len(seri)//2] if seri else 0
    return {"data":seri[-200:],"guncel":round(cvd,2),"medyan":round(medyan,2),
            "yon":"YUKARI" if cvd>medyan else "AŞAĞI"}

# ─── GEX özet (skorlama için) ─────────────────────────────────────
async def gex_ozet(currency="BTC"):
    lv=await alarm_levels(currency)
    if lv.get("error"): return lv
    spot=lv["spot"]; genel=lv.get("genel",{})
    cw,pw,zg=genel.get("call_wall"),genel.get("put_wall"),genel.get("zero_gamma")
    konum="—"
    if zg and spot:
        konum="POZİTİF GAMMA (stabilize)" if spot>zg else "NEGATİF GAMMA (volatil)"
    return {"spot":spot,"call_wall":cw,"put_wall":pw,"zero_gamma":zg,
            "max_pain":genel.get("max_pain"),"gamma_rejim":konum,
            "yorum":f"Spot ${spot:,.0f} · ZG ${zg:,.0f} · {konum}" if zg else f"Spot ${spot:,.0f}"}
