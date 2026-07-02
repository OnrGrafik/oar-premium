"""
Feature Engine — OAR Premium Akıllı Pattern Öğrenici
═══════════════════════════════════════════════════════════
1. ZENGİNLEŞTİR: Her sinyale anlık indikatör seti ekle
   (RSI, MACD, MA20/50 konumu, CVD eğimi, OI değişimi, hacim, funding)
2. ÖĞREN: WIN vs LOSS sinyallerin feature ortalamalarını karşılaştır
   → ayırt edici kazanan profil çıkar
3. ÜRET: Kazanan profil canlı veride eşleşirse "OAR Pattern" sinyali
4. AI: Öğrenilen profili Gemini yorumlar
"""
import os, json, asyncio, httpx
from pathlib import Path
from datetime import datetime, timezone

DATA_DIR = Path(os.environ.get("DATA_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or ("/var/data" if Path("/var/data").exists() else "data"))
SIGLOG   = DATA_DIR / "oar_signals_log.json"
PROFIL   = DATA_DIR / "kazanan_profil.json"
FAPI     = "https://fapi.binance.com"

def _load(p, d):
    try:
        return json.loads(Path(p).read_text()) if Path(p).exists() else d
    except Exception: return d
def _save(p, d): Path(p).write_text(json.dumps(d, ensure_ascii=False, indent=2))
def _now(): return datetime.now(timezone.utc).isoformat()

# ── İNDİKATÖR HESAPLARI ────────────────────────────────────────────
def _rsi(closes, n=14):
    if len(closes) < n+1: return None
    g = l = 0
    for i in range(-n, 0):
        d = closes[i] - closes[i-1]
        if d > 0: g += d
        else: l -= d
    if l == 0: return 100.0
    return round(100 - 100/(1 + g/l), 1)

def _ema(vals, n):
    k = 2/(n+1); e = sum(vals[:n])/n
    for v in vals[n:]: e = v*k + e*(1-k)
    return e

def _macd_hist(closes):
    if len(closes) < 35: return None
    macd_line = _ema(closes, 12) - _ema(closes, 26)
    # sinyal çizgisi yaklaşık: son 9 macd değeri yerine kompakt tahmin
    macds = []
    for i in range(-9, 0):
        seg = closes[:len(closes)+i+1]
        if len(seg) >= 26:
            macds.append(_ema(seg,12) - _ema(seg,26))
    if len(macds) < 9: return None
    return round(macd_line - sum(macds)/len(macds), 4)

async def feature_cek(sym: str) -> dict:
    """Sembolün anlık indikatör fotoğrafı."""
    f = {}
    try:
        async with httpx.AsyncClient(timeout=15) as cl:
            r = await cl.get(f"{FAPI}/fapi/v1/klines",
                             params={"symbol": sym, "interval": "15m", "limit": 100})
            k = r.json()
            if not isinstance(k, list) or len(k) < 60: return f
            closes = [float(x[4]) for x in k]
            c = closes[-1]
            ma20 = sum(closes[-20:])/20; ma50 = sum(closes[-50:])/50
            f["rsi"]        = _rsi(closes)
            f["macd_hist"]  = _macd_hist(closes)
            f["ma20_dist"]  = round((c-ma20)/ma20*100, 2)   # % mesafe
            f["ma50_dist"]  = round((c-ma50)/ma50*100, 2)
            f["vol_oran"]   = round(float(k[-1][5]) / (sum(float(x[5]) for x in k[-20:])/20), 2)
            cvd = 0; seri = []
            for x in k[-12:]:
                v = float(x[5]); tb = float(x[9]); cvd += tb-(v-tb); seri.append(cvd)
            f["cvd_egim"]   = 1 if seri[-1] > seri[0] else -1
            r2 = await cl.get(f"{FAPI}/futures/data/openInterestHist",
                              params={"symbol": sym, "period": "30m", "limit": 2})
            oi = r2.json()
            if isinstance(oi, list) and len(oi) == 2:
                o0, o1 = float(oi[0]["sumOpenInterestValue"]), float(oi[1]["sumOpenInterestValue"])
                f["oi_pct"] = round((o1-o0)/o0*100, 2) if o0 else 0
            r3 = await cl.get(f"{FAPI}/fapi/v1/premiumIndex", params={"symbol": sym})
            f["funding"] = round(float(r3.json().get("lastFundingRate", 0))*100, 4)
    except Exception:
        pass
    return f

# ── 1. ZENGİNLEŞTİRİCİ LOOP ────────────────────────────────────────
async def zenginlestirici_loop():
    """features alanı olmayan sinyallere indikatör fotoğrafı ekler."""
    await asyncio.sleep(90)
    while True:
        try:
            log = _load(SIGLOG, {"signals": []})
            sigs = log.get("signals", []) if isinstance(log, dict) else log
            n = 0
            for s in sigs[-50:]:
                if s.get("features") or not s.get("symbol"): continue
                sym = s["symbol"].upper().replace(".P","")
                if not sym.endswith("USDT"): sym += "USDT"
                feats = await feature_cek(sym)
                if feats:
                    s["features"] = feats; n += 1
                await asyncio.sleep(0.5)
                if n >= 10: break
            if n:
                _save(SIGLOG, {"signals": sigs})
                print(f"[FeatureEngine] {n} sinyal zenginleştirildi")
        except Exception as e:
            print(f"[FeatureEngine] {str(e)[:60]}")
        await asyncio.sleep(600)

# ── 2. ÖĞRENİCİ ────────────────────────────────────────────────────
def profil_ogren() -> dict:
    """WIN vs LOSS feature ortalamaları → ayırt edici kazanan profil."""
    log = _load(SIGLOG, {"signals": []})
    sigs = log.get("signals", []) if isinstance(log, dict) else log
    w = [s["features"] for s in sigs if s.get("outcome")=="WIN" and s.get("features")]
    l = [s["features"] for s in sigs if s.get("outcome")=="LOSS" and s.get("features")]
    if len(w) < 5 or len(l) < 5:
        return {"durum": "yetersiz", "win_n": len(w), "loss_n": len(l),
                "mesaj": "Öğrenme için en az 5 WIN + 5 LOSS feature'lı sinyal gerekli."}
    def ort(grup, anahtar):
        v = [g[anahtar] for g in grup if g.get(anahtar) is not None]
        return round(sum(v)/len(v), 3) if v else None
    anahtarlar = ["rsi","macd_hist","ma20_dist","ma50_dist","vol_oran","cvd_egim","oi_pct","funding"]
    ayirt = {}
    for a in anahtarlar:
        wo, lo = ort(w, a), ort(l, a)
        if wo is not None and lo is not None and abs(wo-lo) > 0.01:
            ayirt[a] = {"win_ort": wo, "loss_ort": lo, "fark": round(wo-lo, 3)}
    profil = {"tarih": _now(), "win_n": len(w), "loss_n": len(l), "ayirt_edici": ayirt}
    _save(PROFIL, profil)
    return profil

# ── 3. ÜRETİCİ LOOP ────────────────────────────────────────────────
async def pattern_sinyal_loop():
    """Kazanan profil canlıda eşleşirse OAR Pattern sinyali üret."""
    await asyncio.sleep(300)
    while True:
        try:
            profil = profil_ogren()
            if profil.get("ayirt_edici"):
                ayirt = profil["ayirt_edici"]
                for sym in ["BTCUSDT","ETHUSDT","SOLUSDT"]:
                    f = await feature_cek(sym)
                    if not f: continue
                    # Eşleşme skoru: her ayırt edici feature WIN ortalamasına
                    # LOSS'tan daha yakınsa +1
                    skor = uy = 0
                    for a, d in ayirt.items():
                        if f.get(a) is None: continue
                        uy += 1
                        if abs(f[a]-d["win_ort"]) < abs(f[a]-d["loss_ort"]): skor += 1
                    if uy >= 4 and skor/uy >= 0.75:  # %75+ kazanan profile uyum
                        yon = "LONG" if f.get("cvd_egim",0) > 0 else "SHORT"
                        log = _load(SIGLOG, {"signals": []})
                        sigs = log.get("signals", [])
                        # 2 saat dedup
                        son = [s for s in sigs[-30:] if s.get("bot")=="OAR Pattern" and s.get("symbol")==sym]
                        if son:
                            try:
                                t = datetime.fromisoformat(son[-1]["time"])
                                if (datetime.now(timezone.utc)-t).total_seconds() < 7200:
                                    continue
                            except Exception: pass
                        sigs.append({"bot": "OAR Pattern", "symbol": sym, "direction": yon,
                                     "price": 0, "features": f,
                                     "detail": f"Kazanan profile %{skor/uy*100:.0f} uyum ({skor}/{uy} feature)",
                                     "time": _now(), "outcome": None})
                        _save(SIGLOG, {"signals": sigs[-1000:]})
                        print(f"[Pattern] ⚡ {sym} {yon} — profil uyumu %{skor/uy*100:.0f}")
                    await asyncio.sleep(0.5)
        except Exception as e:
            print(f"[Pattern] {str(e)[:60]}")
        await asyncio.sleep(1800)  # 30 dk

def kazanan_profil_al():
    return _load(PROFIL, {"durum": "henuz_yok"})
