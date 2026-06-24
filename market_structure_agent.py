"""
Market Structure Agent — OAR Premium
══════════════════════════════════════════════════════════════════
Piyasa yapısını tespit eder:
  TREND_UP / TREND_DOWN / RANGE / EXPANSION / COMPRESSION

Ayrıca her timeframe için:
  - Swing High / Swing Low seviyeleri
  - BOS (Break of Structure)
  - CHoCH (Change of Character)
  - Higher High / Lower Low zinciri

regime_engine.py'yi temel alır, scalper ve swing için daha
ayrıntılı yapı bilgisi ekler.
"""

import asyncio
import math
from exchange_client import klines as _klines


# ─── Yardımcı hesaplamalar ────────────────────────────────────────

def _swing_highs_lows(candles: list, pencere: int = 3) -> dict:
    """
    Basit pivot swing high / low tespiti.
    pencere: her iki yanda kaç mum kontrol edilsin.
    """
    highs, lows = [], []
    for i in range(pencere, len(candles) - pencere):
        h = candles[i][2]
        l = candles[i][3]
        ts = int(candles[i][0])
        if all(h >= candles[j][2] for j in range(i - pencere, i + pencere + 1) if j != i):
            highs.append({"ts": ts, "fiyat": h, "indeks": i})
        if all(l <= candles[j][3] for j in range(i - pencere, i + pencere + 1) if j != i):
            lows.append({"ts": ts, "fiyat": l, "indeks": i})
    return {"swing_highs": highs[-6:], "swing_lows": lows[-6:]}


def _hh_ll_zinciri(swing_highs: list, swing_lows: list) -> str:
    """Son 3 pivot'a bakarak HH/HL veya LH/LL zinciri tespiti."""
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "BELIRSIZ"

    son_yh = swing_highs[-1]["fiyat"]
    onceki_yh = swing_highs[-2]["fiyat"]
    son_dl = swing_lows[-1]["fiyat"]
    onceki_dl = swing_lows[-2]["fiyat"]

    yukari = son_yh > onceki_yh and son_dl > onceki_dl   # HH + HL
    asagi = son_yh < onceki_yh and son_dl < onceki_dl    # LH + LL

    if yukari:
        return "HH_HL"
    if asagi:
        return "LH_LL"
    return "KARISIK"


def _bos_choch(candles: list, swing_highs: list, swing_lows: list) -> dict:
    """
    BOS (Break of Structure) ve CHoCH (Change of Character) tespiti.
    Son mumun son swing high/low'u kırıp kırmadığına bakar.
    """
    kapanis = candles[-1][4]
    bos = "YOK"
    choch = "YOK"

    if swing_highs and kapanis > swing_highs[-1]["fiyat"]:
        bos = "YUKARI_BOS"
    elif swing_lows and kapanis < swing_lows[-1]["fiyat"]:
        bos = "ASAGI_BOS"

    # CHoCH: zincir tersine döndü mü?
    zincir = _hh_ll_zinciri(swing_highs, swing_lows)
    if zincir == "LH_LL" and bos == "YUKARI_BOS":
        choch = "BULLISH_CHOCH"
    elif zincir == "HH_HL" and bos == "ASAGI_BOS":
        choch = "BEARISH_CHOCH"

    return {"bos": bos, "choch": choch}


def _atr(candles: list, period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i][2], candles[i][3], candles[i - 1][4]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period


def _range_genisligi(candles: list, period: int = 20) -> float:
    """Son N mumun high-low bandı / ATR oranı — genişleme tespiti."""
    son = candles[-period:]
    band = max(c[2] for c in son) - min(c[3] for c in son)
    atr = _atr(candles)
    if atr == 0:
        return 0.0
    return band / atr


def _sma(closes: list, period: int) -> float:
    if len(closes) < period:
        return closes[-1]
    return sum(closes[-period:]) / period


def _adx(candles: list, period: int = 14) -> float:
    """Basitleştirilmiş ADX hesabı (trend gücü 0-100)."""
    if len(candles) < period + 2:
        return 25.0

    plus_dm, minus_dm, tr_list = [], [], []
    for i in range(1, len(candles)):
        h, l = candles[i][2], candles[i][3]
        ph, pl, pc = candles[i - 1][2], candles[i - 1][3], candles[i - 1][4]
        up = h - ph
        down = pl - l
        plus_dm.append(up if up > down and up > 0 else 0)
        minus_dm.append(down if down > up and down > 0 else 0)
        tr_list.append(max(h - l, abs(h - pc), abs(l - pc)))

    def smooth(lst):
        s = sum(lst[:period])
        result = [s]
        for v in lst[period:]:
            s = s - s / period + v
            result.append(s)
        return result

    atr_s = smooth(tr_list)
    pdm_s = smooth(plus_dm)
    mdm_s = smooth(minus_dm)

    dx_list = []
    for a, p, m in zip(atr_s, pdm_s, mdm_s):
        if a == 0:
            continue
        pdi = 100 * p / a
        mdi = 100 * m / a
        dx = 100 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) > 0 else 0
        dx_list.append(dx)

    if not dx_list:
        return 25.0
    return sum(dx_list[-period:]) / min(period, len(dx_list))


# ─── Ana analiz fonksiyonu ────────────────────────────────────────

async def market_structure_analiz(
    sembol: str = "BTCUSDT",
    timeframe: str = "1h",
    limit: int = 100,
) -> dict:
    """
    Piyasa yapısını analiz eder.

    Returns:
        {
            "yapi":         TREND_UP|TREND_DOWN|RANGE|EXPANSION|COMPRESSION,
            "zincir":       HH_HL|LH_LL|KARISIK|BELIRSIZ,
            "bos":          YUKARI_BOS|ASAGI_BOS|YOK,
            "choch":        BULLISH_CHOCH|BEARISH_CHOCH|YOK,
            "adx":          float,
            "atr_pct":      float,
            "range_oran":   float,   # band / ATR — yüksekse expansion
            "swing_highs":  list,
            "swing_lows":   list,
            "fiyat":        float,
            "sma20":        float,
            "sma50":        float,
            "guven":        0-100,
            "aciklama":     str,
            "timeframe":    str,
            "sembol":       str,
        }
    """
    try:
        data = await _klines(sembol, timeframe, limit, futures=True)
        if not data or len(data) < 30:
            return _varsayilan("Yetersiz veri", sembol, timeframe)

        candles = [[float(x) for x in row[:6]] for row in data]
        closes = [c[4] for c in candles]
        fiyat = closes[-1]

        sma20 = _sma(closes, 20)
        sma50 = _sma(closes, 50)
        atr = _atr(candles)
        atr_pct = atr / fiyat * 100
        adx = _adx(candles)
        range_oran = _range_genisligi(candles, 20)

        pivotlar = _swing_highs_lows(candles, pencere=3)
        swing_highs = pivotlar["swing_highs"]
        swing_lows = pivotlar["swing_lows"]
        zincir = _hh_ll_zinciri(swing_highs, swing_lows)
        bos_choch = _bos_choch(candles, swing_highs, swing_lows)

        # ── Yapı kararı ──────────────────────────────────────────
        yapi = "RANGE"
        guven = 50
        nedenler = []

        if range_oran > 4.0 and atr_pct > 3.0:
            yapi = "EXPANSION"
            guven = 80
            nedenler.append(f"Genişleme: band/ATR={range_oran:.1f}, ATR%={atr_pct:.1f}")

        elif range_oran < 1.5 and atr_pct < 1.0:
            yapi = "COMPRESSION"
            guven = 75
            nedenler.append(f"Sıkışma: band/ATR={range_oran:.1f}, ATR%={atr_pct:.1f}")

        elif adx > 25 and zincir == "HH_HL":
            yapi = "TREND_UP"
            guven = min(90, int(50 + adx))
            nedenler.append(f"Yukarı trend: ADX={adx:.0f}, zincir={zincir}, fiyat SMA20 {'üst' if fiyat > sma20 else 'alt'}ında")

        elif adx > 25 and zincir == "LH_LL":
            yapi = "TREND_DOWN"
            guven = min(90, int(50 + adx))
            nedenler.append(f"Aşağı trend: ADX={adx:.0f}, zincir={zincir}, fiyat SMA20 {'üst' if fiyat > sma20 else 'alt'}ında")

        else:
            yapi = "RANGE"
            guven = 60
            nedenler.append(f"Range: ADX={adx:.0f}, zincir={zincir}, band/ATR={range_oran:.1f}")

        if bos_choch["choch"] != "YOK":
            nedenler.append(f"CHoCH tespit: {bos_choch['choch']}")
        if bos_choch["bos"] != "YOK":
            nedenler.append(f"BOS: {bos_choch['bos']}")

        return {
            "yapi":        yapi,
            "zincir":      zincir,
            "bos":         bos_choch["bos"],
            "choch":       bos_choch["choch"],
            "adx":         round(adx, 1),
            "atr_pct":     round(atr_pct, 2),
            "range_oran":  round(range_oran, 2),
            "swing_highs": swing_highs,
            "swing_lows":  swing_lows,
            "fiyat":       fiyat,
            "sma20":       round(sma20, 2),
            "sma50":       round(sma50, 2),
            "guven":       guven,
            "aciklama":    " | ".join(nedenler),
            "timeframe":   timeframe,
            "sembol":      sembol,
        }

    except Exception as e:
        return _varsayilan(str(e), sembol, timeframe)


async def coklu_timeframe_analiz(sembol: str = "BTCUSDT") -> dict:
    """15m, 1h, 4h yapısını aynı anda çekip özetler."""
    tf_listesi = ["15m", "1h", "4h"]
    sonuclar = await asyncio.gather(
        *[market_structure_analiz(sembol, tf) for tf in tf_listesi],
        return_exceptions=True,
    )
    cikti = {}
    for tf, s in zip(tf_listesi, sonuclar):
        cikti[tf] = s if isinstance(s, dict) else _varsayilan(str(s), sembol, tf)

    # Genel yorum: 2/3 timeframe aynı yöndeyse "HIZALI"
    yapilar = [cikti[tf]["yapi"] for tf in tf_listesi]
    trend_up = yapilar.count("TREND_UP")
    trend_down = yapilar.count("TREND_DOWN")
    hizalama = ("BULLISH_HIZALI" if trend_up >= 2 else
                "BEARISH_HIZALI" if trend_down >= 2 else "KARISIK")

    cikti["hizalama"] = hizalama
    cikti["ozet"] = (f"MTF Yapı: {hizalama} | "
                     f"15m={yapilar[0]} 1h={yapilar[1]} 4h={yapilar[2]}")
    return cikti


def _varsayilan(hata: str, sembol: str, timeframe: str) -> dict:
    return {
        "yapi": "UNKNOWN", "zincir": "BELIRSIZ",
        "bos": "YOK", "choch": "YOK",
        "adx": 0.0, "atr_pct": 0.0, "range_oran": 0.0,
        "swing_highs": [], "swing_lows": [],
        "fiyat": 0.0, "sma20": 0.0, "sma50": 0.0,
        "guven": 0, "aciklama": f"Market structure hatası: {hata}",
        "timeframe": timeframe, "sembol": sembol,
    }


if __name__ == "__main__":
    import json
    r = asyncio.run(coklu_timeframe_analiz("BTCUSDT"))
    print(json.dumps(r, ensure_ascii=False, indent=2))
