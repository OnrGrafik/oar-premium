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

def _ema(closes: list, period: int) -> float:
    if len(closes) < period:
        return closes[-1] if closes else 0.0
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for p in closes[period:]:
        ema = p * k + ema * (1 - k)
    return ema


def _rsi_ind(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    return round(100 - 100 / (1 + ag / al), 2)


def _macd_ind(closes: list) -> dict:
    """EMA12 - EMA26 MACD; signal EMA9; histogram."""
    if len(closes) < 35:
        return {"hist": 0.0, "line": 0.0, "signal": 0.0, "yon": "NOTR"}
    line = _ema(closes, 12) - _ema(closes, 26)
    macd_seri = []
    for i in range(9, 0, -1):
        seg = closes[:-i]
        if len(seg) >= 26:
            macd_seri.append(_ema(seg, 12) - _ema(seg, 26))
    macd_seri.append(line)
    signal = _ema(macd_seri, 9) if len(macd_seri) >= 9 else line
    hist = line - signal
    return {
        "hist":   round(hist, 4),
        "line":   round(line, 4),
        "signal": round(signal, 4),
        "yon":    "YUKARI" if hist > 0 else "ASAGI",
    }


def _bollinger_ind(closes: list, period: int = 20) -> dict:
    """Bollinger Bantları: üst, orta, alt, %B, bant genişliği."""
    if len(closes) < period:
        p = closes[-1] if closes else 0.0
        return {"ust": p, "orta": p, "alt": p, "pct_b": 0.5, "bw": 0.0, "pozisyon": "ICERIDE"}
    son = closes[-period:]
    orta = sum(son) / period
    std = math.sqrt(sum((x - orta) ** 2 for x in son) / period)
    ust = orta + 2 * std
    alt = orta - 2 * std
    fiyat = closes[-1]
    pct_b = (fiyat - alt) / (ust - alt) if ust != alt else 0.5
    bw = (ust - alt) / orta * 100 if orta else 0.0

    if fiyat > ust:
        pozisyon = "USTU"        # Aşırı uzatılmış — olası tersine dönüş
    elif fiyat < alt:
        pozisyon = "ALTI"        # Aşırı satım — olası tepki
    elif pct_b > 0.7:
        pozisyon = "UST_BANT"
    elif pct_b < 0.3:
        pozisyon = "ALT_BANT"
    else:
        pozisyon = "ICERIDE"

    return {
        "ust":      round(ust, 2),
        "orta":     round(orta, 2),
        "alt":      round(alt, 2),
        "pct_b":    round(pct_b, 3),   # 0=alt bant, 1=üst bant
        "bw":       round(bw, 2),       # Düşük bw = sıkışma (breakout yakın)
        "pozisyon": pozisyon,
    }


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

        # Yeni indikatörler
        rsi = _rsi_ind(closes)
        macd = _macd_ind(closes)
        bb = _bollinger_ind(closes)

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
            nedenler.append(f"Sıkışma: band/ATR={range_oran:.1f}, ATR%={atr_pct:.1f} | BB bw={bb['bw']:.1f}%")

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

        # RSI teyidi — güveni artırır veya azaltır
        if yapi == "TREND_UP":
            if rsi > 50 and macd["yon"] == "YUKARI":
                guven = min(90, guven + 8)
                nedenler.append(f"RSI={rsi} + MACD yukarı — trend teyit")
            elif rsi < 40:
                guven = max(40, guven - 10)
                nedenler.append(f"RSI={rsi} zayıf — trend yorgunluğu dikkat")
        elif yapi == "TREND_DOWN":
            if rsi < 50 and macd["yon"] == "ASAGI":
                guven = min(90, guven + 8)
                nedenler.append(f"RSI={rsi} + MACD aşağı — trend teyit")
            elif rsi > 60:
                guven = max(40, guven - 10)
                nedenler.append(f"RSI={rsi} güçlü — aşağı trend zayıflıyor")

        # Bollinger sıkışması RANGE/COMPRESSION güvenini artırır
        if bb["bw"] < 3.0 and yapi in ("RANGE", "COMPRESSION"):
            guven = min(85, guven + 5)
            nedenler.append(f"BB sıkışma: bant genişliği=%{bb['bw']:.1f} (breakout yakın)")

        # Bollinger aşırı bölge uyarısı
        if bb["pozisyon"] == "USTU":
            nedenler.append(f"BB: Fiyat üst bandın üstünde (aşırı uzatılmış, %B={bb['pct_b']:.2f})")
        elif bb["pozisyon"] == "ALTI":
            nedenler.append(f"BB: Fiyat alt bandın altında (aşırı satım, %B={bb['pct_b']:.2f})")

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
            # ── Yeni indikatörler ──
            "rsi":         rsi,
            "macd":        macd,
            "bollinger":   bb,
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
        "rsi": 50.0,
        "macd": {"hist": 0.0, "line": 0.0, "signal": 0.0, "yon": "NOTR"},
        "bollinger": {"ust": 0.0, "orta": 0.0, "alt": 0.0, "pct_b": 0.5, "bw": 0.0, "pozisyon": "ICERIDE"},
        "guven": 0, "aciklama": f"Market structure hatası: {hata}",
        "timeframe": timeframe, "sembol": sembol,
    }


if __name__ == "__main__":
    import json
    r = asyncio.run(coklu_timeframe_analiz("BTCUSDT"))
    print(json.dumps(r, ensure_ascii=False, indent=2))
