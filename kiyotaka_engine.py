"""
Kiyotaka Engine — OAR Premium
Volume Profile (VPFR), TPO/Market Profile, Liquidations.
API: https://api.kiyotaka.ai/v1/points
Auth: X-Kiyotaka-Key header
"""
import os, json, asyncio, httpx
from pathlib import Path
from datetime import datetime, timezone

KIYOTAKA_BASE = "https://api.kiyotaka.ai/v1/points"
DATA_DIR  = Path(os.environ.get("DATA_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or ("/var/data" if Path("/var/data").exists() else "data"))
CACHE_DIR = DATA_DIR / "kiyotaka"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

def _key(): return os.environ.get("KIYOTAKA_API_KEY", "")
def _hdr(k=""): return {"X-Kiyotaka-Key": k or _key()}
def _cache(t,s,ts): return CACHE_DIR / f"{t}_{s}_{ts}.json"
def _lc(p):
    try: return json.loads(Path(p).read_text()) if Path(p).exists() else None
    except: return None
def _sc(p,d):
    try: Path(p).write_text(json.dumps(d, ensure_ascii=False))
    except: pass
def _day_start(ts_ms):
    dt = datetime.fromtimestamp(ts_ms/1000, tz=timezone.utc)
    return int(dt.replace(hour=0,minute=0,second=0,microsecond=0).timestamp())

# ── VOLUME PROFILE ────────────────────────────────────────
async def get_volume_profile(sym, from_ts, period_sec=86400, key=""):
    cp = _cache("vpfr", sym, from_ts)
    if c := _lc(cp): return c
    params = {"type":"VOLUME_PROFILE_AGG","exchange":"BINANCE_FUTURES",
              "rawSymbol":sym,"interval":"DAY" if period_sec>=86400 else "HOUR",
              "from":from_ts,"period":period_sec,"transform.normalize.quote":"USD"}
    try:
        async with httpx.AsyncClient(timeout=15) as cl:
            r = await cl.get(KIYOTAKA_BASE, params=params, headers=_hdr(key))
            if r.status_code != 200: return {"error": f"HTTP {r.status_code}"}
            data = r.json()
        series = data.get("series", [])
        if not series: return {"error": "bos yanit"}
        prof = series[0]["points"][0]["Point"]["profile"]
        triplets = [(prof[i], prof[i+1], prof[i+2]) for i in range(0,len(prof)-2,3)]
        if not triplets: return {"error": "profil bos"}
        poc_e = max(triplets, key=lambda t: t[1]+t[2])
        poc = poc_e[0]
        total = sum(t[1]+t[2] for t in triplets)
        sp = sorted(triplets, key=lambda t: t[0])
        pi = next(i for i,t in enumerate(sp) if t[0]==poc)
        va = poc_e[1]+poc_e[2]; target = total*0.70
        lo=hi=pi
        while va<target:
            cu = hi+1<len(sp); cd = lo-1>=0
            if not cu and not cd: break
            uv = (sp[hi+1][1]+sp[hi+1][2]) if cu else 0
            dv = (sp[lo-1][1]+sp[lo-1][2]) if cd else 0
            if uv>=dv and cu: hi+=1; va+=uv
            elif cd: lo-=1; va+=dv
            else: hi+=1; va+=uv
        res = {"poc":poc,"vah":sp[hi][0],"val":sp[lo][0],"total_volume":round(total,2)}
        _sc(cp, res); return res
    except Exception as e: return {"error": str(e)[:80]}

# ── TPO / MARKET PROFILE ─────────────────────────────────
async def get_tpo(sym, from_ts, period_sec=86400, block_min=30, key=""):
    cp = _cache("tpo", sym, from_ts)
    if c := _lc(cp): return c
    params = {"type":"TPO_AGG","exchange":"BINANCE_FUTURES","rawSymbol":sym,
              "interval":"DAY","from":from_ts,"period":period_sec,
              "tpoSession":"TPO_SESSION_DAILY","tpoBlockSizeMinutes":block_min}
    try:
        async with httpx.AsyncClient(timeout=20) as cl:
            r = await cl.get(KIYOTAKA_BASE, params=params, headers=_hdr(key))
            if r.status_code != 200: return {"error": f"HTTP {r.status_code}"}
            data = r.json()
        series = data.get("series",[])
        if not series: return {"error": "bos"}
        td = series[0]["points"][0]["Point"]["TpoAggregation"]
        res = {k: td.get(k) for k in
               ["poc_price","vah_price","val_price","ibr_high","ibr_low",
                "poor_highs","poor_lows","profile_high","profile_low","total_volume"]}
        res["poor_highs"] = res.get("poor_highs") or []
        res["poor_lows"]  = res.get("poor_lows")  or []
        _sc(cp, res); return res
    except Exception as e: return {"error": str(e)[:80]}

# ── LİKİDASYONLAR ────────────────────────────────────────
async def get_liquidations(sym, from_ts, period_sec=3600, key=""):
    cp = _cache("liq", sym, from_ts)
    if c := _lc(cp): return c
    params = {"type":"LIQUIDATION_AGG","exchange":"BINANCE_FUTURES","rawSymbol":sym,
              "interval":"HOUR" if period_sec<=3600 else "DAY",
              "from":from_ts,"period":period_sec,"transform.normalize.quote":"USD"}
    long_liq=short_liq=0.0
    try:
        async with httpx.AsyncClient(timeout=15) as cl:
            r = await cl.get(KIYOTAKA_BASE, params=params, headers=_hdr(key))
            if r.status_code != 200: return {"error": f"HTTP {r.status_code}"}
            for s in r.json().get("series",[]):
                side = s["id"].get("side","")
                pts  = s.get("points",[])
                if not pts: continue
                v = pts[0]["Point"].get("liquidations",0)
                if side=="BUY": long_liq+=v
                elif side=="SELL": short_liq+=v
    except Exception as e: return {"error": str(e)[:80]}
    total = long_liq+short_liq
    ratio = long_liq/total if total>0 else 0.5
    dom = "LONG" if ratio>0.6 else "SHORT" if ratio<0.4 else "NEUTRAL"
    res = {"long_liq_usd":round(long_liq,2),"short_liq_usd":round(short_liq,2),
           "total_liq_usd":round(total,2),"dominant":dom,"ratio":round(ratio,4)}
    _sc(cp, res); return res

# ── OAR BACKTEST FİLTRESİ ────────────────────────────────
async def kiyotaka_filtre(sym, fib_price, direction, ts_ms, key=""):
    """
    Fib temas anında Kiyotaka filtresi.
    Dönüş: {score(0-30), kalite, details, vpfr, tpo, liq}
    """
    ds  = _day_start(ts_ms)
    hs  = int(ts_ms/1000) - 3600
    vpfr, tpo, liq = await asyncio.gather(
        get_volume_profile(sym, ds, 86400, key),
        get_tpo(sym, ds, 86400, 30, key),
        get_liquidations(sym, hs, 3600, key),
    )
    score=0; details=[]
    tol = fib_price*0.005

    # VPFR (0-10)
    if not vpfr.get("error"):
        poc,vah,val = vpfr.get("poc",0),vpfr.get("vah",0),vpfr.get("val",0)
        if poc and abs(fib_price-poc)<=tol:
            score+=10; details.append(f"VPFR POC={poc:,.0f} ortüşüyor(+10)")
        elif val and vah and val<=fib_price<=vah:
            score+=5; details.append(f"VPFR VA içi(+5)")
        else:
            score+=8; details.append("VPFR VA dışı-ekstrem(+8)")

    # TPO (0-10)
    if not tpo.get("error"):
        pp,vp,vl = tpo.get("poc_price"),tpo.get("vah_price"),tpo.get("val_price")
        if pp and abs(fib_price-pp)<=tol:
            score+=10; details.append(f"TPO POC={pp:,.0f}(+10)")
        elif vp and vl and not(vl<=fib_price<=vp):
            score+=8; details.append("TPO VA dışı(+8)")
        elif vp and vl:
            score+=4; details.append("TPO VA içi(+4)")
        for ph in tpo.get("poor_highs",[]):
            if abs(fib_price-ph)<=tol: score+=3; details.append(f"PoorH={ph:,.0f}(+3)"); break
        for pl in tpo.get("poor_lows",[]):
            if abs(fib_price-pl)<=tol: score+=3; details.append(f"PoorL={pl:,.0f}(+3)"); break

    # Likidasyon (0-10)
    if not liq.get("error"):
        tl,dom = liq.get("total_liq_usd",0),liq.get("dominant","NEUTRAL")
        if tl>1_000_000:
            if (direction=="LONG" and dom=="LONG") or (direction=="SHORT" and dom=="SHORT"):
                score+=10; details.append(f"LIK ${tl/1e6:.1f}M uyumlu(+10)")
            else:
                score+=3; details.append(f"LIK ${tl/1e6:.1f}M ters(+3)")
        elif tl>100_000: score+=5; details.append(f"LIK ${tl/1e3:.0f}K(+5)")
        else: score+=2; details.append("LIK dusuk(+2)")

    kal = "YÜKSEK" if score>=22 else "ORTA" if score>=14 else "DÜŞÜK"
    return {"score":score,"max":30,"kalite":kal,"vpfr":vpfr,"tpo":tpo,"liq":liq,
            "details":" | ".join(details)}

# ── CANLI ÖZET ───────────────────────────────────────────
async def canli_ozet(sym="BTCUSDT", key=""):
    ds = _day_start(int(datetime.now(timezone.utc).timestamp()*1000))
    vpfr,tpo = await asyncio.gather(
        get_volume_profile(sym,ds,86400,key),
        get_tpo(sym,ds,86400,30,key),
    )
    return {
        "symbol":sym,
        "vpfr_poc":vpfr.get("poc"),"vpfr_vah":vpfr.get("vah"),"vpfr_val":vpfr.get("val"),
        "tpo_poc":tpo.get("poc_price"),"tpo_vah":tpo.get("vah_price"),"tpo_val":tpo.get("val_price"),
        "tpo_ibr_high":tpo.get("ibr_high"),"tpo_ibr_low":tpo.get("ibr_low"),
        "poor_highs":tpo.get("poor_highs",[])[:3],"poor_lows":tpo.get("poor_lows",[])[:3],
        "errors":{"vpfr":vpfr.get("error"),"tpo":tpo.get("error")},
    }

if __name__ == "__main__":
    import sys
    async def test():
        key = _key()
        if not key: print("KIYOTAKA_API_KEY eksik"); return
        sym = sys.argv[1] if len(sys.argv)>1 else "BTCUSDT"
        ozet = await canli_ozet(sym, key)
        print(json.dumps(ozet, indent=2, ensure_ascii=False))
    asyncio.run(test())
