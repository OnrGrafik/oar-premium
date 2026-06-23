"""
Paper Trade Agent — OAR Premium
═══════════════════════════════════════════════════════
CIO Karar Motoru LONG/SHORT ürettiğinde sanal pozisyon açar,
canlı fiyata karşı SL/TP takip eder, sonucu SQLite'a kaydeder.

Gerçek para riski yok — sistemin kararlarının gerçek performansını
ölçmenin tek dürüst yolu. Backtest geçmişe bakar; bu, ileriye doğru
(forward test) gerçek zamanlı doğrulama yapar.

Sağlamlaştırmalar:
  • High/Low mumu ile SL/TP kontrolü (sadece last price değil)
  • Time-stop: MAX_SURE saat sonra kapatılmayan pozisyon mecburi kapanır
  • Fee/slippage: gerçekçi net PnL için %0.07 round-trip düşülür
  • Max açık pozisyon tavanı: sembol başına 1, toplam 3
  • Risk tavanı: konfidans < KONFIDANS_ESIK ise trade açılmaz
"""

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

import persistence as db
from exchange_client import klines as _ec_klines, ticker_price as _ticker

KONFIDANS_ESIK = 60.0      # bu konfidansın altında trade açılmaz
POZISYON_USD   = 1000.0    # sanal pozisyon büyüklüğü (USD)
FEE_ROUNDTRIP  = 0.0007    # %0.07 round-trip fee + slippage
MAX_SURE_SAAT  = 48        # time-stop: 48 saat sonra zorla kapat
MAX_ACIK_TOPLAM = 3        # aynı anda max pozisyon sayısı


async def _fiyat(sembol: str = "BTCUSDT") -> float:
    return await _ticker(sembol, futures=False)


async def _son_mum_hl(sembol: str) -> tuple[float, float]:
    """Son 5 dakikalık mumun high ve low'unu döndür — SL/TP wick kontrolü için."""
    try:
        rows = await _ec_klines(sembol, "5m", 1, futures=False)
        if rows:
            return rows[-1][2], rows[-1][3]  # high, low
    except Exception:
        pass
    fiyat = await _fiyat(sembol)
    return fiyat, fiyat


def _sl_tp_hesapla(yon: str, giris: float, atr_pct: float, rejim: str) -> tuple:
    """
    Rejime göre SL/TP seviyeleri üretir.
    atr_pct yoksa makul varsayılan (%1.5) kullanılır.
    """
    atr = (atr_pct or 1.5) / 100 * giris

    # (SL_mult, TP_mult) → R:R oranı
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
    else:
        sl = giris + atr * sl_mult
        tp = giris - atr * tp_mult
    return round(sl, 2), round(tp, 2)


def _net_pnl(yon: str, giris: float, cikis: float, miktar: float) -> tuple[float, float]:
    """Fee düşüldükten sonra gerçekçi PnL. (pnl_pct, pnl_usd)"""
    if yon == "LONG":
        gross = (cikis - giris) / giris
    else:
        gross = (giris - cikis) / giris
    net = gross - FEE_ROUNDTRIP
    return round(net * 100, 3), round(miktar * net, 2)


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

    # Max toplam açık pozisyon
    tumacik = db.acik_tradeler()
    if len(tumacik) >= MAX_ACIK_TOPLAM:
        return {"acildi": False, "neden": f"Max açık pozisyon tavanı ({MAX_ACIK_TOPLAM}) doldu"}

    # Aynı sembol+yönde açık var mı?
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


async def acik_tradeleri_kontrol(sembol: Optional[str] = None) -> list:
    """
    Tüm açık trade'leri kontrol eder.
    - 5m mumun HIGH/LOW ile SL/TP wicklerini yakalar
    - 48 saat sonra time-stop uygular
    Kapanan trade listesi döner.
    """
    acik = db.acik_tradeler(sembol)
    if not acik:
        return []

    # Sembollere göre anlık fiyat + son mum H/L
    semboller = list({t["sembol"] for t in acik})
    fiyatlar, highs, lows = {}, {}, {}
    for s in semboller:
        try:
            h, l = await _son_mum_hl(s)
            highs[s], lows[s] = h, l
            fiyatlar[s] = (h + l) / 2
        except Exception:
            try:
                f = await _fiyat(s)
                fiyatlar[s] = highs[s] = lows[s] = f
            except Exception:
                pass

    now = datetime.now(timezone.utc)
    kapanan = []

    for t in acik:
        s = t["sembol"]
        if s not in fiyatlar:
            continue

        high = highs[s]
        low  = lows[s]
        yon, sl, tp = t["yon"], t["sl"], t["tp"]
        miktar = t.get("miktar") or POZISYON_USD
        sonuc = None
        cikis_fiyat = None

        # Time-stop kontrolü
        try:
            acilis = datetime.fromisoformat(t["acilis_tarih"])
            if acilis.tzinfo is None:
                acilis = acilis.replace(tzinfo=timezone.utc)
            sure_saat = (now - acilis).total_seconds() / 3600
        except Exception:
            sure_saat = 0

        if sure_saat >= MAX_SURE_SAAT:
            cikis_fiyat = fiyatlar[s]
            sonuc = "TIME_STOP"
        elif yon == "LONG":
            if sl and low <= sl:
                sonuc, cikis_fiyat = "LOSS", sl
            elif tp and high >= tp:
                sonuc, cikis_fiyat = "WIN", tp
        else:  # SHORT
            if sl and high >= sl:
                sonuc, cikis_fiyat = "LOSS", sl
            elif tp and low <= tp:
                sonuc, cikis_fiyat = "WIN", tp

        if sonuc:
            pnl_pct, pnl_usd = _net_pnl(yon, t["giris"], cikis_fiyat, miktar)
            kapali = db.trade_kapat_net(t["id"], cikis_fiyat, sonuc, pnl_pct, pnl_usd)
            kapanan.append(kapali)
            print(f"[PaperTrade] #{t['id']} {s} {yon} kapandı: "
                  f"{sonuc} @ {cikis_fiyat} (Net PnL %{pnl_pct}, ${pnl_usd})")

            # Öğrenme döngüsünü kapat
            try:
                from learning_engine import trade_sonucundan_ogren
                karar_json = db.karar_detay_json(t["karar_id"]) if t.get("karar_id") else {}
                trade_sonucundan_ogren(kapali, karar_json)
            except Exception as le:
                print(f"[PaperTrade] learning_engine hatası: {str(le)[:60]}")

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
