"""
Theory Engine — OAR Premium v2 (Gelişmiş Backtest)
═══════════════════════════════════════════════════════════════════
Belge gereksinimleri:
  • Coin / gün / fib seçimi
  • Asia Range (TR 03:00-07:00 = UTC 00:00-04:00) — saat kontrollü
  • Gömülü indikatör skoru: HANGİ SKORDA hangi seviyeden tepki/kayıp
  • Research Agent: günlük/haftalık/aylık/çeyreklik tarihsel skorlar
  • "Şu konuda çalışılmalı" teori önerileri

Mantık:
  Her gün Asia range çekilir → seçilen fib seviyesi hesaplanır →
  fiyat o seviyeye temas ettiğinde O ANKİ indikatör skoru hesaplanır →
  4 saat sonrası WIN/LOSS → skor aralığına göre gruplanır.

  Sonuç: "Skor +40 üstünde temas → %72 başarı,
          skor -20 altında temas → %31 (kaçın)"
"""
import os, json, asyncio, httpx, math
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or ("/var/data" if Path("/var/data").exists() else "data"))
SONUC_FILE = DATA_DIR / "theory_engine_sonuc.json"
FAPI = "https://fapi.binance.com"

def _now(): return datetime.now(timezone.utc).isoformat()

# ─── İndikatör skoru (geçmiş mum penceresi için — basitleştirilmiş) ───
def _rsi(c, n=14):
    if len(c)<n+1: return 50
    g=l=0
    for i in range(-n,0):
        d=c[i]-c[i-1]
        if d>0:g+=d
        else:l-=d
    return 100-100/(1+g/l) if l else 100

def _skor_hesapla(k, idx):
    """idx mumundaki indikatör skoru (-100..+100). Geçmiş veriyle uyumlu."""
    if idx < 35: return 0, {}
    pencere = k[max(0,idx-50):idx+1]
    c=[float(x[4]) for x in pencere]
    v=[float(x[5]) for x in pencere]
    if len(c)<35: return 0, {}
    puan=0; ag=0; d={}
    # RSI
    rsi=_rsi(c)
    if rsi>70: p=-0.5
    elif rsi<30: p=0.7
    elif rsi>50: p=0.3
    else: p=-0.3
    puan+=p*10; ag+=10; d["RSI"]=round(rsi,1)
    # MACD (ema12-26 vs signal)
    def ema(vals,n):
        if len(vals)<n:return None
        kf=2/(n+1);e=sum(vals[:n])/n
        for x in vals[n:]:e=x*kf+e*(1-kf)
        return e
    e12,e26=ema(c,12),ema(c,26)
    if e12 and e26:
        macd=e12-e26
        puan+=(1 if macd>0 else -1)*10; ag+=10; d["MACD"]="+" if macd>0 else "-"
    # CVD (taker)
    cvd=0
    for x in pencere[-12:]:
        vol=float(x[5]);tb=float(x[9]);cvd+=tb-(vol-tb)
    cvd_prev=0
    for x in pencere[-18:-6]:
        vol=float(x[5]);tb=float(x[9]);cvd_prev+=tb-(vol-tb)
    puan+=(1 if cvd>cvd_prev else -1)*15; ag+=15; d["CVD"]="↑" if cvd>cvd_prev else "↓"
    # VWAP
    h=[float(x[2]) for x in pencere];l=[float(x[3]) for x in pencere]
    tpv=sum(((h[i]+l[i]+c[i])/3)*v[i] for i in range(len(pencere)))
    vs=sum(v)
    if vs:
        vwap=tpv/vs
        puan+=(0.6 if c[-1]>vwap else -0.6)*10; ag+=10; d["VWAP"]="üst" if c[-1]>vwap else "alt"
    # Hacim ivmesi (RVOL)
    vavg=sum(v[-20:])/20 if len(v)>=20 else None
    if vavg and v[-1]/vavg>1.5:
        puan+=0.4*8; ag+=8; d["RVOL"]=round(v[-1]/vavg,2)
    skor=max(-100,min(100,round(puan/ag*100))) if ag else 0
    return skor, d

async def _klines_tum(cl, sym, interval, gun):
    bitir=int(datetime.now(timezone.utc).timestamp()*1000)
    cursor=bitir-gun*86400_000; tum=[]
    for _ in range(80):
        r=await cl.get(f"{FAPI}/fapi/v1/klines",
            params={"symbol":sym,"interval":interval,"startTime":cursor,"limit":1000})
        d=r.json()
        if not isinstance(d,list) or not d: break
        tum.extend(d)
        if d[-1][0]>=bitir-60_000 or len(d)<1000: break
        cursor=d[-1][0]+1
        await asyncio.sleep(0.1)
    return tum

# ─── ANA GELİŞMİŞ BACKTEST ────────────────────────────────────────
async def gelismis_backtest(sym="BTCUSDT", gun=180, fib=0.618,
                            asia_baslangic=0, asia_bitis=4) -> dict:
    """
    fib: test edilecek seviye (0.377, 0.618, -0.272, 1.272 vb)
    asia_baslangic/bitis: UTC saat (0-4 = TR 03-07). Saat kaydırma kontrolü için ayarlanabilir.
    """
    async with httpx.AsyncClient(timeout=40) as cl:
        k = await _klines_tum(cl, sym, "15m", gun)
    if len(k) < 200:
        return {"hata": "veri yetersiz", "mum": len(k)}

    # Günlere ayır
    gunler = {}
    for i, x in enumerate(k):
        t = datetime.fromtimestamp(x[0]/1000, tz=timezone.utc)
        gunler.setdefault(t.date(), []).append((i, x, t))

    ornekler = []
    for tarih, mumlar in sorted(gunler.items()):
        asia = [(i,x) for i,x,t in mumlar if asia_baslangic <= t.hour < asia_bitis]
        if len(asia) < 8: continue
        hi = max(float(x[2]) for _,x in asia)
        lo = min(float(x[3]) for _,x in asia)
        rng = hi - lo
        if rng <= 0 or rng/lo*100 < 0.4: continue
        sev = lo + rng*fib
        yon = "LONG" if fib < 0.5 else "SHORT"

        islem = [(i,x,t) for i,x,t in mumlar if asia_bitis <= t.hour < 22]
        for i,x,t in islem:
            h,l,cl_ = float(x[2]),float(x[3]),float(x[4])
            if l <= sev <= h:  # temas
                skor, ind_detay = _skor_hesapla(k, i)
                # 16 mum (4 saat) sonrası değerlendirme
                j = min(i+16, len(k)-1)
                cikis = float(k[j][4])
                pct = (cikis-cl_)/cl_*100
                if yon == "SHORT": pct = -pct
                out = "WIN" if pct>=0.5 else "LOSS" if pct<=-0.5 else "FLAT"
                ornekler.append({
                    "tarih": t.isoformat()[:16], "yon": yon, "skor": skor,
                    "outcome": out, "pct": round(pct,2),
                    "hafta": t.isocalendar()[1], "ay": t.month,
                    "ceyrek": (t.month-1)//3+1, "ind": ind_detay,
                })
                break  # günde 1

    if not ornekler:
        return {"hata": "temas bulunamadı", "sym": sym, "gun": gun, "fib": fib}

    # ── SKOR ARALIĞI ANALİZİ (belge: hangi skorda hangi tepki) ──
    aralıklar = {
        "skor>+40 (güçlü teyit)": [o for o in ornekler if o["skor"]>40],
        "skor +15..+40 (zayıf teyit)": [o for o in ornekler if 15<o["skor"]<=40],
        "skor -15..+15 (nötr)": [o for o in ornekler if -15<=o["skor"]<=15],
        "skor -40..-15 (zayıf ters)": [o for o in ornekler if -40<=o["skor"]<-15],
        "skor<-40 (güçlü ters)": [o for o in ornekler if o["skor"]<-40],
    }
    skor_analiz = {}
    for ad, grup in aralıklar.items():
        d = [o for o in grup if o["outcome"] in ("WIN","LOSS")]
        if not d: continue
        w = sum(1 for o in d if o["outcome"]=="WIN")
        skor_analiz[ad] = {"ornek": len(grup), "degerli": len(d),
            "win_rate": round(w/len(d)*100,1), "ort_pct": round(sum(o["pct"] for o in d)/len(d),2)}

    # ── DÖNEMSEL (günlük/haftalık/aylık/çeyreklik) ──
    def donem_wr(key):
        gr={}
        for o in ornekler:
            if o["outcome"] not in ("WIN","LOSS"): continue
            gr.setdefault(o[key],{"w":0,"t":0})
            gr[o[key]]["t"]+=1
            if o["outcome"]=="WIN": gr[o[key]]["w"]+=1
        return {str(k):{"win_rate":round(v["w"]/v["t"]*100,1),"ornek":v["t"]} for k,v in sorted(gr.items())}
    donemsel = {"aylik": donem_wr("ay"), "ceyreklik": donem_wr("ceyrek"), "haftalik": donem_wr("hafta")}

    # ── GENEL ──
    d = [o for o in ornekler if o["outcome"] in ("WIN","LOSS")]
    w = sum(1 for o in d if o["outcome"]=="WIN")
    pnls = [o["pct"] for o in d]
    kar = sum(x for x in pnls if x>0); zarar = abs(sum(x for x in pnls if x<0))

    # ── RESEARCH ÖNERİSİ ──
    oneriler = []
    if skor_analiz:
        en_iyi = max(skor_analiz, key=lambda a: skor_analiz[a]["win_rate"])
        en_kotu = min(skor_analiz, key=lambda a: skor_analiz[a]["win_rate"])
        oneriler.append(f"✅ '{en_iyi}' aralığında temas → %{skor_analiz[en_iyi]['win_rate']} başarı. Bu skorda fib {fib} seviyesine GÜVEN.")
        if skor_analiz[en_kotu]["win_rate"] < 40:
            oneriler.append(f"⛔ '{en_kotu}' aralığında temas → %{skor_analiz[en_kotu]['win_rate']}. Bu skorda fib {fib} KAÇINILMALI.")
    genel_wr = round(w/len(d)*100,1) if d else 0
    if genel_wr >= 60:
        oneriler.append(f"📊 fib {fib} genel %{genel_wr} — bu seviye {sym} için ÇALIŞIYOR, Confirmed adayı.")
    elif genel_wr < 45:
        oneriler.append(f"📉 fib {fib} genel %{genel_wr} — tek başına zayıf, indikatör skoruyla filtrelenmeli.")

    sonuc = {
        "sym": sym, "gun": gun, "fib": fib,
        "asia_saat": f"UTC {asia_baslangic}-{asia_bitis} (TR {asia_baslangic+3}-{asia_bitis+3})",
        "tarih": _now(), "toplam_temas": len(ornekler), "degerli": len(d),
        "genel_win_rate": genel_wr,
        "profit_factor": round(kar/zarar,2) if zarar else 0,
        "ort_pct": round(sum(pnls)/len(pnls),2) if pnls else 0,
        "skor_analiz": skor_analiz,
        "donemsel": donemsel,
        "oneriler": oneriler,
        "son_10": ornekler[-10:],
    }
    # Kaydet (silinmez)
    db = json.loads(SONUC_FILE.read_text()) if SONUC_FILE.exists() else {"testler": []}
    db["testler"].append({k:v for k,v in sonuc.items() if k not in ("son_10",)})
    db["testler"] = db["testler"][-100:]
    SONUC_FILE.write_text(json.dumps(db, ensure_ascii=False, indent=2))
    return sonuc

def gecmis_sonuclar(limit=20):
    db = json.loads(SONUC_FILE.read_text()) if SONUC_FILE.exists() else {"testler": []}
    return db["testler"][-limit:]


# ═══════════════════════════════════════════════════════════════════
#  v3 — OTOMATİK HİPOTEZ ÜRETECİ (coin/fib seçimi yok)
# ═══════════════════════════════════════════════════════════════════
# Belge: "Benim fib seçmem ya da coin seçmem önemli değil. İptal et.
#  Tarihte ne kadar geriye gidebiliyorsa o kadar gidip tüm enstrümanlarla
#  denesin, hipotezler üretsin."

# Sadece bu 6 enstrüman trade ediliyor
ENSTRUMANLAR = {
    "BTCUSDT": "Bitcoin", "ETHUSDT": "Ethereum",
    "PAXGUSDT": "Altın (PAXG)", "XAUTUSDT": "Altın (XAUT)",
    # SP500/Nasdaq/Gümüş Binance'te yok → Yahoo ile ayrı ele alınır (şimdilik kripto proxy)
}
# Test edilecek fib seviyeleri (otomatik)
FIB_SEVIYELER = [0.236, 0.377, 0.5, 0.618, 0.786, -0.272, 1.272, 1.618]
HIPOTEZ_FILE = DATA_DIR / "otomatik_hipotezler.json"

async def otomatik_hipotez_uret(sym="BTCUSDT", gun=365):
    """Bir enstrüman için TÜM fib seviyelerini otomatik tarar, en iyi hipotezleri çıkarır."""
    bulgular = []
    for fib in FIB_SEVIYELER:
        try:
            r = await gelismis_backtest(sym, gun, fib, 0, 4)
            if r.get("hata"): continue
            wr = r.get("genel_win_rate", 0)
            pf = r.get("profit_factor", 0)
            ornek = r.get("degerli", 0)
            if ornek < 20: continue
            # Skor analizinden en iyi aralığı bul
            sa = r.get("skor_analiz", {})
            en_iyi_aralik = None; en_iyi_wr = 0
            for ad, s in sa.items():
                if s.get("degerli", 0) >= 8 and s.get("win_rate", 0) > en_iyi_wr:
                    en_iyi_wr = s["win_rate"]; en_iyi_aralik = ad
            durum = "Confirmed" if (wr>=60 and pf>=1.5 and ornek>=30) else "Rejected" if (wr<45 or pf<1) else "Testing"
            bulgular.append({
                "fib": fib, "win_rate": wr, "profit_factor": pf, "ornek": ornek,
                "durum": durum, "en_iyi_skor_aralik": en_iyi_aralik, "en_iyi_skor_wr": en_iyi_wr,
                "donemsel": r.get("donemsel", {}),
            })
        except Exception:
            continue
        await asyncio.sleep(0.3)
    bulgular.sort(key=lambda x: (x["win_rate"], x["profit_factor"]), reverse=True)
    return {"sym": sym, "ad": ENSTRUMANLAR.get(sym, sym), "gun": gun,
            "tarih": _now(), "bulgular": bulgular}

async def tum_enstruman_tara(gun=365):
    """Tüm enstrümanları otomatik tarar — sistemin kendi hipotez üretimi."""
    sonuc = {}
    for sym in ["BTCUSDT", "ETHUSDT", "PAXGUSDT"]:
        try:
            sonuc[sym] = await otomatik_hipotez_uret(sym, gun)
        except Exception as e:
            sonuc[sym] = {"hata": str(e)[:60]}
    # En güçlü bulguları topla — Confirmed + Testing (sadece Confirmed çoğu zaman boş kalıyor)
    en_iyiler = []
    tum_bulgular = []
    hata_var = []
    for sym, r in sonuc.items():
        if r.get("hata"):
            hata_var.append(f"{sym}: {r['hata']}")
        if r.get("bulgular"):
            for b in r["bulgular"]:
                kayit = {"sym": sym, "ad": r["ad"], **b}
                tum_bulgular.append(kayit)
                if b["durum"] in ("Confirmed", "Testing"):
                    en_iyiler.append(kayit)
    en_iyiler.sort(key=lambda x: (x["durum"] != "Confirmed", -x["win_rate"], -x.get("profit_factor", 0)))
    # Hiç Confirmed/Testing yoksa en yüksek WR'li 3 bulguyu yine de göster
    if not en_iyiler and tum_bulgular:
        tum_bulgular.sort(key=lambda x: (-x["win_rate"], -x.get("profit_factor", 0)))
        en_iyiler = tum_bulgular[:3]
    out = {"tarih": _now(), "gun": gun, "enstrumanlar": sonuc,
           "en_iyi_hipotezler": en_iyiler[:8],
           "tarama_durumu": ("hata" if hata_var and not tum_bulgular else "ok"),
           "hatalar": hata_var}
    HIPOTEZ_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    return out

def son_hipotezler():
    if HIPOTEZ_FILE.exists():
        return json.loads(HIPOTEZ_FILE.read_text())
    return {"durum": "henuz_calismadi", "en_iyi_hipotezler": []}

async def hipotez_loop():
    """Günde bir tüm enstrümanları otomatik tarar."""
    await asyncio.sleep(600)
    while True:
        try:
            await tum_enstruman_tara(365)
            print("[Hipotez] ✅ Tüm enstrümanlar tarandı")
        except Exception as e:
            print(f"[Hipotez] {str(e)[:60]}")
        await asyncio.sleep(86400)  # 24 saat
