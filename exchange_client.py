"""
Exchange Client — OAR Premium
═══════════════════════════════════════════════════════
Merkezi borsa istemcisi. Tüm modüller doğrudan httpx yerine
bu modülü kullanır.

Fallback zinciri:  Binance  →  Bybit  →  Exception
Retry:             3 deneme, exponential backoff (1s, 2s, 4s)
Desteklenen:       klines, spot fiyat, OI, funding rate, taker oran

Yeni borsa eklemek için _ADAPTERS listesine bir sözlük ekle.
"""

import asyncio
import httpx
from typing import Optional

# ─── Binance→Bybit sembol çevirisi ───────────────────────────────
# Bybit genellikle aynı sembolü kullanır; özel olanlar buraya.
_SYM_MAP = {
    "BTCUSDT": "BTCUSDT",
    "ETHUSDT": "ETHUSDT",
}
def _bybit_sym(sym: str) -> str:
    return _SYM_MAP.get(sym, sym)

# ─── Interval çevirisi ───────────────────────────────────────────
_INTERVAL_BYBIT = {
    "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720",
    "1d": "D", "1w": "W",
}

# Retry parametreleri
_MAX_RETRY = 3
_RETRY_DELAYS = [1.0, 2.0, 4.0]
_TIMEOUT = 12


async def _get(url: str, params: dict = None, headers: dict = None) -> dict | list:
    """
    Tek URL'e retry ile GET. 3 denemede başarısız olursa exception fırlatır.
    """
    for attempt, delay in enumerate(_RETRY_DELAYS, 1):
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
                r = await c.get(url, params=params, headers=headers)
                if r.status_code in (429, 418):
                    # Rate limit — bekle ve dene
                    wait = float(r.headers.get("Retry-After", delay))
                    await asyncio.sleep(min(wait, 10))
                    continue
                if r.status_code >= 400:
                    raise httpx.HTTPStatusError(
                        f"HTTP {r.status_code}", request=r.request, response=r)
                return r.json()
        except (httpx.TimeoutException, httpx.ProxyError, httpx.ConnectError) as e:
            if attempt == _MAX_RETRY:
                raise
            await asyncio.sleep(delay)
    raise RuntimeError("Tüm retry denemeleri başarısız")


# ─────────────────────────────────────────────────────────────────
# KLINES
# ─────────────────────────────────────────────────────────────────

async def klines(
    symbol: str,
    interval: str = "1h",
    limit: int = 100,
    futures: bool = True,
    start_ms: Optional[int] = None,
) -> list:
    """
    OHLCV mum verisi. Satır formatı: [ts_ms, open, high, low, close, volume, ...]
    Tüm değerler float olarak döner.

    Args:
        symbol:   "BTCUSDT"
        interval: "1m","5m","15m","1h","4h","1d" vb.
        limit:    kaç mum (max 1500)
        futures:  True=perpetual, False=spot
        start_ms: başlangıç timestamp (ms), yoksa son N mum
    """
    errors = []

    # ── Binance ──────────────────────────────────────────────────
    try:
        base = ("https://fapi.binance.com/fapi/v1/klines" if futures
                else "https://api.binance.com/api/v3/klines")
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_ms is not None:   # 0 da geçerli başlangıç (falsy-0 hatasından kaçın)
            params["startTime"] = start_ms
        data = await _get(base, params)
        if isinstance(data, list) and data:
            return [[float(x) for x in row[:6]] for row in data]
    except Exception as e:
        errors.append(f"Binance: {str(e)[:60]}")

    # ── Bybit ────────────────────────────────────────────────────
    try:
        cat = "linear" if futures else "spot"
        byb_iv = _INTERVAL_BYBIT.get(interval, interval)
        params = {
            "category": cat,
            "symbol": _bybit_sym(symbol),
            "interval": byb_iv,
            "limit": min(limit, 1000),
        }
        if start_ms is not None:
            params["start"] = start_ms
        data = await _get("https://api.bybit.com/v5/market/kline", params)
        rows = (data.get("result", {}) or {}).get("list", [])
        if rows:
            # Bybit ters sıra (yeni→eski), Binance eski→yeni
            rows = list(reversed(rows))
            # Format: [ts_ms, open, high, low, close, volume, ...]
            return [[float(r[0]), float(r[1]), float(r[2]), float(r[3]),
                     float(r[4]), float(r[5])] for r in rows]
    except Exception as e:
        errors.append(f"Bybit: {str(e)[:60]}")

    raise RuntimeError(f"klines başarısız — {'; '.join(errors)}")


# ─────────────────────────────────────────────────────────────────
# SPOT / FUTURES FİYAT
# ─────────────────────────────────────────────────────────────────

async def ticker_price(symbol: str, futures: bool = True) -> float:
    """Anlık mark/last fiyat. Her zaman float döner."""
    errors = []

    try:
        if futures:
            url = f"https://fapi.binance.com/fapi/v1/ticker/price"
        else:
            url = f"https://api.binance.com/api/v3/ticker/price"
        data = await _get(url, {"symbol": symbol})
        return float(data["price"])
    except Exception as e:
        errors.append(f"Binance: {str(e)[:60]}")

    try:
        cat = "linear" if futures else "spot"
        data = await _get("https://api.bybit.com/v5/market/tickers",
                          {"category": cat, "symbol": _bybit_sym(symbol)})
        items = (data.get("result", {}) or {}).get("list", [])
        if items:
            return float(items[0].get("lastPrice") or items[0].get("markPrice"))
    except Exception as e:
        errors.append(f"Bybit: {str(e)[:60]}")

    raise RuntimeError(f"ticker_price başarısız — {'; '.join(errors)}")


# ─────────────────────────────────────────────────────────────────
# OPEN INTEREST
# ─────────────────────────────────────────────────────────────────

async def open_interest(symbol: str, interval: str = "5m", limit: int = 50) -> list:
    """OI geçmişi. [{"timestamp": ms, "oi": float}, ...]"""
    errors = []

    try:
        data = await _get("https://fapi.binance.com/futures/data/openInterestHist", {
            "symbol": symbol, "period": interval, "limit": limit})
        if isinstance(data, list):
            return [{"timestamp": int(r["timestamp"]),
                     "oi": float(r["sumOpenInterest"])} for r in data]
    except Exception as e:
        errors.append(f"Binance OI: {str(e)[:60]}")

    try:
        data = await _get("https://api.bybit.com/v5/market/open-interest", {
            "category": "linear",
            "symbol": _bybit_sym(symbol),
            "intervalTime": interval,
            "limit": limit,
        })
        rows = (data.get("result", {}) or {}).get("list", [])
        if rows:
            return [{"timestamp": int(r["timestamp"]),
                     "oi": float(r["openInterest"])} for r in rows]
    except Exception as e:
        errors.append(f"Bybit OI: {str(e)[:60]}")

    raise RuntimeError(f"open_interest başarısız — {'; '.join(errors)}")


# ─────────────────────────────────────────────────────────────────
# FUNDING RATE
# ─────────────────────────────────────────────────────────────────

async def funding_rate(symbol: str) -> float:
    """Anlık funding rate (ondalık: 0.0001 = %0.01)."""
    errors = []

    try:
        data = await _get("https://fapi.binance.com/fapi/v1/premiumIndex",
                          {"symbol": symbol})
        return float(data["lastFundingRate"])
    except Exception as e:
        errors.append(f"Binance FR: {str(e)[:60]}")

    try:
        data = await _get("https://api.bybit.com/v5/market/tickers",
                          {"category": "linear", "symbol": _bybit_sym(symbol)})
        items = (data.get("result", {}) or {}).get("list", [])
        if items:
            return float(items[0].get("fundingRate", 0))
    except Exception as e:
        errors.append(f"Bybit FR: {str(e)[:60]}")

    raise RuntimeError(f"funding_rate başarısız — {'; '.join(errors)}")


# ─────────────────────────────────────────────────────────────────
# TAKER LONG/SHORT ORANI
# ─────────────────────────────────────────────────────────────────

async def taker_ratio(symbol: str, interval: str = "5m", limit: int = 20) -> list:
    """[{"timestamp": ms, "long_ratio": float, "short_ratio": float}, ...]"""
    errors = []

    try:
        data = await _get("https://fapi.binance.com/futures/data/takerlongshortRatio", {
            "symbol": symbol, "period": interval, "limit": limit})
        if isinstance(data, list):
            return [{"timestamp": int(r["timestamp"]),
                     "long_ratio": float(r["buySellRatio"]),
                     "short_ratio": 1 - float(r["buySellRatio"])} for r in data]
    except Exception as e:
        errors.append(f"Binance taker: {str(e)[:60]}")

    # Bybit taker ratio: ayrı endpoint yok; fallback = boş liste
    errors.append("Bybit: taker ratio endpoint yok")
    return []   # caller None yerine boş listeyle başa çıkabilir


# ─────────────────────────────────────────────────────────────────
# SAĞLIK KONTROLÜ
# ─────────────────────────────────────────────────────────────────

async def saglik_kontrol() -> dict:
    """Her iki borsanın erişilebilirliğini test et."""
    sonuc = {}
    for isim, url, params in [
        ("binance_spot", "https://api.binance.com/api/v3/ping", {}),
        ("binance_futures", "https://fapi.binance.com/fapi/v1/ping", {}),
        ("bybit", "https://api.bybit.com/v5/market/time", {}),
    ]:
        try:
            await _get(url, params)
            sonuc[isim] = "OK"
        except Exception as e:
            sonuc[isim] = f"HATA: {str(e)[:50]}"
    return sonuc


if __name__ == "__main__":
    import json
    result = asyncio.run(saglik_kontrol())
    print(json.dumps(result, ensure_ascii=False, indent=2))
