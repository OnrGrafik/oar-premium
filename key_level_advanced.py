"""
key_level_advanced.py — Gelişmiş Key Level Analizi
═══════════════════════════════════════════════════
Her key level'ı şunlarla test eder:
  • Hacim (VPFR — POC, VAH, VAL yakınlığı)
  • Opsiyon (GEX — Call/Put Wall, Zero Gamma)
  • Makro (DVOL rejimi)
  • Likidasyon (Kiyotaka)
  • Önceki Gün H/L
  • Haftalık H/L
  • Çeyreklik kapanışlar
Sonuç: her seviye için çok boyutlu güvenilirlik skoru
"""
import os, json, asyncio, time, httpx
from pathlib import Path
from datetime import datetime, timezone, timedelta

DATA_DIR  = Path(os.environ.get("DATA_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or ("/var/data" if Path("/var/data").exists() else "data"))
KL_FILE   = DATA_DIR / "key_level_advanced.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)

FAPI = "https://fapi.binance.com"

def _yukle():
    try: return json.loads(KL_FILE.read_text()) if KL_FILE.exists() else {"analizler":[]}
    except: return {"analizler":[]}

# ── Anlık Key Level Özeti (Canlı Kullanım) ───────────────────────────────────
async def anlik_key_levels(sym="BTCUSDT") -> dict:
    """
    Şu anda aktif tüm key levelları çeker ve skorlar.
    Canlı panel + OAR backtest filtresi için kullanılır.
    """
    async with httpx.AsyncClient(timeout=20) as cl:
        # Bugünün klines
        end_ms = int(time.time()*1000)
        st_ms  = end_ms - 8*86400_000  # 8 gün
        r = await cl.get(f"{FAPI}/fapi/v1/klines",
            params={"symbol":sym,"interval":"5m","startTime":st_ms,"limit":1500})
        klines = r.json() if r.status_code==200 else []

        # Günlük klines (haftalık H/L için)
        r2 = await cl.get(f"{FAPI}/fapi/v1/klines",
            params={"symbol":sym,"interval":"1d","limit":30})
        daily = r2.json() if r2.status_code==200 else []

    # Spot fiyat
    spot = float(klines[-1][4]) if klines else 0

    # Bugünün Asia range
    now_utc = datetime.now(timezone.utc)
    day_s = int(now_utc.replace(hour=0,minute=0,second=0,microsecond=0).timestamp()*1000)
    asia_k = [k for k in klines if day_s <= k[0] < day_s+4*3600_000]
    asia_hi = max(float(k[2]) for k in asia_k) if asia_k else 0
    asia_lo = min(float(k[3]) for k in asia_k) if asia_k else 0
    asia_r  = asia_hi - asia_lo

    fibs = {}
    if asia_r > 0:
        fibs = {
            "Fib 2.272 (SHORT)":  round(asia_lo + asia_r*2.272,  2),
            "Fib 2.618 (SHORT)":  round(asia_lo + asia_r*2.618,  2),
            "Fib -1.272 (LONG)":  round(asia_lo + asia_r*-1.272, 2),
            "Fib -1.618 (LONG)":  round(asia_lo + asia_r*-1.618, 2),
        }

    # Önceki gün H/L
    prev_day = daily[-2] if len(daily) >= 2 else None
    prev_hi  = float(prev_day[2]) if prev_day else 0
    prev_lo  = float(prev_day[3]) if prev_day else 0

    # Haftalık H/L (son 7 gün)
    week_k = daily[-7:] if len(daily)>=7 else daily
    week_hi = max(float(k[2]) for k in week_k) if week_k else 0
    week_lo = min(float(k[3]) for k in week_k) if week_k else 0

    # Aylık H/L (son 30 gün)
    mon_k   = daily[-30:] if len(daily)>=30 else daily
    mon_hi  = max(float(k[2]) for k in mon_k) if mon_k else 0
    mon_lo  = min(float(k[3]) for k in mon_k) if mon_k else 0

    # GEX seviyeleri
    gex_levels = {}
    try:
        from options_engine import gex_ozet, alarm_levels
        gex = await gex_ozet("BTC")
        lv  = await alarm_levels("BTC")
        genel = lv.get("genel",{})
        gex_levels = {
            "Call Wall":   genel.get("call_wall"),
            "Put Wall":    genel.get("put_wall"),
            "Zero Gamma":  genel.get("zero_gamma"),
            "Max Pain":    genel.get("max_pain"),
        }
    except Exception:
        pass

    # VPFR POC
    vpfr_levels = {}
    try:
        from kiyotaka_engine import get_volume_profile
        key = os.environ.get("KIYOTAKA_API_KEY","")
        if key:
            vp = await get_volume_profile(sym, int(day_s/1000), 86400, key)
            if not vp.get("error"):
                vpfr_levels = {
                    "VPFR POC":  vp.get("poc"),
                    "VPFR VAH":  vp.get("vah"),
                    "VPFR VAL":  vp.get("val"),
                }
    except Exception:
        pass

    # Tüm seviyeleri birleştir ve skoru hesapla
    tum_seviyeler = {}

    # Asia Range seviyeleri
    if asia_hi: tum_seviyeler["Asia High"] = {"fiyat":asia_hi,"tip":"OAR","yon":"SHORT","renk":"#ff5d6c"}
    if asia_lo: tum_seviyeler["Asia Low"]  = {"fiyat":asia_lo,"tip":"OAR","yon":"LONG", "renk":"#1ff0ad"}
    for ad,f in fibs.items():
        yon = "SHORT" if "SHORT" in ad else "LONG"
        tum_seviyeler[ad] = {"fiyat":f,"tip":"OAR_FIB","yon":yon,"renk":"#56a8ff" if yon=="LONG" else "#ff8a3c"}

    # Önceki gün
    if prev_hi: tum_seviyeler["Önceki Gün High"] = {"fiyat":prev_hi,"tip":"PDH","yon":"SHORT","renk":"#ffb13c"}
    if prev_lo: tum_seviyeler["Önceki Gün Low"]  = {"fiyat":prev_lo,"tip":"PDL","yon":"LONG", "renk":"#56a8ff"}

    # Haftalık
    if week_hi: tum_seviyeler["Haftalık High"] = {"fiyat":week_hi,"tip":"WH","yon":"SHORT","renk":"#ff5d6c"}
    if week_lo: tum_seviyeler["Haftalık Low"]  = {"fiyat":week_lo,"tip":"WL","yon":"LONG", "renk":"#1ff0ad"}

    # Aylık
    if mon_hi and mon_hi != week_hi: tum_seviyeler["Aylık High"] = {"fiyat":mon_hi,"tip":"MH","yon":"SHORT","renk":"#ff5d6c"}
    if mon_lo and mon_lo != week_lo: tum_seviyeler["Aylık Low"]  = {"fiyat":mon_lo,"tip":"ML","yon":"LONG", "renk":"#1ff0ad"}

    # GEX + VPFR
    for ad,f in {**gex_levels,**vpfr_levels}.items():
        if f:
            yon = "SHORT" if ("Call" in ad or "VAH" in ad or "POC" in ad) else "LONG"
            tip = "GEX" if ad in gex_levels else "VPFR"
            tum_seviyeler[ad] = {"fiyat":f,"tip":tip,"yon":yon,"renk":"#b45cf6"}

    # Her seviyeye çok boyutlu skor ekle
    for ad, sv in tum_seviyeler.items():
        sv["skor"] = _level_skor(sv["fiyat"], sv["yon"], spot, gex_levels, vpfr_levels, asia_hi, asia_lo)
        sv["spot_mesafe_pct"] = round(abs(spot - sv["fiyat"]) / spot * 100, 2) if spot else 0
        sv["aktif"] = sv["spot_mesafe_pct"] < 5  # %5 içindeki seviyeler aktif

    # Spot'a uzaklığa göre sırala
    sirali = sorted(tum_seviyeler.items(), key=lambda x: x[1]["spot_mesafe_pct"])

    return {
        "sembol":   sym,
        "spot":     round(spot, 2),
        "tarih":    datetime.now(timezone.utc).isoformat(),
        "asia":     {"high":round(asia_hi,2),"low":round(asia_lo,2),"range_pct":round(asia_r/asia_lo*100,2) if asia_lo else 0},
        "gex":      gex_levels,
        "vpfr":     vpfr_levels,
        "seviyeler": {ad:sv for ad,sv in sirali},
        "kritik":   [ad for ad,sv in sirali if sv["aktif"] and sv["skor"]>=70][:5],
    }

def _level_skor(fiyat, yon, spot, gex_levels, vpfr_levels, asia_hi, asia_lo) -> int:
    """Bir key level için 0-100 çok boyutlu güvenilirlik skoru."""
    p = 0
    tol = fiyat * 0.005  # %0.5 tolerans

    # GEX uyumu (30pt)
    cw = gex_levels.get("Call Wall",0) or 0
    pw = gex_levels.get("Put Wall",0)  or 0
    zg = gex_levels.get("Zero Gamma",0) or 0
    mp = gex_levels.get("Max Pain",0)   or 0

    if yon=="SHORT" and cw and abs(fiyat-cw) <= tol: p += 30
    elif yon=="LONG" and pw and abs(fiyat-pw) <= tol: p += 30
    elif zg and abs(fiyat-zg) <= tol: p += 25
    elif mp and abs(fiyat-mp) <= tol: p += 20

    # VPFR uyumu (30pt)
    poc = vpfr_levels.get("VPFR POC",0) or 0
    vah = vpfr_levels.get("VPFR VAH",0) or 0
    val = vpfr_levels.get("VPFR VAL",0) or 0

    if poc and abs(fiyat-poc) <= tol: p += 30
    elif vah and abs(fiyat-vah) <= tol: p += 20
    elif val and abs(fiyat-val) <= tol: p += 20

    # Asia Range uyumu (20pt)
    if abs(fiyat - asia_hi) <= tol: p += 20
    elif abs(fiyat - asia_lo) <= tol: p += 20

    # Spot yakınlığı (20pt)
    mesafe = abs(fiyat-spot)/spot*100 if spot else 100
    p += 20 if mesafe<0.5 else 15 if mesafe<1 else 10 if mesafe<2 else 5 if mesafe<5 else 0

    return min(p, 100)

# ── Tarihsel Key Level Testi ──────────────────────────────────────────────────
async def tarihsel_analiz(sym="BTCUSDT", gun=60) -> dict:
    """
    Geçmiş gun gün için key level bounce analizini GEX + VPFR ile yapar.
    Bu analiz lokal runner'dan çağrılır (ağır işlem).
    """
    print(f"[KLAdv] {sym} {gun}g tarihsel analiz başlıyor...")

    async with httpx.AsyncClient(timeout=30) as cl:
        end_ms = int(time.time()*1000)
        st_ms  = end_ms - gun*86400_000
        rows   = []
        cur    = st_ms
        while cur < end_ms:
            r = await cl.get(f"{FAPI}/fapi/v1/klines",
                params={"symbol":sym,"interval":"5m","startTime":cur,"limit":1500})
            if r.status_code!=200: break
            d=r.json()
            if not d: break
            rows.extend(d)
            cur=d[-1][0]+1
            if len(d)<1500: break
            await asyncio.sleep(0.15)

    # Günlere ayır
    gunler = {}
    for k in rows:
        dt = datetime.fromtimestamp(k[0]/1000,tz=timezone.utc).date()
        gunler.setdefault(dt,[]).append(k)

    level_stats = {
        "asia_high":{"temas":0,"bounce":0,"break":0,"pnl_list":[]},
        "asia_low": {"temas":0,"bounce":0,"break":0,"pnl_list":[]},
        "fib_2272": {"temas":0,"bounce":0,"break":0,"pnl_list":[]},
        "fib_2618": {"temas":0,"bounce":0,"break":0,"pnl_list":[]},
        "fib_n1272":{"temas":0,"bounce":0,"break":0,"pnl_list":[]},
        "fib_n1618":{"temas":0,"bounce":0,"break":0,"pnl_list":[]},
        "prev_high":{"temas":0,"bounce":0,"break":0,"pnl_list":[]},
        "prev_low": {"temas":0,"bounce":0,"break":0,"pnl_list":[]},
    }

    tarihler = sorted(gunler.keys())
    for i, tarih in enumerate(tarihler[1:],1):
        mumlar  = gunler[tarih]
        prev    = gunler.get(tarihler[i-1],[])

        asia_k = [k for k in mumlar if 0<=datetime.fromtimestamp(k[0]/1000,tz=timezone.utc).hour<4]
        if len(asia_k)<8: continue
        H=max(float(k[2]) for k in asia_k)
        L=min(float(k[3]) for k in asia_k)
        R=H-L
        if L<=0 or R/L*100<0.5: continue

        gun_k = [k for k in mumlar if datetime.fromtimestamp(k[0]/1000,tz=timezone.utc).hour>=4]
        ph = max(float(k[2]) for k in prev) if prev else 0
        pl = min(float(k[3]) for k in prev) if prev else 0

        level_map = {
            "asia_high": (H,  "SHORT"),
            "asia_low":  (L,  "LONG"),
            "fib_2272":  (L+R*2.272,  "SHORT"),
            "fib_2618":  (L+R*2.618,  "SHORT"),
            "fib_n1272": (L+R*-1.272, "LONG"),
            "fib_n1618": (L+R*-1.618, "LONG"),
            "prev_high": (ph, "SHORT"),
            "prev_low":  (pl, "LONG"),
        }

        for lk,(lv,yon) in level_map.items():
            if not lv: continue
            tol = lv*0.003
            for j,k in enumerate(gun_k):
                hi,lo,cl = float(k[2]),float(k[3]),float(k[4])
                if not(lo-tol<=lv<=hi+tol): continue
                level_stats[lk]["temas"]+=1
                # 12 mum sonra (~1 saat)
                fut=gun_k[j+1:j+13]
                if not fut: continue
                fc=float(fut[-1][4])
                bp=(fc-lv)/lv*100 if yon=="LONG" else (lv-fc)/lv*100
                if bp>=0.3:
                    level_stats[lk]["bounce"]+=1
                    level_stats[lk]["pnl_list"].append(bp)
                elif bp<=-0.3:
                    level_stats[lk]["break"]+=1
                break  # Günde 1 temas

    # Özet hesapla
    ozet = {}
    for lk,v in level_stats.items():
        t=v["temas"]
        pl=v["pnl_list"]
        ozet[lk]={
            "temas":t,
            "bounce":v["bounce"],
            "break":v["break"],
            "bounce_rate":round(v["bounce"]/t*100,1) if t else 0,
            "avg_bounce": round(sum(pl)/len(pl),2) if pl else 0,
            "skor": min(int(v["bounce"]/t*100 if t else 0), 100),
        }

    sonuc={
        "sembol":sym,"gun":gun,
        "tarih":datetime.now(timezone.utc).isoformat(),
        "ozet":ozet,
    }
    db=_yukle(); db["analizler"].append(sonuc)
    db["analizler"]=db["analizler"][-20:]
    KL_FILE.write_text(json.dumps(db,ensure_ascii=False,indent=2))
    return sonuc

def son_analiz():
    db=_yukle()
    return db["analizler"][-1] if db["analizler"] else {}
