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

# ─── Black-Scholes Greeks (Hull 11e · gex.js ile birebir) ─────────
def _npdf(x): return math.exp(-0.5*x*x)/math.sqrt(2*math.pi)
def _ncdf(x):
    s=-1 if x<0 else 1; x=abs(x)
    t=1/(1+0.2316419*x)
    p=t*(0.319381530+t*(-0.356563782+t*(1.781477937+t*(-1.821255978+t*1.330274429))))
    return 0.5+s*(0.5-_npdf(x)*p)

def _bs_greeks(S, K, T, sig, typ, r=0, q=0):
    """Tam BS Greekleri — delta, gamma, vega, vanna, charm (Hull 11e)."""
    if T<=0 or sig<=0 or S<=0 or K<=0:
        return {"delta":0,"gamma":0,"vega":0,"vanna":0,"charm":0,"d1":0,"d2":0}
    sqrtT=math.sqrt(T)
    d1=(math.log(S/K)+(r-q+0.5*sig*sig)*T)/(sig*sqrtT)
    d2=d1-sig*sqrtT
    nd1=_npdf(d1); eqT=math.exp(-q*T)
    delta = eqT*_ncdf(d1) if typ=="call" else eqT*(_ncdf(d1)-1)
    gamma = nd1*eqT/(S*sig*sqrtT)
    vega  = S*eqT*nd1*sqrtT
    vanna = -eqT*nd1*d2/sig
    core  = nd1*(2*(r-q)*T - d2*sig*sqrtT)/(2*T*sig*sqrtT)
    charm = (-eqT*(core+q*_ncdf(d1))) if typ=="call" else (-eqT*(core-q*_ncdf(-d1)))
    return {"delta":delta,"gamma":gamma,"vega":vega,"vanna":vanna,"charm":charm,"d1":d1,"d2":d2}

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
    spot=float(d.get("index_price",0)) if d else 0
    if spot>0: return spot
    # Deribit başarısızsa Binance'ten spot al (opsiyon hesabı yine Deribit OI'siyle yapılır)
    try:
        r=await cl.get("https://api.binance.com/api/v3/ticker/price",
            params={"symbol":f"{currency}USDT"})
        if r.status_code==200:
            return float(r.json().get("price",0))
    except Exception: pass
    return 0

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
            sgn=1 if typ=="call" else -1
            # Deribit greeks varsa onları kullan (mark), yoksa BS hesapla
            bs=_bs_greeks(spot,K,T,iv,typ)
            drb_g=tk.get("greeks",{}) or {}
            gamma=drb_g.get("gamma") if isinstance(drb_g.get("gamma"),(int,float)) else bs["gamma"]
            delta=drb_g.get("delta") if isinstance(drb_g.get("delta"),(int,float)) else bs["delta"]
            vanna=bs["vanna"]; charm=bs["charm"]
            # gex.js ile birebir: exposure formülleri (contractSize=1)
            gex = gamma*oi*spot*spot*0.01*sgn
            vex = vanna*oi*spot*0.01*sgn
            cex = charm*oi*spot*(1/365)*sgn
            opts.append({"strike":K,"type":typ,"oi":oi,"iv":iv,
                         "gex":gex,"vex":vex,"cex":cex,
                         "gamma":gamma,"delta":delta,"vanna":vanna,"charm":charm,
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
    # netGamma = call gex - put gex (dealer pozisyonu yaklaşık)
    for x in m.values():
        x["netGamma"]=round(x["callGex"]-x["putGex"],2)
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
    # Expected Move: ATM IV bazlı 1-haftalık ±1σ bant
    em_ust=em_alt=None
    if opts:
        atm=min(opts,key=lambda o:abs(o["strike"]-spot))
        iv=atm.get("iv",0)
        if iv>0:
            sigma=spot*iv*math.sqrt(7/365)  # 1 hafta
            em_ust=round(spot+sigma); em_alt=round(spot-sigma)
    return {"call_wall":cw,"put_wall":pw,"max_pain":mp,"zero_gamma":zg,
            "em_ust":em_ust,"em_alt":em_alt,
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


# ═══════════════════════════════════════════════════════════════════
#  v3 GENİŞLETME — Strike topografyası, Greekler, IV skew, 3× CVD
# ═══════════════════════════════════════════════════════════════════

def _vanna(S,K,T,sig):
    """Vanna = ∂²V/∂S∂σ — spot-vol çapraz duyarlılık."""
    if T<=0 or sig<=0 or S<=0 or K<=0: return 0
    d1=(math.log(S/K)+0.5*sig*sig*T)/(sig*math.sqrt(T))
    d2=d1-sig*math.sqrt(T)
    return -_npdf(d1)*d2/sig

def _charm(S,K,T,sig):
    """Charm = ∂Δ/∂t — delta'nın zaman bozunumu."""
    if T<=0 or sig<=0 or S<=0 or K<=0: return 0
    d1=(math.log(S/K)+0.5*sig*sig*T)/(sig*math.sqrt(T))
    d2=d1-sig*math.sqrt(T)
    return -_npdf(d1)*(2*0.5*sig*sig*T-d2*sig*math.sqrt(T))/(2*T*sig*math.sqrt(T))

async def strike_topografya(currency="BTC", vade="all"):
    """Strike bazında call/put OI + GEX dağılımı (görsel: Strike Topografyası)."""
    async with httpx.AsyncClient(timeout=30) as cl:
        spot=await _spot(cl,currency)
        if not spot: return {"error":"spot yok"}
        opts=await _tum_opsiyonlar(cl,spot,currency)
    if not opts: return {"error":"opsiyon yok"}
    strikes=_aggregate(opts,vade)
    # Spot etrafında ±%15 filtrele (okunabilirlik)
    lo,hi=spot*0.85,spot*1.15
    strikes=[s for s in strikes if lo<=s["strike"]<=hi]
    levels=_find_levels(_aggregate(opts,vade),spot,[o for o in opts if vade=="all" or o["expiryLabel"]==vade])
    return {"spot":spot,"vade":vade,"strikes":strikes,"levels":levels,
            "max_call_oi":max((s["callOI"] for s in strikes),default=0),
            "max_put_oi":max((s["putOI"] for s in strikes),default=0)}

async def toplu_greekler(currency="BTC"):
    """Net Gamma, Net Vanna, Net Charm — dealer pozisyonu (USD, gex.js formülü)."""
    async with httpx.AsyncClient(timeout=30) as cl:
        spot=await _spot(cl,currency)
        if not spot: return {"error":"spot yok"}
        opts=await _tum_opsiyonlar(cl,spot,currency)
    if not opts: return {"error":"opsiyon yok"}
    # Her opsiyonda zaten gex/vex/cex hesaplı (gex.js ile birebir)
    ng=sum(o.get("gex",0) for o in opts)
    nv=sum(o.get("vex",0) for o in opts)
    nc=sum(o.get("cex",0) for o in opts)
    return {"spot":spot,
            "net_gamma":round(ng/1e6,2),"net_vanna":round(nv/1e6,2),"net_charm":round(nc/1e6,2),
            "gamma_yorum":"Dealer net LONG gamma — fiyat yükselince satıp düşünce alır, volatiliteyi bastırır (mean-reversion). Spot Call Wall'a yaklaştıkça delta-hedge baskısı artar." if ng>0 else "Dealer net SHORT gamma — fiyat hareketini güçlendirir (momentum). Volatilite genişleme eğiliminde.",
            "vanna_yorum":"Net vanna pozitif — IV yükselişi dealer'ı net alıma iter, spot ile aynı yönde hareket eder." if nv>0 else "Net vanna negatif — IV yükselişi dealer satışı getirir, spot'a ters baskı.",
            "charm_yorum":"Net charm pozitif — vade yaklaştıkça (özellikle haftalık expiry) dealer alım baskısı, pin riski yukarı." if nc>0 else "Net charm negatif — vade yaklaştıkça dealer satış baskısı, pin riski aşağı."}

async def iv_skew(currency="BTC"):
    """ATM IV vade yapısı (log-moneyness interp) + gerçek 25Δ Risk Reversal (Hull §20.3)."""
    async with httpx.AsyncClient(timeout=30) as cl:
        spot=await _spot(cl,currency)
        if not spot: return {"error":"spot yok"}
        opts=await _tum_opsiyonlar(cl,spot,currency)
    if not opts: return {"error":"opsiyon yok"}
    now=int(time.time()*1000)
    vadeler={}
    for o in opts:
        gun=(o["expiryTs"]-now)/(86400*1000)
        if gun<0: continue
        vadeler.setdefault(o["expiryTs"],{"gun":gun,"T":o["T"],"opts":[]})
        vadeler[o["expiryTs"]]["opts"].append(o)
    atm_seri=[]; rr_seri=[]
    for ts,v in sorted(vadeler.items()):
        opts_v=v["opts"]; gun=v["gun"]; T=v["T"]
        # ── ATM IV: log-moneyness ağırlıklı interpolasyon (Gatheral) ──
        calls=sorted([o for o in opts_v if o["type"]=="call" and o["iv"]>0],key=lambda o:o["strike"])
        atmIV=None
        if calls:
            above=next((c for c in calls if c["strike"]>=spot),None)
            below=next((c for c in reversed(calls) if c["strike"]<spot),None)
            if above and below:
                lm_a=abs(math.log(above["strike"]/spot)); lm_b=abs(math.log(below["strike"]/spot))
                tot=lm_a+lm_b
                atmIV=(below["iv"]*(lm_a/tot)+above["iv"]*(lm_b/tot)) if tot>0 else (above["iv"]+below["iv"])/2
            elif above: atmIV=above["iv"]
            elif below: atmIV=below["iv"]
        if atmIV and 0.01<atmIV<5:
            atm_seri.append({"gun":round(gun),"iv":round(atmIV*100,1)})
        # ── 25Δ Risk Reversal: gerçek delta (N(d1)=0.25 call, N(d1)=0.75 put) ──
        if T>0.001:
            bestC=bestP=None; minCd=minPd=1e9
            for o in opts_v:
                if not o.get("iv") or o["iv"]<=0: continue
                sqrtT=math.sqrt(o["T"])
                d1=(math.log(spot/o["strike"])+0.5*o["iv"]*o["iv"]*o["T"])/(o["iv"]*sqrtT)
                cdelta=_ncdf(d1); pdelta=cdelta-1
                if o["type"]=="call":
                    dist=abs(cdelta-0.25)
                    if dist<minCd and dist<0.10: minCd=dist; bestC=o
                else:
                    dist=abs(pdelta+0.25)
                    if dist<minPd and dist<0.10: minPd=dist; bestP=o
            if bestC and bestP:
                rr=(bestP["iv"]-bestC["iv"])*100
                if abs(rr)<25:
                    rr_seri.append({"gun":round(gun),"rr":round(rr,2),
                        "put_iv":round(bestP["iv"]*100,1),"call_iv":round(bestC["iv"]*100,1)})
    return {"spot":spot,"atm_vade":atm_seri[:12],"risk_reversal":rr_seri[:12],
            "yapı":"Contango (uzun vade pahalı)" if len(atm_seri)>=2 and atm_seri[-1]["iv"]>atm_seri[0]["iv"] else "Backwardation"}

async def cvd_uclu(currency="BTC"):
    """3 CVD: Opsiyon CVD + Premium CVD (USD) + Whale CVD (≥50 BTC). Medyan çizgili."""
    async with httpx.AsyncClient(timeout=20) as cl:
        now=int(time.time()*1000); basla=now-3*86400*1000
        trades=await _drb(cl,"get_last_trades_by_currency_and_time",
            {"currency":currency,"kind":"option","start_timestamp":basla,"end_timestamp":now,"count":1000})
    if not trades or "trades" not in trades:
        return {"error":"trade yok"}
    tl=sorted(trades["trades"],key=lambda x:x["timestamp"])
    cvd=prem=whale=0
    cseri=[];pseri=[];wseri=[]
    for t in tl:
        nm=t["instrument_name"]; tip="C" if nm.endswith("-C") else "P"
        buy=t["direction"]=="buy"; amt=t["amount"]
        usd=amt*t.get("price",0)*t.get("index_price",0)  # yaklaşık USD premium
        yon=1 if (tip=="C" and buy) or (tip=="P" and not buy) else -1
        cvd+=amt*yon; cseri.append({"ts":t["timestamp"],"v":round(cvd,2)})
        prem+=usd*yon; pseri.append({"ts":t["timestamp"],"v":round(prem,0)})
        if amt>=50: whale+=amt*yon; wseri.append({"ts":t["timestamp"],"v":round(whale,2)})
    def medyan(seri):
        vals=sorted(s["v"] for s in seri)
        return round(vals[len(vals)//2],2) if vals else 0
    return {
        "opsiyon_cvd":{"seri":cseri[-200:],"guncel":round(cvd,2),"medyan":medyan(cseri),"yon":"YUKARI" if cvd>medyan(cseri) else "AŞAĞI"},
        "premium_cvd":{"seri":pseri[-200:],"guncel":round(prem,0),"medyan":medyan(pseri),"yon":"YUKARI" if prem>medyan(pseri) else "AŞAĞI"},
        "whale_cvd":{"seri":wseri[-200:],"guncel":round(whale,2),"medyan":medyan(wseri),"yon":"YUKARI" if whale>medyan(wseri) else "AŞAĞI"},
    }

async def islem_dagilimi(currency="BTC"):
    """Son 1000 işlem: Calls/Puts Buy/Sell + Block + STRIKE bazlı buy/sell volume + gamma."""
    async with httpx.AsyncClient(timeout=25) as cl:
        now=int(time.time()*1000); basla=now-86400*1000
        trades=await _drb(cl,"get_last_trades_by_currency_and_time",
            {"currency":currency,"kind":"option","start_timestamp":basla,"end_timestamp":now,"count":1000})
        spot=await _spot(cl,currency)
        opts=await _tum_opsiyonlar(cl,spot,currency) if spot else []
    if not trades or "trades" not in trades: return {"error":"trade yok"}
    d={"calls_buy":0,"calls_sell":0,"puts_buy":0,"puts_sell":0,
       "calls_buy_block":0,"puts_buy_block":0,"calls_sell_block":0,"puts_sell_block":0}
    BLOCK=25
    # Strike bazlı buy/sell volume (görsel 4 sağ panel)
    strike_vol={}  # strike -> {buy, sell}
    def _strike_from_name(nm):
        # BTC-27JUN25-65000-C → 65000
        try: return float(nm.split("-")[2])
        except Exception: return None
    for t in trades["trades"]:
        nm=t["instrument_name"]; tip="calls" if nm.endswith("-C") else "puts"
        yon="buy" if t["direction"]=="buy" else "sell"
        amt=t["amount"]
        d[f"{tip}_{yon}"]+=amt
        if amt>=BLOCK: d[f"{tip}_{yon}_block"]+=amt
        K=_strike_from_name(nm)
        if K:
            sv=strike_vol.setdefault(K,{"buy":0,"sell":0})
            sv[yon]+=amt
    for k in d: d[k]=round(d[k],1)
    # Strike volume listesi (spot etrafı ±%20)
    vol_list=[]
    if spot:
        lo,hi=spot*0.8,spot*1.2
        for K,v in sorted(strike_vol.items()):
            if lo<=K<=hi and (v["buy"]+v["sell"])>0:
                vol_list.append({"strike":K,"buy":round(v["buy"],1),"sell":round(v["sell"],1)})
    # Gamma per strike (görsel 4 sol panel) — opsiyon zincirinden
    gamma_list=[]
    if opts and spot:
        agg={}
        for o in opts:
            K=o["strike"]
            if not (spot*0.8<=K<=spot*1.2): continue
            g=agg.setdefault(K,{"strike":K,"callG":0,"putG":0})
            if o["type"]=="call": g["callG"]+=o.get("gex",0)
            else: g["putG"]+=o.get("gex",0)
        for K,g in sorted(agg.items()):
            net=g["callG"]+g["putG"]  # putG zaten negatif işaretli
            gamma_list.append({"strike":K,"net":round(net/1e6,3)})
    toplam_call=d["calls_buy"]+d["calls_sell"]
    toplam_put=d["puts_buy"]+d["puts_sell"]
    return {"dagilim":d,"call_toplam":round(toplam_call,1),"put_toplam":round(toplam_put,1),
            "pcr":round(toplam_put/toplam_call,2) if toplam_call else 0,
            "spot":spot,"strike_volume":vol_list,"gamma_per_strike":gamma_list,
            "calls_buy":d["calls_buy"],"calls_sell":d["calls_sell"],
            "puts_buy":d["puts_buy"],"puts_sell":d["puts_sell"],
            "call_blocks":round(d["calls_buy_block"]+d["calls_sell_block"],1)}
