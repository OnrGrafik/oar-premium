"""
Regime Engine — OAR Premium
═══════════════════════════════════════════════════════
Piyasa rejimini tespit eder: TREND_UP / TREND_DOWN / RANGE / HIGH_VOL / PANIC

exchange_client üzerinden klines çeker (Binance→Bybit fallback, retry dahil).
"""

import asyncio
import math
from exchange_client import klines as _klines


def _atr(candles: list, period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h = candles[i][2]
        l = candles[i][3]
        pc = candles[i-1][4]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period


def _rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _realized_vol(closes: list, period: int = 20) -> float:
    """Annualized realized volatility (log returns)."""
    if len(closes) < period + 1:
        return 0.0
    log_rets = [math.log(closes[i] / closes[i-1]) for i in range(len(closes)-period, len(closes))]
    mean = sum(log_rets) / len(log_rets)
    variance = sum((r - mean) ** 2 for r in log_rets) / len(log_rets)
    return math.sqrt(variance * 365) * 100  # yıllık %


async def rejim_tespit(sembol: str = "BTCUSDT") -> dict:
    """
    Piyasa rejimini tespit eder.

    Returns:
        {
            "rejim": "TREND_UP" | "TREND_DOWN" | "RANGE" | "HIGH_VOL" | "PANIC",
            "guvenis": 0-100,
            "atr_pct": float,           # ATR / fiyat * 100
            "rsi": float,
            "realized_vol_pct": float,  # 20g yıllık vol %
            "fiyat": float,
            "sma20": float,
            "aciklama": str
        }
    """
    try:
        data = await _klines(sembol, "1d", 35, futures=False)

        if not isinstance(data, list) or len(data) < 20:
            return _varsayilan_rejim("Yetersiz veri")

        candles = [[float(x) for x in row[:6]] for row in data]
        # [open_time, open, high, low, close, volume]
        closes   = [c[4] for c in candles]
        fiyat    = closes[-1]
        sma20    = sum(closes[-20:]) / 20
        atr      = _atr(candles)
        atr_pct  = atr / fiyat * 100
        rsi      = _rsi(closes)
        rv       = _realized_vol(closes)

        # Son 5 günün yönü
        son5 = closes[-5:]
        yukari_gun = sum(1 for i in range(1, len(son5)) if son5[i] > son5[i-1])
        asagi_gun  = 4 - yukari_gun

        # ─── Rejim kararı ───────────────────────────────────────
        rejim = "RANGE"
        guven = 50
        neden = []

        if rsi < 28 and asagi_gun >= 3 and rv > 80:
            rejim = "PANIC"
            guven = 85
            neden.append(f"RSI {rsi:.0f} (aşırı sat), vol %{rv:.0f}, {asagi_gun}/4 gün aşağı")

        elif rv > 70 or atr_pct > 4.5:
            rejim = "HIGH_VOL"
            guven = 75
            neden.append(f"Realized vol %{rv:.0f}, ATR %{atr_pct:.1f}")

        elif fiyat > sma20 * 1.01 and rsi > 52 and yukari_gun >= 3:
            rejim = "TREND_UP"
            guven = min(90, int(60 + (rsi - 52) * 1.5 + yukari_gun * 5))
            neden.append(f"Fiyat SMA20'nin %{(fiyat/sma20-1)*100:.1f} üzerinde, RSI {rsi:.0f}, {yukari_gun}/4 gün yukarı")

        elif fiyat < sma20 * 0.99 and rsi < 48 and asagi_gun >= 3:
            rejim = "TREND_DOWN"
            guven = min(90, int(60 + (48 - rsi) * 1.5 + asagi_gun * 5))
            neden.append(f"Fiyat SMA20'nin %{(1-fiyat/sma20)*100:.1f} altında, RSI {rsi:.0f}, {asagi_gun}/4 gün aşağı")

        else:
            rejim = "RANGE"
            guven = 60
            neden.append(f"Fiyat SMA20 etrafında salınıyor (±%{abs(fiyat/sma20-1)*100:.1f}), RSI {rsi:.0f}")

        # OAR uyarısı — hangi rejimde OAR daha güçlü/zayıf
        oar_uyari = {
            "TREND_UP":   "OAR long setuplara uygun. Breakout uzanmaları güçlü.",
            "TREND_DOWN": "OAR short setuplara uygun. London fakeout → short ağırlıklı.",
            "RANGE":      "OAR range içinde kalabilir. Fakeout riski yüksek, teyit bekle.",
            "HIGH_VOL":   "Yüksek volatilite — stop mesafeleri genişlet, pozisyon küçült.",
            "PANIC":      "Panik satış. Countertrend long dikkatli, short squeeze riski var.",
        }.get(rejim, "")

        return {
            "rejim":            rejim,
            "guvenis":          guven,
            "atr_pct":          round(atr_pct, 2),
            "rsi":              round(rsi, 1),
            "realized_vol_pct": round(rv, 1),
            "fiyat":            fiyat,
            "sma20":            round(sma20, 1),
            "aciklama":         "; ".join(neden),
            "oar_uyari":        oar_uyari,
            "sembol":           sembol,
        }

    except Exception as e:
        return _varsayilan_rejim(str(e))


def _varsayilan_rejim(hata: str) -> dict:
    return {
        "rejim":            "UNKNOWN",
        "guvenis":          0,
        "atr_pct":          0.0,
        "rsi":              50.0,
        "realized_vol_pct": 0.0,
        "fiyat":            0.0,
        "sma20":            0.0,
        "aciklama":         f"Rejim tespiti başarısız: {hata}",
        "oar_uyari":        "",
        "sembol":           "BTCUSDT",
    }


def rejim_ozet(r: dict) -> str:
    """Kısa okunur özet."""
    emojiler = {
        "TREND_UP": "📈", "TREND_DOWN": "📉",
        "RANGE": "↔️", "HIGH_VOL": "⚡", "PANIC": "🔥", "UNKNOWN": "❓"
    }
    e = emojiler.get(r["rejim"], "❓")
    return (f"{e} REJİM: {r['rejim']}  (güven %{r['guvenis']})\n"
            f"   ATR %{r['atr_pct']} | RSI {r['rsi']} | Vol %{r['realized_vol_pct']}\n"
            f"   {r['aciklama']}\n"
            f"   ⚑ {r['oar_uyari']}")


if __name__ == "__main__":
    import json
    result = asyncio.run(rejim_tespit("BTCUSDT"))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print()
    print(rejim_ozet(result))
