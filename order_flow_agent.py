"""
Order Flow Agent — OAR Premium
══════════════════════════════════════════════════════════════════
CVD, Open Interest, Funding Rate ve Coinbase Premium'u
birleştirerek order flow yönünü ve gücünü tespit eder.

Sinyal hiyerarşisi:
  CVD yönü + OI değişimi + Funding taraflılığı + CB Premium
  → BULLISH_FLOW / BEARISH_FLOW / NEUTRAL_FLOW
"""

import asyncio
import httpx
from exchange_client import (
    klines as _klines,
    open_interest as _oi,
    funding_rate as _funding,
    taker_ratio as _taker,
)


# ─── CVD (Cumulative Volume Delta) ───────────────────────────────

def _cvd_hesapla(candles: list) -> list:
    """
    Her mum için tahmini delta hesaplar (Kaygın formül).
    delta = (close - low) / (high - low) * volume * 2 - volume
    Pozitif → alım baskısı, Negatif → satım baskısı.
    """
    cvd = []
    kumulatif = 0.0
    for c in candles:
        o, h, l, cl, v = c[1], c[2], c[3], c[4], c[5]
        hl = h - l
        if hl == 0:
            delta = 0.0
        else:
            buy_vol = (cl - l) / hl * v
            sell_vol = v - buy_vol
            delta = buy_vol - sell_vol
        kumulatif += delta
        cvd.append({"ts": int(c[0]), "delta": delta, "cvd": kumulatif, "volume": v})
    return cvd


def _cvd_trend(cvd_list: list, pencere: int = 10) -> dict:
    """Son N mumda CVD yönü ve ivmesi."""
    if len(cvd_list) < pencere:
        return {"yon": "NOTR", "ivme": 0.0, "son_cvd": 0.0, "cvd_degisim": 0.0}
    son = cvd_list[-pencere:]
    baslangic = son[0]["cvd"]
    bitis = son[-1]["cvd"]
    degisim = bitis - baslangic
    hacim_ort = sum(x["volume"] for x in son) / pencere
    # Normalize: CVD değişimi / ortalama hacim
    normalize = degisim / hacim_ort if hacim_ort else 0.0

    yon = "BULLISH" if normalize > 0.05 else "BEARISH" if normalize < -0.05 else "NOTR"
    return {
        "yon": yon,
        "ivme": round(normalize, 4),
        "son_cvd": round(bitis, 2),
        "cvd_degisim": round(degisim, 2),
    }


# ─── Open Interest Analizi ────────────────────────────────────────

def _oi_analiz(oi_list: list) -> dict:
    """OI değişimi ve yönü."""
    if not oi_list or len(oi_list) < 3:
        return {"yon": "NOTR", "degisim_pct": 0.0, "artiyor": False}

    baslangic = oi_list[0]["oi"]
    son = oi_list[-1]["oi"]
    if baslangic == 0:
        return {"yon": "NOTR", "degisim_pct": 0.0, "artiyor": False}

    degisim_pct = (son - baslangic) / baslangic * 100
    artiyor = degisim_pct > 1.0
    azaliyor = degisim_pct < -1.0

    yon = "ARTIYOR" if artiyor else "AZALIYOR" if azaliyor else "YATAY"
    return {
        "yon": yon,
        "degisim_pct": round(degisim_pct, 2),
        "artiyor": artiyor,
        "son_oi": round(son, 2),
    }


# ─── Funding Rate Yorumu ──────────────────────────────────────────

def _funding_yorum(rate: float) -> dict:
    """
    Funding rate yorumu.
    Pozitif = longlar short'lara ödüyor → piyasa aşırı uzun
    Negatif = shortlar longlara ödüyor → piyasa aşırı kısa
    """
    pct = rate * 100  # ondalık → yüzde
    if pct > 0.05:
        taraflilik = "ASIRI_LONG"
        yorum = f"Funding yüksek pozitif (%{pct:.3f}) — long kalabalık, short squeeze potansiyeli düşük"
    elif pct < -0.01:
        taraflilik = "ASIRI_SHORT"
        yorum = f"Funding negatif (%{pct:.3f}) — short kalabalık, long squeeze potansiyeli yüksek"
    elif pct > 0.01:
        taraflilik = "HAFIF_LONG"
        yorum = f"Funding hafif pozitif (%{pct:.3f}) — nötr-bullish"
    else:
        taraflilik = "NOTR"
        yorum = f"Funding nötr (%{pct:.3f})"

    return {"taraflilik": taraflilik, "rate_pct": round(pct, 4), "yorum": yorum}


# ─── Coinbase Premium ─────────────────────────────────────────────

async def _coinbase_premium(sembol: str = "BTC") -> dict:
    """
    Coinbase spot fiyatı ile Binance spot arasındaki fark.
    Pozitif premium → ABD kurumsal alım baskısı.
    Negatif premium → Coinbase'de satış ağırlıklı.
    """
    try:
        cb_sembol = f"{sembol}-USD"
        async with httpx.AsyncClient(timeout=8) as c:
            cb_r = await c.get(f"https://api.exchange.coinbase.com/products/{cb_sembol}/ticker")
            cb_fiyat = float(cb_r.json()["price"])

        async with httpx.AsyncClient(timeout=8) as c:
            bn_r = await c.get(f"https://api.binance.com/api/v3/ticker/price",
                                params={"symbol": f"{sembol}USDT"})
            bn_fiyat = float(bn_r.json()["price"])

        premium = cb_fiyat - bn_fiyat
        premium_pct = premium / bn_fiyat * 100

        return {
            "cb_fiyat": cb_fiyat,
            "bn_fiyat": bn_fiyat,
            "premium": round(premium, 2),
            "premium_pct": round(premium_pct, 4),
            "yon": ("POZITIF" if premium_pct > 0.02 else
                    "NEGATIF" if premium_pct < -0.02 else "NOTR"),
            "yorum": (f"CB premium {'pozitif' if premium > 0 else 'negatif'}: "
                      f"CB={cb_fiyat:.1f} BN={bn_fiyat:.1f} (fark %{premium_pct:.3f})"),
        }
    except Exception as e:
        return {
            "cb_fiyat": 0.0, "bn_fiyat": 0.0,
            "premium": 0.0, "premium_pct": 0.0,
            "yon": "BILINMIYOR",
            "yorum": f"Coinbase premium alınamadı: {str(e)[:80]}",
        }


# ─── Ana Analiz ───────────────────────────────────────────────────

async def order_flow_analiz(
    sembol: str = "BTCUSDT",
    timeframe: str = "5m",
    limit: int = 50,
) -> dict:
    """
    CVD + OI + Funding + Coinbase Premium birleşik analiz.

    Returns:
        {
            "karar":         BULLISH_FLOW|BEARISH_FLOW|NEUTRAL_FLOW,
            "puan":          -100 .. +100  (pozitif = bullish)
            "cvd":           {...}
            "oi":            {...}
            "funding":       {...}
            "cb_premium":    {...}
            "taker":         {"son_oran": float}
            "aciklama":      str
            "sembol":        str
        }
    """
    # Sembol kökü (BTC / ETH)
    kok = sembol.replace("USDT", "").replace("PERP", "")

    candles, oi_data, funding, taker = await asyncio.gather(
        _klines(sembol, timeframe, limit, futures=True),
        _oi(sembol, interval="5m", limit=20),
        _funding(sembol),
        _taker(sembol, interval="5m", limit=10),
        return_exceptions=True,
    )
    cb_prem = await _coinbase_premium(kok)

    # CVD
    if isinstance(candles, list) and candles:
        cvd_list = _cvd_hesapla(candles)
        cvd = _cvd_trend(cvd_list, pencere=10)
    else:
        cvd = {"yon": "NOTR", "ivme": 0.0, "son_cvd": 0.0, "cvd_degisim": 0.0}

    # OI
    oi = _oi_analiz(oi_data if isinstance(oi_data, list) else [])

    # Funding
    fr = _funding_yorum(funding if isinstance(funding, float) else 0.0)

    # Taker
    if isinstance(taker, list) and taker:
        son_taker = taker[-1]["long_ratio"]
    else:
        son_taker = 0.5

    # ── Puanlama: -100 / +100 ──────────────────────────────────
    puan = 0

    # ── ÖNEM SIRALI puanlama — HİÇBİR İKİ FAKTÖR EŞİT DEĞİL ──────────
    # Order-flow biliminde sinyal gücü sırası (en doğrudan → en yavaş):
    #   CVD(30) > Coinbase Premium(22) > OI(18) > Taker(14) > Funding(10)
    # Önemli faktör daha çok, önemsiz daha az puan; eşit ağırlık yok.

    # 1) CVD — agresör delta, en doğrudan akış sinyali (±30)
    if cvd["yon"] == "BULLISH":
        puan += 30
    elif cvd["yon"] == "BEARISH":
        puan -= 30

    # 2) Coinbase Premium — spot/kurumsal taraf teyidi (±22)
    if cb_prem["yon"] == "POZITIF":
        puan += 22
    elif cb_prem["yon"] == "NEGATIF":
        puan -= 22

    # 3) OI — pozisyonlanma; yönünü CVD ile kazanır (±18, tek başına zayıf +6/-9)
    if oi["artiyor"]:
        if cvd["yon"] == "BULLISH":
            puan += 18
        elif cvd["yon"] == "BEARISH":
            puan -= 18
        else:
            puan += 6   # belirsiz ama OI artışı = ilgi
    elif oi["yon"] == "AZALIYOR":
        puan -= 9       # pozisyon kapatma

    # 4) Taker oranı — anlık agresif alış/satış baskısı (±14)
    if son_taker > 0.55:
        puan += 14
    elif son_taker < 0.45:
        puan -= 14

    # 5) Funding — kalabalık/contrarian, en yavaş sinyal (±10, asimetrik squeeze)
    if fr["taraflilik"] == "ASIRI_SHORT":
        puan += 10   # short squeeze potansiyeli
    elif fr["taraflilik"] == "ASIRI_LONG":
        puan -= 7    # long kalabalık — contrarian
    elif fr["taraflilik"] == "HAFIF_LONG":
        puan += 3

    puan = max(-100, min(100, puan))

    karar = ("BULLISH_FLOW" if puan >= 25 else
             "BEARISH_FLOW" if puan <= -25 else "NEUTRAL_FLOW")

    aciklama_parcalar = [
        f"CVD: {cvd['yon']} (ivme={cvd['ivme']:+.4f})",
        f"OI: {oi['yon']} (%{oi['degisim_pct']:+.1f})",
        f"Funding: {fr['taraflilik']} (%{fr['rate_pct']:.4f})",
        f"CB Premium: {cb_prem['yon']} (%{cb_prem['premium_pct']:+.3f})",
        f"Taker Long: %{son_taker*100:.0f}",
    ]

    return {
        "karar":      karar,
        "puan":       puan,
        "cvd":        cvd,
        "oi":         oi,
        "funding":    fr,
        "cb_premium": cb_prem,
        "taker":      {"son_oran": round(son_taker, 3)},
        "aciklama":   " | ".join(aciklama_parcalar),
        "sembol":     sembol,
    }


if __name__ == "__main__":
    import json
    r = asyncio.run(order_flow_analiz("BTCUSDT", "5m"))
    print(json.dumps(r, ensure_ascii=False, indent=2))
