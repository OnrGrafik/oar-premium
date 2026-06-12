"""
Tarihsel Backtest Motoru — OAR Premium
═══════════════════════════════════════════════════════════════════
Sinyal botlarının kurallarını GEÇMİŞ verilere uygular,
sonuçları Backtest Agent analiz eder.

Stratejiler:
  ASIA_EKSTREM : Asia Range (TR 03-07) fib ekstrem temas → 4h değerlendirme
  CVD_OI_KOMBO : CVD eğim + OI değişim (OI: max 30 gün geçmiş)
  MA_TEMAS     : Günlük MA50'ye ±%0.5 temas + yön
Değerlendirme: sinyalden 4 saat sonra ±%0.5 eşik → WIN/LOSS/FLAT
"""
import os, json, asyncio, httpx
from pathlib import Path
from datetime import datetime, timezone, timedelta

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
HBT_FILE = DATA_DIR / "tarihsel_backtest.json"

FAPI = "https://fapi.binance.com"

async def _klines(cl, sym, interval, start_ms=None, end_ms=None, limit=1000):
    p = {"symbol": sym, "interval": interval, "limit": limit}
    if start_ms: p["startTime"] = start_ms
    if end_ms:   p["endTime"]   = end_ms
    r = await cl.get(f"{FAPI}/fapi/v1/klines", params=p)
    d = r.json()
    return d if isinstance(d, list) else []

async def _klines_tum(cl, sym, interval, gun):
    """Sayfalama ile `gun` günlük tüm mumları çek."""
    bitir  = int(datetime.now(timezone.utc).timestamp() * 1000)
    basla  = bitir - gun * 86400_000
    tum = []
    cursor = basla
    for _ in range(60):  # güvenlik limiti
        batch = await _klines(cl, sym, interval, start_ms=cursor, limit=1000)
        if not batch: break
        tum.extend(batch)
        son_ts = batch[-1][0]
        if son_ts >= bitir - 60_000 or len(batch) < 1000: break
        cursor = son_ts + 1
        await asyncio.sleep(0.15)
    return tum

def _degerlendir(klines, idx, yon, saat=4, esik=0.5):
    """idx mumundan `saat` sonra fiyatla WIN/LOSS/FLAT."""
    giris = float(klines[idx][4])
    # interval'e göre kaç mum sonrası
    iv_ms = klines[1][0] - klines[0][0]
    ileri = int(saat * 3600_000 / iv_ms)
    j = min(idx + ileri, len(klines) - 1)
    if j <= idx: return None, 0
    cikis = float(klines[j][4])
    pct = (cikis - giris) / giris * 100
    if yon == "SHORT": pct = -pct
    if pct >= esik:  return "WIN", round(pct, 2)
    if pct <= -esik: return "LOSS", round(pct, 2)
    return "FLAT", round(pct, 2)

# ── STRATEJİ 1: ASIA EKSTREM ───────────────────────────────────────
async def bt_asia_ekstrem(sym="BTCUSDT", gun=90):
    async with httpx.AsyncClient(timeout=30) as cl:
        k15 = await _klines_tum(cl, sym, "15m", gun)
    if len(k15) < 200: return {"hata": "veri yetersiz"}
    sinyaller = []
    # Günlere ayır (UTC 00-04 = TR 03-07)
    gunler = {}
    for i, k in enumerate(k15):
        t = datetime.fromtimestamp(k[0]/1000, tz=timezone.utc)
        gunler.setdefault(t.date(), []).append((i, k, t))
    for tarih, mumlar in gunler.items():
        asia = [(i,k) for i,k,t in mumlar if 0 <= t.hour < 4]
        if len(asia) < 10: continue
        hi = max(float(k[2]) for _,k in asia)
        lo = min(float(k[3]) for _,k in asia)
        rng = hi - lo
        if rng/lo*100 < 1.0: continue  # BTC %1 range şartı
        fibs = {"LONG": [lo + rng*o for o in (-1.272, -1.618)],
                "SHORT": [lo + rng*o for o in (2.272, 2.618)]}
        verilen = set()
        for i,k,t in mumlar:
            if not (4 <= t.hour < 20): continue  # TR 07-23
            h, l = float(k[2]), float(k[3])
            for yon, sevler in fibs.items():
                for sev in sevler:
                    key = (yon, round(sev))
                    if key in verilen: continue
                    if l <= sev <= h:
                        out, pct = _degerlendir(k15, i, yon)
                        if out:
                            sinyaller.append({"tarih": t.isoformat(), "yon": yon,
                                              "seviye": round(sev,2), "outcome": out, "pct": pct})
                            verilen.add(key)
    return _istatistik("ASIA_EKSTREM", sym, gun, sinyaller)

# ── STRATEJİ 2: CVD + OI KOMBO (max 30 gün) ───────────────────────
async def bt_cvd_oi(sym="BTCUSDT", gun=30):
    gun = min(gun, 30)
    async with httpx.AsyncClient(timeout=30) as cl:
        k5 = await _klines_tum(cl, sym, "5m", gun)
        # OI 30dk geçmişi (500 kayıt/istek, sayfalama)
        oi_hist = []
        end = int(datetime.now(timezone.utc).timestamp()*1000)
        start = end - gun*86400_000
        cursor = start
        for _ in range(40):
            r = await cl.get(f"{FAPI}/futures/data/openInterestHist",
                params={"symbol": sym, "period": "30m", "startTime": cursor, "limit": 500})
            d = r.json()
            if not isinstance(d, list) or not d: break
            oi_hist.extend(d)
            cursor = d[-1]["timestamp"] + 1
            if len(d) < 500 or cursor >= end: break
            await asyncio.sleep(0.15)
    if len(k5) < 100 or len(oi_hist) < 20: return {"hata": "veri yetersiz"}
    oi_map = {d["timestamp"]: float(d["sumOpenInterestValue"]) for d in oi_hist}
    oi_ts = sorted(oi_map.keys())
    sinyaller = []
    cvd = 0; cvd_seri = []
    for k in k5:
        vol = float(k[5]); tbv = float(k[9])
        cvd += tbv - (vol - tbv)
        cvd_seri.append(cvd)
    son_sinyal_ts = 0
    for i in range(12, len(k5)-48):
        ts = k5[i][0]
        if ts - son_sinyal_ts < 3600_000: continue  # 1 saat dedup
        cvd_egim = cvd_seri[i] - cvd_seri[i-6]
        # En yakın OI çifti
        onceki = [t for t in oi_ts if t <= ts - 1800_000]
        simdi  = [t for t in oi_ts if t <= ts]
        if not onceki or not simdi: continue
        oi_o, oi_s = oi_map[onceki[-1]], oi_map[simdi[-1]]
        if not oi_o: continue
        oi_pct = (oi_s - oi_o) / oi_o * 100
        yon = None
        if cvd_egim > 0 and oi_pct > 0.5:   yon = "LONG"
        elif cvd_egim < 0 and oi_pct > 0.5: yon = "SHORT"
        if yon:
            out, pct = _degerlendir(k5, i, yon)
            if out:
                t = datetime.fromtimestamp(ts/1000, tz=timezone.utc)
                sinyaller.append({"tarih": t.isoformat(), "yon": yon,
                                  "oi_pct": round(oi_pct,2), "outcome": out, "pct": pct})
                son_sinyal_ts = ts
    return _istatistik("CVD_OI_KOMBO", sym, gun, sinyaller)

# ── STRATEJİ 3: MA TEMAS ───────────────────────────────────────────
async def bt_ma_temas(sym="BTCUSDT", gun=365):
    async with httpx.AsyncClient(timeout=30) as cl:
        k1d = await _klines_tum(cl, sym, "1d", gun + 60)
    if len(k1d) < 80: return {"hata": "veri yetersiz"}
    closes = [float(k[4]) for k in k1d]
    sinyaller = []
    for i in range(50, len(k1d)-2):
        ma50 = sum(closes[i-50:i]) / 50
        h, l, c = float(k1d[i][2]), float(k1d[i][3]), closes[i]
        if l <= ma50*1.005 and h >= ma50*0.995:  # ±%0.5 temas
            yon = "LONG" if c > ma50 else "SHORT"
            out, pct = _degerlendir(k1d, i, yon, saat=24, esik=1.0)
            if out:
                t = datetime.fromtimestamp(k1d[i][0]/1000, tz=timezone.utc)
                sinyaller.append({"tarih": t.isoformat(), "yon": yon,
                                  "ma50": round(ma50,2), "outcome": out, "pct": pct})
    return _istatistik("MA_TEMAS", sym, gun, sinyaller)

# ── İSTATİSTİK (Backtest Agent analizi) ────────────────────────────
def _istatistik(strateji, sym, gun, sinyaller):
    w = sum(1 for s in sinyaller if s["outcome"]=="WIN")
    l = sum(1 for s in sinyaller if s["outcome"]=="LOSS")
    f = sum(1 for s in sinyaller if s["outcome"]=="FLAT")
    top = w + l
    pnls = [s["pct"] for s in sinyaller if s["outcome"] in ("WIN","LOSS")]
    # Saat dağılımı
    saat_w, saat_t = {}, {}
    for s in sinyaller:
        if s["outcome"] not in ("WIN","LOSS"): continue
        h = datetime.fromisoformat(s["tarih"]).hour
        saat_t[h] = saat_t.get(h,0)+1
        if s["outcome"]=="WIN": saat_w[h] = saat_w.get(h,0)+1
    en_iyi_saat = max(saat_t, key=lambda h: (saat_w.get(h,0)/saat_t[h], saat_t[h])) if saat_t else None
    # LONG vs SHORT
    lw = sum(1 for s in sinyaller if s["yon"]=="LONG" and s["outcome"]=="WIN")
    lt = sum(1 for s in sinyaller if s["yon"]=="LONG" and s["outcome"] in ("WIN","LOSS"))
    sw = sum(1 for s in sinyaller if s["yon"]=="SHORT" and s["outcome"]=="WIN")
    st = sum(1 for s in sinyaller if s["yon"]=="SHORT" and s["outcome"] in ("WIN","LOSS"))
    sonuc = {
        "strateji": strateji, "sembol": sym, "gun": gun,
        "tarih": datetime.now(timezone.utc).isoformat(),
        "toplam_sinyal": len(sinyaller),
        "win": w, "loss": l, "flat": f,
        "win_rate": round(w/top*100,1) if top else 0,
        "ort_pnl_pct": round(sum(pnls)/len(pnls),2) if pnls else 0,
        "toplam_pnl_pct": round(sum(pnls),2) if pnls else 0,
        "long_wr": round(lw/lt*100,1) if lt else 0, "long_n": lt,
        "short_wr": round(sw/st*100,1) if st else 0, "short_n": st,
        "en_iyi_saat_utc": en_iyi_saat,
        "son_10": sinyaller[-10:],
    }
    # Kaydet — geçmiş silinmez
    db = json.loads(HBT_FILE.read_text()) if HBT_FILE.exists() else {"testler": []}
    db["testler"].append(sonuc)
    HBT_FILE.write_text(json.dumps(db, ensure_ascii=False, indent=2))
    return sonuc

STRATEJILER = {"ASIA_EKSTREM": bt_asia_ekstrem, "CVD_OI_KOMBO": bt_cvd_oi, "MA_TEMAS": bt_ma_temas}

async def calistir(strateji: str, sym: str = "BTCUSDT", gun: int = 90):
    fn = STRATEJILER.get(strateji)
    if not fn: return {"hata": f"Bilinmeyen strateji. Mevcut: {list(STRATEJILER)}"}
    return await fn(sym, gun)

def gecmis_testler(limit=20):
    db = json.loads(HBT_FILE.read_text()) if HBT_FILE.exists() else {"testler": []}
    return db["testler"][-limit:]
