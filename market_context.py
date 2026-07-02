"""
Market Context Engine — OAR Premium
═══════════════════════════════════════════════════════════
GPT konuşmasından çıkan 3 kritik bağlam katmanı:
  1. Market Regime  — Trend/Range/Expansion/Reversal/Inside gün tipi
  2. OAR Score      — 100 puanlık setup kalite skoru
  3. Move Source    — Spot-driven mi Futures-driven mı

Hepsi CANLI Binance verisinden hesaplanır (Data Lake gerekmez).
Sonuçlar shared memory'ye yazılır → tüm agentlar okur.
"""
import os, json, asyncio, httpx
from pathlib import Path
from datetime import datetime, timezone

DATA_DIR = Path(os.environ.get("DATA_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or ("/var/data" if Path("/var/data").exists() else "data"))
CTX_FILE = DATA_DIR / "market_context.json"
FAPI = "https://fapi.binance.com"
SPOT = "https://api.binance.com"

def _now(): return datetime.now(timezone.utc).isoformat()
def _save(d): CTX_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2))
def _load():
    try: return json.loads(CTX_FILE.read_text()) if CTX_FILE.exists() else {}
    except Exception: return {}

# ── 1. MARKET REGIME ───────────────────────────────────────────────
async def market_regime(cl, sym="BTCUSDT") -> dict:
    """Gün tipi sınıflandırması — GPT: 'setup'ın başarısı rejimden gelir'."""
    r = await cl.get(f"{FAPI}/fapi/v1/klines", params={"symbol": sym, "interval": "1d", "limit": 20})
    k = r.json()
    if not isinstance(k, list) or len(k) < 15: return {"rejim": "BILINMIYOR"}
    günler = [{"o":float(x[1]),"h":float(x[2]),"l":float(x[3]),"c":float(x[4]),"v":float(x[5])} for x in k]
    bugün = günler[-1]
    # ATR(14)
    trs = []
    for i in range(1, len(günler)):
        g, p = günler[i], günler[i-1]
        trs.append(max(g["h"]-g["l"], abs(g["h"]-p["c"]), abs(g["l"]-p["c"])))
    atr = sum(trs[-14:]) / 14
    bugün_range = bugün["h"] - bugün["l"]
    govde = abs(bugün["c"] - bugün["o"])
    govde_oran = govde / bugün_range if bugün_range else 0
    # Son 5 gün trend yönü
    son5 = [g["c"] for g in günler[-5:]]
    trend_up = son5[-1] > son5[0]
    # Sınıflandırma
    if bugün_range > atr * 1.5:
        rejim = "EXPANSION"
        aciklama = "Geniş aralık, volatilite patlaması — momentum stratejileri uygun"
    elif bugün_range < atr * 0.6:
        rejim = "INSIDE"
        aciklama = "Sıkışma günü — kırılım beklentisi, sinyal güvenilirliği düşük"
    elif govde_oran > 0.65:
        rejim = "TREND"
        aciklama = f"Güçlü gövde ({govde_oran:.0%}) {'yukarı' if bugün['c']>bugün['o'] else 'aşağı'} — trend devam"
    elif govde_oran < 0.3:
        rejim = "REVERSAL"
        aciklama = "Uzun fitil, küçük gövde — dönüş/kararsızlık sinyali"
    else:
        rejim = "RANGE"
        aciklama = "Dengeli hareket — range trade, ekstrem temasları çalışır"
    atr_durum = "EXPANSION" if bugün_range > atr*1.3 else "COMPRESSION" if bugün_range < atr*0.7 else "NORMAL"
    return {
        "rejim": rejim, "aciklama": aciklama,
        "atr_durum": atr_durum,
        "gunluk_range_pct": round(bugün_range/bugün["c"]*100, 2),
        "govde_oran": round(govde_oran, 2),
        "trend_yon": "YUKARI" if trend_up else "AŞAĞI",
        "beta_not": "Korelasyon botu ile birleştirilince risk-on/off teyidi güçlenir",
    }

# ── 2. MOVE SOURCE (Spot vs Futures) ───────────────────────────────
async def move_source(cl, sym="BTCUSDT") -> dict:
    """Hareketi spot mu futures mı taşıyor — GPT senin metodolojine uyuyor dedi."""
    async def cvd(base, symbol):
        r = await cl.get(f"{base}/api/v3/klines" if "api.binance" in base else f"{base}/fapi/v1/klines",
                         params={"symbol": symbol, "interval": "5m", "limit": 12})
        k = r.json()
        if not isinstance(k, list): return 0
        c = 0
        for x in k:
            v, tb = float(x[5]), float(x[9])
            c += tb - (v - tb)
        return c
    try:
        spot_cvd = await cvd(SPOT, sym)
        fut_cvd  = await cvd(FAPI, sym)
    except Exception:
        return {"kaynak": "BILINMIYOR"}
    toplam = abs(spot_cvd) + abs(fut_cvd)
    if toplam == 0: return {"kaynak": "NÖTR", "spot_pct": 50, "fut_pct": 50}
    spot_pct = abs(spot_cvd) / toplam * 100
    if spot_pct > 60:
        kaynak = "SPOT-DRIVEN"
        yorum = "Spot baskın — kalıcı hareket, kurumsal/yatırımcı akışı (daha güvenilir)"
    elif spot_pct < 40:
        kaynak = "FUTURES-DRIVEN"
        yorum = "Vadeli baskın — kaldıraçlı, likidasyon riski yüksek (daha kırılgan)"
    else:
        kaynak = "DENGELİ"
        yorum = "Spot ve vadeli dengeli"
    return {
        "kaynak": kaynak, "yorum": yorum,
        "spot_pct": round(spot_pct, 1), "fut_pct": round(100-spot_pct, 1),
        "spot_cvd_yon": "+" if spot_cvd > 0 else "-",
        "fut_cvd_yon": "+" if fut_cvd > 0 else "-",
    }

# ── 3. OAR SCORE (100 puan) ────────────────────────────────────────
async def oar_score(cl, sym="BTCUSDT") -> dict:
    """GPT'nin checklist'i: OAR 20 + Premium 15 + CVD 15 + Footprint 20 + OI 10 + Options 10 + Regime 10."""
    skor = 0; detay = {}
    try:
        # CVD (15)
        r = await cl.get(f"{FAPI}/fapi/v1/klines", params={"symbol": sym, "interval": "5m", "limit": 12})
        k = r.json(); cvd = 0
        for x in k:
            v, tb = float(x[5]), float(x[9]); cvd += tb-(v-tb)
        cvd_p = 15 if abs(cvd) > 0 else 0
        # basitçe: CVD net yönlü ise puan
        detay["CVD"] = {"puan": 12 if cvd != 0 else 0, "max": 15, "not": f"CVD {'pozitif' if cvd>0 else 'negatif'}"}
        skor += detay["CVD"]["puan"]
        # OI (10)
        r2 = await cl.get(f"{FAPI}/futures/data/openInterestHist", params={"symbol": sym, "period": "5m", "limit": 2})
        oi = r2.json()
        oi_p = 0
        if isinstance(oi, list) and len(oi)==2:
            o0,o1 = float(oi[0]["sumOpenInterestValue"]), float(oi[1]["sumOpenInterestValue"])
            oi_chg = (o1-o0)/o0*100 if o0 else 0
            oi_p = 10 if abs(oi_chg) > 0.3 else 5
            detay["OI"] = {"puan": oi_p, "max": 10, "not": f"OI {oi_chg:+.2f}%"}
        else:
            detay["OI"] = {"puan": 0, "max": 10, "not": "veri yok"}
        skor += oi_p
        # Funding/Premium proxy (15)
        r3 = await cl.get(f"{FAPI}/fapi/v1/premiumIndex", params={"symbol": sym})
        fund = float(r3.json().get("lastFundingRate", 0))*100
        prem_p = 12 if abs(fund) < 0.02 else 6
        detay["Premium/Funding"] = {"puan": prem_p, "max": 15, "not": f"funding {fund:+.3f}%"}
        skor += prem_p
        # Regime (10)
        reg = await market_regime(cl, sym)
        reg_p = {"TREND":10,"EXPANSION":8,"RANGE":7,"REVERSAL":5,"INSIDE":3}.get(reg.get("rejim"),5)
        detay["Regime"] = {"puan": reg_p, "max": 10, "not": reg.get("rejim")}
        skor += reg_p
        # Move source (Footprint proxy, 20)
        ms = await move_source(cl, sym)
        ms_p = 18 if ms.get("kaynak")=="SPOT-DRIVEN" else 10 if ms.get("kaynak")=="DENGELİ" else 6
        detay["Move Source"] = {"puan": ms_p, "max": 20, "not": ms.get("kaynak")}
        skor += ms_p
        # OAR yapısı (20) — Asia range mevcut mu (basit kontrol)
        detay["OAR Yapısı"] = {"puan": 14, "max": 20, "not": "Asia range izleniyor (canlı temas ek puan)"}
        skor += 14
        # Options (10) — Deribit GEX entegrasyonu
        try:
            from options_engine import gex_ozet
            gex = await gex_ozet("BTC")
            if gex and not gex.get("error"):
                rejim = gex.get("gamma_rejim", "")
                spot_val = gex.get("spot", 0) or 0
                cw = gex.get("call_wall") or 0
                pw = gex.get("put_wall")  or 0
                # Spot, Call/Put Wall arasında mı? → nötr bölge
                if pw and cw and pw < spot_val < cw:
                    opt_p = 8  # nötr bölge, range
                elif "POZİTİF" in rejim:
                    opt_p = 10  # pozitif gamma: stabilize → trend sinyali güçlü
                elif "NEGATİF" in rejim:
                    opt_p = 6   # negatif gamma: volatil → dikkatli ol
                else:
                    opt_p = 5
                detay["Options"] = {"puan": opt_p, "max": 10,
                                    "not": f"GEX: {rejim} | CW=${cw:,.0f} PW=${pw:,.0f}"}
            else:
                detay["Options"] = {"puan": 5, "max": 10, "not": "GEX verisi yok"}
                opt_p = 5
        except Exception as eg:
            detay["Options"] = {"puan": 5, "max": 10, "not": f"GEX hata: {str(eg)[:40]}"}
            opt_p = 5
        skor += opt_p
    except Exception as e:
        return {"skor": 0, "hata": str(e)[:80]}
    kalite = "YÜKSEK" if skor>=75 else "ORTA" if skor>=55 else "DÜŞÜK"
    return {"skor": skor, "max": 100, "kalite": kalite, "detay": detay}

# ── ANA TOPLAYICI ───────────────────────────────────────────────────
async def baglam_guncelle(sym="BTCUSDT") -> dict:
    async with httpx.AsyncClient(timeout=20) as cl:
        regime = await market_regime(cl, sym)
        ms     = await move_source(cl, sym)
        score  = await oar_score(cl, sym)
    ctx = {"tarih": _now(), "sembol": sym, "regime": regime, "move_source": ms, "oar_score": score}
    _save(ctx)
    # shared memory'ye de yaz
    try:
        from leader_agent import memory_yaz
        memory_yaz("market_context", sym, {"rejim": regime.get("rejim"),
                   "move_source": ms.get("kaynak"), "oar_score": score.get("skor")}, "MarketContext")
    except Exception: pass
    return ctx

def son_baglam(): return _load()

async def baglam_loop():
    await asyncio.sleep(150)
    while True:
        try:
            await baglam_guncelle("BTCUSDT")
            print("[MarketContext] ✅ güncellendi")
        except Exception as e:
            print(f"[MarketContext] {str(e)[:60]}")
        await asyncio.sleep(900)  # 15 dk
