"""
Indicator Engine — OAR Premium v2
═══════════════════════════════════════════════════════════════════
Sistemin TEMELİ. Tüm sayfalar (Komuta skoru, bot derecelendirme,
Theory Lab, sinyaller) bu tek motordan beslenir.

Yalnızca ÜCRETSİZ Binance REST verisiyle hesaplanabilen ~30 indikatör.
Tick/L2 gerektirenler (Footprint, Bookmap, DOM, Liquidation) HARİÇ —
bunlar "veri_yok" olarak işaretlenir, sahte üretilmez.

Tek giriş: await analiz(symbol) → tüm indikatörler + 5m skor + yorum
"""
import os, asyncio, httpx, math
from datetime import datetime, timezone

FAPI = "https://fapi.binance.com"
SPOT = "https://api.binance.com"

# ─── Yardımcı matematik ───────────────────────────────────────────
def _sma(v, n):
    return sum(v[-n:]) / n if len(v) >= n else None

def _ema(v, n):
    if len(v) < n: return None
    k = 2/(n+1); e = sum(v[:n])/n
    for x in v[n:]: e = x*k + e*(1-k)
    return e

def _rsi(closes, n=14):
    if len(closes) < n+1: return None
    g=l=0
    for i in range(-n,0):
        d=closes[i]-closes[i-1]
        if d>0:g+=d
        else:l-=d
    if l==0:return 100.0
    return 100-100/(1+g/l)

def _stdev(v):
    if len(v)<2:return 0
    m=sum(v)/len(v)
    return math.sqrt(sum((x-m)**2 for x in v)/len(v))

# ─── Binance veri çekme ───────────────────────────────────────────
async def _klines(cl, base, sym, interval, limit=100):
    ep = "/api/v3/klines" if "api.binance" in base else "/fapi/v1/klines"
    r = await cl.get(f"{base}{ep}", params={"symbol":sym,"interval":interval,"limit":limit})
    d = r.json()
    return d if isinstance(d,list) else []

# ─── İNDİKATÖR HESAPLARI ──────────────────────────────────────────
def hesapla_hacim_grubu(k):
    """Volume, RVOL, VWMA, OBV, A/D, CMF, MFI, PVT, VROC, EFI"""
    if len(k)<30: return {}
    o=[float(x[1]) for x in k]; h=[float(x[2]) for x in k]
    l=[float(x[3]) for x in k]; c=[float(x[4]) for x in k]; v=[float(x[5]) for x in k]
    out={}
    # RVOL — son hacim / 20 ort
    vavg=_sma(v,20)
    out["RVOL"]={"deger":round(v[-1]/vavg,2) if vavg else None,"yorum":"Yüksek hacim ilgisi" if vavg and v[-1]/vavg>1.5 else "Normal"}
    # VWMA
    vw=sum(c[i]*v[i] for i in range(-20,0))/sum(v[-20:]) if sum(v[-20:]) else None
    out["VWMA"]={"deger":round(vw,2) if vw else None,"pozisyon":"üstünde" if vw and c[-1]>vw else "altında"}
    # OBV
    obv=0;obv_seri=[0]
    for i in range(1,len(c)):
        if c[i]>c[i-1]:obv+=v[i]
        elif c[i]<c[i-1]:obv-=v[i]
        obv_seri.append(obv)
    obv_egim=obv_seri[-1]-obv_seri[-10] if len(obv_seri)>=10 else 0
    out["OBV"]={"yon":"yukarı" if obv_egim>0 else "aşağı","yorum":"Hacim alımı destekliyor" if obv_egim>0 else "Hacim satışı destekliyor"}
    # A/D (Accumulation/Distribution)
    ad=0
    for i in range(len(k)):
        rng=h[i]-l[i]
        if rng>0:ad+=((c[i]-l[i])-(h[i]-c[i]))/rng*v[i]
    out["A/D"]={"deger":round(ad,1),"yon":"birikim" if ad>0 else "dağıtım"}
    # CMF (21)
    mfv=0;vsum=0
    for i in range(-21,0):
        rng=h[i]-l[i]
        if rng>0:mfv+=((c[i]-l[i])-(h[i]-c[i]))/rng*v[i];vsum+=v[i]
    cmf=mfv/vsum if vsum else 0
    out["CMF"]={"deger":round(cmf,3),"yorum":"Para girişi" if cmf>0.05 else "Para çıkışı" if cmf<-0.05 else "Nötr"}
    # MFI (14)
    pos=neg=0
    for i in range(-14,0):
        tp=(h[i]+l[i]+c[i])/3; tp0=(h[i-1]+l[i-1]+c[i-1])/3
        rmf=tp*v[i]
        if tp>tp0:pos+=rmf
        else:neg+=rmf
    mfi=100-100/(1+pos/neg) if neg else 100
    out["MFI"]={"deger":round(mfi,1),"yorum":"Aşırı alım" if mfi>80 else "Aşırı satım" if mfi<20 else "Nötr"}
    # PVT
    pvt=0
    for i in range(1,len(c)):
        if c[i-1]:pvt+=(c[i]-c[i-1])/c[i-1]*v[i]
    out["PVT"]={"deger":round(pvt,1)}
    # VROC
    vroc=(v[-1]-v[-13])/v[-13]*100 if len(v)>=13 and v[-13] else 0
    out["VROC"]={"deger":round(vroc,1)}
    # EFI (Elder Force, 13 ema)
    fi=[(c[i]-c[i-1])*v[i] for i in range(1,len(c))]
    efi=_ema(fi,13)
    out["EFI"]={"deger":round(efi,1) if efi else None,"yon":"alıcı" if efi and efi>0 else "satıcı"}
    return out

def hesapla_vwap_grubu(k):
    """Daily VWAP + std bands, Rolling VWAP"""
    if len(k)<20:return {}
    h=[float(x[2]) for x in k];l=[float(x[3]) for x in k]
    c=[float(x[4]) for x in k];v=[float(x[5]) for x in k]
    out={}
    # Rolling VWAP (tüm pencere)
    tpv=sum(((h[i]+l[i]+c[i])/3)*v[i] for i in range(len(k)))
    vs=sum(v)
    vwap=tpv/vs if vs else None
    if vwap:
        # std bands
        tps=[(h[i]+l[i]+c[i])/3 for i in range(len(k))]
        var=sum(v[i]*(tps[i]-vwap)**2 for i in range(len(k)))/vs
        sd=math.sqrt(var)
        out["VWAP"]={"deger":round(vwap,2),"pozisyon":"üstünde" if c[-1]>vwap else "altında",
                     "ust_band":round(vwap+sd,2),"alt_band":round(vwap-sd,2),
                     "yorum":"Fiyat VWAP üstü — alıcı kontrolü" if c[-1]>vwap else "Fiyat VWAP altı — satıcı kontrolü"}
    return out

async def hesapla_cvd_grubu(cl, sym):
    """Spot CVD, Futures CVD, fark (move source)"""
    out={}
    async def cvd(base):
        k=await _klines(cl,base,sym,"5m",24)
        c=0;seri=[]
        for x in k:
            vol=float(x[5]);tb=float(x[9]);c+=tb-(vol-tb);seri.append(c)
        return seri
    try:
        spot=await cvd(SPOT);fut=await cvd(FAPI)
        if spot and fut:
            out["Spot CVD"]={"deger":round(spot[-1],1),"egim":"yukarı" if spot[-1]>spot[-6] else "aşağı"}
            out["Futures CVD"]={"deger":round(fut[-1],1),"egim":"yukarı" if fut[-1]>fut[-6] else "aşağı"}
            ts=abs(spot[-1])+abs(fut[-1])
            spot_pct=abs(spot[-1])/ts*100 if ts else 50
            out["Move Source"]={"spot_pct":round(spot_pct,1),
                "kaynak":"SPOT-DRIVEN" if spot_pct>60 else "FUTURES-DRIVEN" if spot_pct<40 else "DENGELİ",
                "yorum":"Spot baskın — kalıcı hareket" if spot_pct>60 else "Vadeli baskın — kırılgan" if spot_pct<40 else "Dengeli"}
    except Exception:pass
    return out

async def hesapla_oi_funding(cl, sym):
    """OI, OI Delta, Funding, Basis, Long/Short Ratio"""
    out={}
    try:
        r=await cl.get(f"{FAPI}/futures/data/openInterestHist",params={"symbol":sym,"period":"5m","limit":12})
        oi=r.json()
        if isinstance(oi,list) and len(oi)>=2:
            vals=[float(x["sumOpenInterestValue"]) for x in oi]
            delta=(vals[-1]-vals[-2])/vals[-2]*100 if vals[-2] else 0
            trend=(vals[-1]-vals[0])/vals[0]*100 if vals[0] else 0
            out["Open Interest"]={"deger":round(vals[-1]/1e6,1),"birim":"M$","delta_pct":round(delta,2),
                "trend_pct":round(trend,2),"yorum":"OI artıyor — yeni pozisyon" if delta>0.2 else "OI azalıyor — kapanış" if delta<-0.2 else "Yatay"}
    except Exception:pass
    try:
        r=await cl.get(f"{FAPI}/fapi/v1/premiumIndex",params={"symbol":sym})
        d=r.json()
        fund=float(d.get("lastFundingRate",0))*100
        mark=float(d.get("markPrice",0));idx=float(d.get("indexPrice",0))
        basis=(mark-idx)/idx*100 if idx else 0
        out["Funding"]={"deger":round(fund,4),"yorum":"Longlar ödüyor (aşırı iyimser)" if fund>0.03 else "Shortlar ödüyor (aşırı kötümser)" if fund<-0.03 else "Dengeli"}
        out["Basis"]={"deger":round(basis,3),"yorum":"Contango (vadeli pahalı)" if basis>0 else "Backwardation"}
    except Exception:pass
    try:
        r=await cl.get(f"{FAPI}/futures/data/globalLongShortAccountRatio",params={"symbol":sym,"period":"5m","limit":1})
        ls=r.json()
        if isinstance(ls,list) and ls:
            ratio=float(ls[-1]["longShortRatio"])
            out["Long/Short Ratio"]={"deger":round(ratio,2),"yorum":"Long ağırlıklı" if ratio>1.2 else "Short ağırlıklı" if ratio<0.8 else "Dengeli"}
    except Exception:pass
    return out

async def hesapla_coinbase_premium(cl, sym):
    """Coinbase Premium — ABD kurumsal talep göstergesi (ÜCRETSİZ)"""
    out={}
    try:
        base=sym.replace("USDT","")
        # Binance fiyat
        rb=await cl.get(f"{SPOT}/api/v3/ticker/price",params={"symbol":sym})
        bp=float(rb.json()["price"])
        # Coinbase fiyat
        rc=await cl.get(f"https://api.coinbase.com/v2/prices/{base}-USD/spot")
        cp=float(rc.json()["data"]["amount"])
        gap=(cp-bp)/bp*100
        out["Coinbase Premium"]={"deger":round(gap,3),"binance":round(bp,2),"coinbase":round(cp,2),
            "yorum":"ABD kurumsal ALIM baskısı" if gap>0.05 else "ABD SATIŞ baskısı" if gap<-0.05 else "Nötr"}
    except Exception:pass
    return out

def hesapla_momentum(k):
    """RSI, MACD, Volume-Weighted RSI, VPCI"""
    if len(k)<35:return {}
    c=[float(x[4]) for x in k];v=[float(x[5]) for x in k]
    out={}
    rsi=_rsi(c)
    out["RSI"]={"deger":round(rsi,1) if rsi else None,"yorum":"Aşırı alım" if rsi and rsi>70 else "Aşırı satım" if rsi and rsi<30 else "Nötr"}
    # MACD
    e12=_ema(c,12);e26=_ema(c,26)
    if e12 and e26:
        macd=e12-e26
        macds=[]
        for i in range(-9,0):
            seg=c[:len(c)+i+1]
            if len(seg)>=26:macds.append((_ema(seg,12) or 0)-(_ema(seg,26) or 0))
        sig=sum(macds)/len(macds) if macds else 0
        out["MACD"]={"hist":round(macd-sig,4),"yon":"yukarı momentum" if macd>sig else "aşağı momentum"}
    # VPCI
    vwma=sum(c[i]*v[i] for i in range(-20,0))/sum(v[-20:]) if sum(v[-20:]) else None
    sma=_sma(c,20)
    if vwma and sma:
        out["VPCI"]={"deger":round(vwma-sma,2),"yorum":"Hacim fiyatı teyit ediyor" if vwma>sma else "Hacim-fiyat uyumsuz"}
    return out

# ─── SKORLAMA (5m net yön) ────────────────────────────────────────
def skorla(ind):
    """Tüm indikatörlerden -100..+100 arası net yön skoru + yorum."""
    puan=0; agirlik=0;detay=[]
    def ekle(ad,p,w,not_):
        nonlocal puan,agirlik
        puan+=p*w;agirlik+=w;detay.append({"ind":ad,"katki":round(p*w,1),"not":not_})
    # CVD move source
    ms=ind.get("Move Source",{})
    if ms.get("kaynak")=="SPOT-DRIVEN":ekle("Move Source",1,20,"Spot baskın (güçlü)")
    elif ms.get("kaynak")=="FUTURES-DRIVEN":ekle("Move Source",-0.3,20,"Vadeli baskın (kırılgan)")
    # Spot CVD
    sc=ind.get("Spot CVD",{})
    if sc.get("egim")=="yukarı":ekle("Spot CVD",1,15,"CVD yukarı")
    elif sc.get("egim")=="aşağı":ekle("Spot CVD",-1,15,"CVD aşağı")
    # OI
    oi=ind.get("Open Interest",{})
    if oi.get("delta_pct",0)>0.2:ekle("OI",0.6,12,"OI artıyor")
    elif oi.get("delta_pct",0)<-0.2:ekle("OI",-0.4,12,"OI azalıyor")
    # RSI
    rsi=ind.get("RSI",{}).get("deger")
    if rsi:
        if rsi>70:ekle("RSI",-0.5,10,"Aşırı alım")
        elif rsi<30:ekle("RSI",0.7,10,"Aşırı satım (tepki)")
        elif rsi>50:ekle("RSI",0.3,10,"Momentum yukarı")
        else:ekle("RSI",-0.3,10,"Momentum aşağı")
    # MACD
    mh=ind.get("MACD",{}).get("hist")
    if mh is not None:ekle("MACD",1 if mh>0 else -1,10,ind["MACD"]["yon"])
    # VWAP
    vw=ind.get("VWAP",{})
    if vw.get("pozisyon")=="üstünde":ekle("VWAP",0.6,10,"VWAP üstü")
    elif vw.get("pozisyon")=="altında":ekle("VWAP",-0.6,10,"VWAP altı")
    # CMF
    cmf=ind.get("CMF",{}).get("deger")
    if cmf is not None:ekle("CMF",1 if cmf>0.05 else -1 if cmf<-0.05 else 0,8,"Para akışı")
    # Coinbase Premium
    cb=ind.get("Coinbase Premium",{}).get("deger")
    if cb is not None:ekle("Coinbase Premium",1 if cb>0.05 else -1 if cb<-0.05 else 0,10,"ABD talebi")
    # Funding (ters — aşırı funding dönüş riski)
    fund=ind.get("Funding",{}).get("deger")
    if fund is not None:
        if fund>0.05:ekle("Funding",-0.3,5,"Aşırı long (dönüş riski)")
        elif fund<-0.05:ekle("Funding",0.3,5,"Aşırı short (squeeze)")
    norm=round(puan/agirlik) if agirlik else 0
    skor=max(-100,min(100,norm))
    if skor>=40:yon="GÜÇLÜ LONG";renk="#1ff0ad"
    elif skor>=15:yon="ZAYIF LONG";renk="#7af0c4"
    elif skor<=-40:yon="GÜÇLÜ SHORT";renk="#ff5d6c"
    elif skor<=-15:yon="ZAYIF SHORT";renk="#ff8a3c"
    else:yon="NÖTR";renk="#ffb13c"
    return {"skor":skor,"yon":yon,"renk":renk,"detay":sorted(detay,key=lambda x:abs(x["katki"]),reverse=True)}

# ─── ANA GİRİŞ ────────────────────────────────────────────────────
async def analiz(symbol="BTCUSDT", interval="5m"):
    async with httpx.AsyncClient(timeout=20) as cl:
        k = await _klines(cl, FAPI, symbol, interval, 100)
        if len(k)<35:
            return {"hata":"veri yetersiz","symbol":symbol}
        ind={}
        ind.update(hesapla_hacim_grubu(k))
        ind.update(hesapla_vwap_grubu(k))
        ind.update(hesapla_momentum(k))
        ind.update(await hesapla_cvd_grubu(cl,symbol))
        ind.update(await hesapla_oi_funding(cl,symbol))
        ind.update(await hesapla_coinbase_premium(cl,symbol))
    skor=skorla(ind)
    # Veri yok işaretlenenler (dürüstlük)
    yok=["Footprint","Bookmap/DOM","Liquidation Heatmap","Bid/Ask Delta","Order Book Imbalance"]
    return {
        "symbol":symbol,"interval":interval,"tarih":datetime.now(timezone.utc).isoformat(),
        "fiyat":float(k[-1][4]),
        "indikatorler":ind,"skor":skor,
        "veri_yok":yok,  # tick/L2 gerektiren — ücretsiz API'de yok
        "indikator_sayisi":len(ind),
    }

