"""
Başarı Skoru — OAR Premium v3
═══════════════════════════════════════════════════════════════════
Win rate YERİNE. Belge kuralı:
  "Sinyal üretmesi başarı değildir. Verdiği sinyal yönünde
   o coin ne kadar gidiyor — O başarıdır."

İki ölçüm (ayrı tablolar):
  1. YÜZDE  : sinyal sonrası coin sinyal yönünde MAX % kaç gitti
  2. DOLAR  : 10$ ana para, 100x kaldıraç → o gün en tepe/dipte
              kapatılsa MAX kaç $ olurdu

Haftalık otomatik sıfırlanır. Geçmiş haftalar arşivlenir.
Botlar yarıştırılmaz — her bot kendi tablosunda.
"""
import os, json, asyncio, httpx
from pathlib import Path
from datetime import datetime, timezone, timedelta

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
SKOR_FILE = DATA_DIR / "basari_skoru.json"
ARSIV_FILE = DATA_DIR / "basari_arsiv.json"
SIGLOG = DATA_DIR / "oar_signals_log.json"
FAPI = "https://fapi.binance.com"

LEVERAGE = 100
ANAPARA = 10.0

def _now(): return datetime.now(timezone.utc)
def _hafta_no(): 
    iso = _now().isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"
def _load(p, d):
    try: return json.loads(Path(p).read_text()) if Path(p).exists() else d
    except Exception: return d
def _save(p, d): Path(p).write_text(json.dumps(d, ensure_ascii=False, indent=2))

async def _max_hareket(cl, sym: str, yon: str, giris_ts: int, giris_fiyat: float):
    """Sinyal anından gün sonuna kadar sinyal yönünde MAX hareket %."""
    try:
        r = await cl.get(f"{FAPI}/fapi/v1/klines", params={
            "symbol": sym, "interval": "5m", "startTime": giris_ts, "limit": 288})
        k = r.json()
        if not isinstance(k, list) or not k: return None
        if yon in ("LONG", "ALIS", "ALIŞ"):
            best = max(float(x[2]) for x in k)  # en yüksek high
            return (best - giris_fiyat) / giris_fiyat * 100
        else:
            best = min(float(x[3]) for x in k)  # en düşük low
            return (giris_fiyat - best) / giris_fiyat * 100
    except Exception:
        return None

async def skorlari_guncelle():
    """Değerlendirilmemiş sinyalleri tara, max hareket + dolar kazanç hesapla."""
    hafta = _hafta_no()
    db = _load(SKOR_FILE, {"hafta": hafta, "botlar": {}})

    # Hafta değiştiyse arşivle + sıfırla
    if db.get("hafta") != hafta:
        arsiv = _load(ARSIV_FILE, {"haftalar": []})
        arsiv["haftalar"].append({"hafta": db.get("hafta"), "botlar": db.get("botlar", {}),
                                  "arsiv_tarih": _now().isoformat()})
        arsiv["haftalar"] = arsiv["haftalar"][-12:]  # son 12 hafta
        _save(ARSIV_FILE, arsiv)
        db = {"hafta": hafta, "botlar": {}}

    log = _load(SIGLOG, {"signals": []})
    sigs = log.get("signals", []) if isinstance(log, dict) else log
    simdi = _now()
    guncellenen = 0

    async with httpx.AsyncClient(timeout=15) as cl:
        for s in sigs:
            if s.get("skor_islendi"): continue
            sym = (s.get("symbol") or "").upper().replace(".P", "")
            if not sym: continue
            if not sym.endswith("USDT"): sym += "USDT"
            yon = (s.get("direction") or "").upper()
            if yon not in ("LONG", "SHORT", "ALIS", "ALIŞ", "SATIS", "SATIŞ"): continue
            try:
                t = datetime.fromisoformat(s.get("time", "").replace("Z", "+00:00"))
            except Exception:
                continue
            # En az 1 saat geçmiş sinyaller (hareket oluşsun)
            if (simdi - t).total_seconds() < 3600: continue
            giris = float(s.get("price") or 0)
            if giris <= 0:
                # fiyat yoksa o anki kapanışı al
                try:
                    rr = await cl.get(f"{FAPI}/fapi/v1/klines", params={
                        "symbol": sym, "interval": "5m",
                        "startTime": int(t.timestamp()*1000), "limit": 1})
                    giris = float(rr.json()[0][4])
                except Exception:
                    continue
            max_pct = await _max_hareket(cl, sym, yon, int(t.timestamp()*1000), giris)
            if max_pct is None: continue
            max_pct = max(max_pct, 0)  # negatifse 0 (sinyal yönünde gitmedi)
            dolar = ANAPARA * (1 + max_pct/100 * LEVERAGE)  # 10$ 100x

            bot = s.get("bot", "Bilinmeyen")
            b = db["botlar"].setdefault(bot, {"sinyal": 0, "toplam_pct": 0, "max_pct": 0,
                                              "toplam_dolar": 0, "max_dolar": 0, "ornekler": []})
            b["sinyal"] += 1
            b["toplam_pct"] += max_pct
            b["max_pct"] = max(b["max_pct"], max_pct)
            b["toplam_dolar"] += dolar
            b["max_dolar"] = max(b["max_dolar"], dolar)
            b["ornekler"].append({"sym": sym, "yon": yon, "pct": round(max_pct, 2),
                                  "dolar": round(dolar, 1), "t": s.get("time", "")[:16]})
            b["ornekler"] = b["ornekler"][-20:]
            s["skor_islendi"] = True
            s["max_pct"] = round(max_pct, 2)
            guncellenen += 1

    if guncellenen:
        _save(SIGLOG, {"signals": sigs})
    _save(SKOR_FILE, db)
    return {"guncellenen": guncellenen, "hafta": hafta}

def skor_tablosu():
    """İki tablo: yüzde + dolar. Bot bazında, yarıştırma yok."""
    db = _load(SKOR_FILE, {"hafta": _hafta_no(), "botlar": {}})
    yuzde, dolar = [], []
    for bot, b in db.get("botlar", {}).items():
        n = b["sinyal"] or 1
        yuzde.append({"bot": bot, "sinyal": b["sinyal"],
                      "ort_pct": round(b["toplam_pct"]/n, 2), "max_pct": round(b["max_pct"], 2)})
        dolar.append({"bot": bot, "sinyal": b["sinyal"],
                      "ort_dolar": round(b["toplam_dolar"]/n, 1), "max_dolar": round(b["max_dolar"], 1)})
    yuzde.sort(key=lambda x: x["max_pct"], reverse=True)
    dolar.sort(key=lambda x: x["max_dolar"], reverse=True)
    return {"hafta": db.get("hafta"), "anapara": ANAPARA, "kaldirac": LEVERAGE,
            "yuzde_tablo": yuzde, "dolar_tablo": dolar}

def bot_detay(bot_adi):
    db = _load(SKOR_FILE, {"botlar": {}})
    return db.get("botlar", {}).get(bot_adi, {})

async def skor_loop():
    await asyncio.sleep(180)
    while True:
        try:
            r = await skorlari_guncelle()
            if r["guncellenen"]:
                print(f"[BaşarıSkoru] {r['guncellenen']} sinyal işlendi ({r['hafta']})")
        except Exception as e:
            print(f"[BaşarıSkoru] {str(e)[:60]}")
        await asyncio.sleep(1800)  # 30 dk
