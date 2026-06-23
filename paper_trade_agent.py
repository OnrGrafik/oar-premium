"""
Paper Trade Agent — OAR Premium
═══════════════════════════════════════════════════════
CIO Karar Motoru LONG/SHORT ürettiğinde sanal pozisyon açar,
canlı fiyata karşı SL/TP takip eder, sonucu SQLite'a kaydeder.

Gerçek para riski yok — sistemin kararlarının gerçek performansını
ölçmenin tek dürüst yolu. Backtest geçmişe bakar; bu, ileriye doğru
(forward test) gerçek zamanlı doğrulama yapar.

SL/TP mantığı: rejimin ATR'ına göre dinamik.
  • TREND     → geniş TP (2.5R), trend takip
  • RANGE     → dar TP (1.2R), hızlı al-sat
  • HIGH_VOL  → geniş SL (stop avı koruması)
"""

import asyncio
import httpx
from datetime import datetime, timezone

import persistence as db

# Aynı yönde aynı sembolde zaten açık trade varsa yenisini açma
KONFIDANS_ESIK = 60.0      # bu konfidansın altında trade açma
POZISYON_USD   = 1000.0    # sanal pozisyon büyüklüğü


async def _fiyat(sembol: str = "BTCUSDT") -> float:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get("https://api.binance.com/api/v3/ticker/price",
                        params={"symbol": sembol})
        return float(r.json()["price"])


def _sl_tp_hesapla(yon: str, giris: float, atr_pct: float, rejim: str) -> tuple:
    """
    Rejime göre SL/TP seviyeleri üretir.
    atr_pct yoksa makul varsayılan (%1.5) kullanılır.
    """
    atr = (atr_pct or 1.5) / 100 * giris

    # Risk çarpanları (SL_mult, TP_mult)
    carpanlar = {
        "TREND_UP":   (1.2, 2.5),
        "TREND_DOWN": (1.2, 2.5),
        "RANGE":      (1.0, 1.2),
        "HIGH_VOL":   (1.8, 2.2),
        "PANIC":      (2.0, 2.0),
    }.get(rejim, (1.3, 2.0))

    sl_mult, tp_mult = carpanlar
    if yon == "LONG":
        sl = giris - atr * sl_mult
        tp = giris + atr * tp_mult
    else:  # SHORT
        sl = giris + atr * sl_mult
        tp = giris - atr * tp_mult
    return round(sl, 2), round(tp, 2)


async def karardan_trade_ac(karar: dict, karar_id: int = None) -> dict:
    """
    CIO kararına göre paper trade açar.
    Açılmazsa sebep döner (örn. NO_TRADE, düşük konfidans, zaten açık).
    """
    yon = karar.get("karar")
    sembol = karar.get("sembol", "BTCUSDT")
    konfidans = karar.get("konfidans", 0)

    if yon not in ("LONG", "SHORT"):
        return {"acildi": False, "neden": f"Karar {yon} — trade açılmaz"}

    if konfidans < KONFIDANS_ESIK:
        return {"acildi": False, "neden": f"Konfidans {konfidans} < {KONFIDANS_ESIK}"}

    # Aynı yönde açık pozisyon var mı?
    for t in db.acik_tradeler(sembol):
        if t["yon"] == yon:
            return {"acildi": False, "neden": f"Zaten açık {yon} pozisyon var (#{t['id']})"}

    rejim_obj = karar.get("rejim", {})
    rejim = rejim_obj.get("rejim", "UNKNOWN") if isinstance(rejim_obj, dict) else "UNKNOWN"
    atr_pct = rejim_obj.get("atr_pct", 0) if isinstance(rejim_obj, dict) else 0

    giris = await _fiyat(sembol)
    sl, tp = _sl_tp_hesapla(yon, giris, atr_pct, rejim)

    trade_id = db.trade_ac({
        "sembol": sembol,
        "yon": yon,
        "giris": round(giris, 2),
        "sl": sl,
        "tp": tp,
        "miktar": POZISYON_USD,
        "konfidans": konfidans,
        "rejim": rejim,
        "karar_id": karar_id,
        "not_metni": karar.get("karar_nedeni", "")[:200],
    })

    return {
        "acildi": True,
        "trade_id": trade_id,
        "yon": yon,
        "giris": round(giris, 2),
        "sl": sl,
        "tp": tp,
        "rejim": rejim,
        "konfidans": konfidans,
    }


async def acik_tradeleri_kontrol(sembol: str = None) -> list:
    """
    Tüm açık trade'leri canlı fiyata karşı kontrol eder.
    SL veya TP'ye değen pozisyonları kapatır. Kapanan trade listesi döner.
    """
    acik = db.acik_tradeler(sembol)
    if not acik:
        return []

    # Sembollere göre fiyatları topla
    semboller = list({t["sembol"] for t in acik})
    fiyatlar = {}
    for s in semboller:
        try:
            fiyatlar[s] = await _fiyat(s)
        except Exception:
            pass

    kapanan = []
    for t in acik:
        fiyat = fiyatlar.get(t["sembol"])
        if fiyat is None:
            continue
        yon, sl, tp = t["yon"], t["sl"], t["tp"]
        sonuc = None
        cikis = None

        if yon == "LONG":
            if sl and fiyat <= sl:
                sonuc, cikis = "LOSS", sl
            elif tp and fiyat >= tp:
                sonuc, cikis = "WIN", tp
        else:  # SHORT
            if sl and fiyat >= sl:
                sonuc, cikis = "LOSS", sl
            elif tp and fiyat <= tp:
                sonuc, cikis = "WIN", tp

        if sonuc:
            kapali = db.trade_kapat(t["id"], cikis, sonuc)
            kapanan.append(kapali)
            print(f"[PaperTrade] #{t['id']} {t['sembol']} {yon} kapandı: "
                  f"{sonuc} @ {cikis} (PnL %{kapali.get('pnl_pct')})")

    return kapanan


async def paper_trade_loop(interval: int = 120):
    """
    Arka plan döngüsü: açık trade'leri periyodik kontrol eder.
    main.py startup'ında başlatılır.
    """
    db.init_db()
    await asyncio.sleep(20)
    while True:
        try:
            await acik_tradeleri_kontrol()
        except Exception as e:
            print(f"[PaperTrade] kontrol hatası: {str(e)[:80]}")
        await asyncio.sleep(interval)


def ozet() -> dict:
    """UI için paper trade performans özeti + açık pozisyonlar."""
    stat = db.trade_istatistik()
    acik = db.acik_tradeler()
    return {
        "istatistik": stat,
        "acik_pozisyonlar": acik,
        "son_kapananlar": [t for t in db.trade_gecmisi(20) if t["durum"] == "CLOSED"][:10],
    }


if __name__ == "__main__":
    import json
    db.init_db()
    print(json.dumps(ozet(), ensure_ascii=False, indent=2))
