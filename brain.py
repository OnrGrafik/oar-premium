"""
Crypto AI Agent - Otonom Beyin Modülü
Backtest, öğrenme, sinyal üretimi, strateji geliştirme
"""

import json
import asyncio
import httpx
from pathlib import Path
from datetime import datetime, timezone
from collections import deque

# ─── Veri Deposu (JSON dosyaları, DB gerekmez) ───────────────────────────────
DATA_DIR = Path(__import__("os").environ.get("DATA_DIR") or ("/var/data" if Path("/var/data").exists() else "data"))
DATA_DIR.mkdir(exist_ok=True)

SIGNALS_FILE  = DATA_DIR / "signals.json"
MEMORY_FILE   = DATA_DIR / "memory.json"
BACKTEST_FILE = DATA_DIR / "backtest_results.json"
ALERTS_FILE   = DATA_DIR / "alerts.json"

def load_json(path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default

def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

# ─── Binance OHLCV Verisi ────────────────────────────────────────────────────
async def get_ohlcv(symbol: str = "BTCUSDT", interval: str = "1h", limit: int = 200) -> list:
    """Mum verisi çek — backtest ve indikatörler için"""
    async with httpx.AsyncClient(timeout=15) as c:
        try:
            r = await c.get(
                "https://api.binance.com/api/v3/klines",
                params={"symbol": symbol, "interval": interval, "limit": limit}
            )
            if r.status_code == 200:
                candles = []
                for k in r.json():
                    candles.append({
                        "ts":     k[0],
                        "open":   float(k[1]),
                        "high":   float(k[2]),
                        "low":    float(k[3]),
                        "close":  float(k[4]),
                        "volume": float(k[5]),
                    })
                return candles
        except Exception:
            pass
    return []

# ─── Teknik İndikatörler ─────────────────────────────────────────────────────
def calc_rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = closes[-i] - closes[-i-1]
        (gains if diff > 0 else losses).append(abs(diff))
    avg_gain = sum(gains) / period if gains else 0.0001
    avg_loss = sum(losses) / period if losses else 0.0001
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)

def calc_ema(closes: list, period: int) -> float:
    if len(closes) < period:
        return closes[-1] if closes else 0
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = price * k + ema * (1 - k)
    return round(ema, 4)

def calc_macd(closes: list):
    if len(closes) < 26:
        return 0, 0, 0
    ema12 = calc_ema(closes, 12)
    ema26 = calc_ema(closes, 26)
    macd_line = round(ema12 - ema26, 4)
    # Signal: 9-period EMA of MACD
    macd_history = []
    for i in range(9, 0, -1):
        e12 = calc_ema(closes[:-i] if i > 0 else closes, 12)
        e26 = calc_ema(closes[:-i] if i > 0 else closes, 26)
        macd_history.append(e12 - e26)
    signal = calc_ema(macd_history, 9)
    histogram = round(macd_line - signal, 4)
    return macd_line, round(signal, 4), histogram

def calc_bollinger(closes: list, period: int = 20):
    if len(closes) < period:
        return closes[-1], closes[-1]*1.02, closes[-1]*0.98
    recent = closes[-period:]
    mid = sum(recent) / period
    std = (sum((x - mid)**2 for x in recent) / period) ** 0.5
    return round(mid, 4), round(mid + 2*std, 4), round(mid - 2*std, 4)

def calc_atr(candles: list, period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0
    trs = []
    for i in range(1, period + 1):
        c = candles[-i]
        p = candles[-i-1]
        tr = max(c["high"] - c["low"],
                 abs(c["high"] - p["close"]),
                 abs(c["low"]  - p["close"]))
        trs.append(tr)
    return round(sum(trs) / period, 4)

def detect_trend(closes: list) -> str:
    if len(closes) < 50:
        return "belirsiz"
    ema20 = calc_ema(closes, 20)
    ema50 = calc_ema(closes, 50)
    price = closes[-1]
    if price > ema20 > ema50:
        return "güçlü_yükseliş"
    elif price > ema50:
        return "yükseliş"
    elif price < ema20 < ema50:
        return "güçlü_düşüş"
    elif price < ema50:
        return "düşüş"
    return "yatay"

def find_support_resistance(candles: list, lookback: int = 50) -> dict:
    if len(candles) < lookback:
        return {}
    recent = candles[-lookback:]
    highs  = sorted([c["high"] for c in recent], reverse=True)
    lows   = sorted([c["low"]  for c in recent])
    price  = recent[-1]["close"]
    # En yakın destek ve direnç
    resistances = [h for h in highs[:5] if h > price]
    supports    = [l for l in lows[:5]  if l < price]
    return {
        "resistance1": round(resistances[0], 2) if resistances else None,
        "resistance2": round(resistances[1], 2) if len(resistances) > 1 else None,
        "support1":    round(supports[0], 2)    if supports    else None,
        "support2":    round(supports[1], 2)    if len(supports) > 1 else None,
    }

# ─── Sinyal Motoru ───────────────────────────────────────────────────────────
def generate_signal(symbol: str, candles: list, deribit_data: dict = None) -> dict:
    """Tüm indikatörleri birleştirerek sinyal üret"""
    if not candles or len(candles) < 30:
        return {}

    closes  = [c["close"] for c in candles]
    price   = closes[-1]
    rsi     = calc_rsi(closes)
    macd_l, macd_s, macd_h = calc_macd(closes)
    bb_mid, bb_up, bb_low  = calc_bollinger(closes)
    atr     = calc_atr(candles)
    trend   = detect_trend(closes)
    sr      = find_support_resistance(candles)
    ema20   = calc_ema(closes, 20)
    ema50   = calc_ema(closes, 50)

    # Skor sistemi (-10 ile +10 arası)
    score = 0
    reasons = []

    # RSI
    if rsi < 30:
        score += 3; reasons.append(f"RSI aşırı satım ({rsi})")
    elif rsi < 45:
        score += 1; reasons.append(f"RSI düşük ({rsi})")
    elif rsi > 70:
        score -= 3; reasons.append(f"RSI aşırı alım ({rsi})")
    elif rsi > 55:
        score -= 1; reasons.append(f"RSI yüksek ({rsi})")

    # MACD
    if macd_h > 0 and macd_l > macd_s:
        score += 2; reasons.append("MACD pozitif kesişim")
    elif macd_h < 0 and macd_l < macd_s:
        score -= 2; reasons.append("MACD negatif kesişim")

    # Bollinger
    if price < bb_low:
        score += 2; reasons.append("Alt Bollinger bandının altında")
    elif price > bb_up:
        score -= 2; reasons.append("Üst Bollinger bandının üzerinde")

    # EMA trend
    if "güçlü_yükseliş" in trend:
        score += 2; reasons.append("Güçlü yükseliş trendi")
    elif "yükseliş" in trend:
        score += 1; reasons.append("Yükseliş trendi")
    elif "güçlü_düşüş" in trend:
        score -= 2; reasons.append("Güçlü düşüş trendi")
    elif "düşüş" in trend:
        score -= 1; reasons.append("Düşüş trendi")

    # Deribit opsiyon flow (varsa)
    if deribit_data and deribit_data.get("summary"):
        pcr = deribit_data["summary"].get("pcr_oi", 1)
        if pcr > 1.3:
            score -= 1; reasons.append(f"Deribit PCR yüksek ({pcr}) - ayı baskısı")
        elif pcr < 0.7:
            score += 1; reasons.append(f"Deribit PCR düşük ({pcr}) - boğa baskısı")

    # Karar
    if score >= 4:
        signal_type = "GÜÇLÜ AL"
        color = "green"
    elif score >= 2:
        signal_type = "AL"
        color = "lightgreen"
    elif score <= -4:
        signal_type = "GÜÇLÜ SAT"
        color = "red"
    elif score <= -2:
        signal_type = "SAT"
        color = "salmon"
    else:
        signal_type = "BEKLE"
        color = "yellow"

    # Stop loss / Take profit (ATR bazlı)
    sl = round(price - atr * 2, 2) if atr else None
    tp = round(price + atr * 3, 2) if atr else None

    return {
        "symbol":      symbol,
        "price":       round(price, 4),
        "signal":      signal_type,
        "score":       score,
        "color":       color,
        "rsi":         rsi,
        "macd":        macd_l,
        "macd_hist":   macd_h,
        "trend":       trend,
        "bb_upper":    bb_up,
        "bb_lower":    bb_low,
        "ema20":       round(ema20, 2),
        "ema50":       round(ema50, 2),
        "atr":         atr,
        "stop_loss":   sl,
        "take_profit": tp,
        "support1":    sr.get("support1"),
        "resistance1": sr.get("resistance1"),
        "reasons":     reasons,
        "timestamp":   datetime.now(timezone.utc).isoformat(),
    }

# ─── Basit Backtest Motoru ───────────────────────────────────────────────────
def run_backtest(candles: list, strategy: str = "rsi_macd") -> dict:
    """Geçmiş veriyle strateji test et"""
    if len(candles) < 60:
        return {"error": "Yeterli veri yok (min 60 mum)"}

    trades = []
    position = None  # {"entry": price, "sl": price, "tp": price, "idx": i}
    initial_capital = 10000
    capital = initial_capital

    for i in range(50, len(candles) - 1):
        window   = candles[:i+1]
        closes   = [c["close"] for c in window]
        price    = closes[-1]
        rsi      = calc_rsi(closes)
        _, _, mh = calc_macd(closes)
        atr      = calc_atr(window)

        if position is None:
            # Giriş koşulu
            if strategy == "rsi_macd":
                if rsi < 35 and mh > 0:
                    sl = price - atr * 2
                    tp = price + atr * 3
                    position = {"entry": price, "sl": sl, "tp": tp, "idx": i}
            elif strategy == "bollinger":
                _, _, bb_low = calc_bollinger(closes)
                if price < bb_low and rsi < 40:
                    sl = price - atr * 1.5
                    tp = price + atr * 2.5
                    position = {"entry": price, "sl": sl, "tp": tp, "idx": i}
        else:
            # Çıkış kontrolü
            if price >= position["tp"]:
                pnl_pct = (position["tp"] - position["entry"]) / position["entry"] * 100
                capital *= (1 + pnl_pct/100)
                trades.append({"result": "WIN", "pnl_pct": round(pnl_pct, 2),
                               "entry": position["entry"], "exit": price})
                position = None
            elif price <= position["sl"]:
                pnl_pct = (position["sl"] - position["entry"]) / position["entry"] * 100
                capital *= (1 + pnl_pct/100)
                trades.append({"result": "LOSS", "pnl_pct": round(pnl_pct, 2),
                               "entry": position["entry"], "exit": price})
                position = None

    if not trades:
        return {"error": "Bu periyotta sinyal oluşmadı"}

    wins      = [t for t in trades if t["result"] == "WIN"]
    losses    = [t for t in trades if t["result"] == "LOSS"]
    win_rate  = round(len(wins) / len(trades) * 100, 1)
    total_pnl = round(capital - initial_capital, 2)
    avg_win   = round(sum(t["pnl_pct"] for t in wins)   / len(wins),   2) if wins   else 0
    avg_loss  = round(sum(t["pnl_pct"] for t in losses) / len(losses), 2) if losses else 0

    # Sharpe (basit)
    pnls    = [t["pnl_pct"] for t in trades]
    avg_pnl = sum(pnls) / len(pnls)
    std_pnl = (sum((p - avg_pnl)**2 for p in pnls) / len(pnls))**0.5 if len(pnls) > 1 else 1
    sharpe  = round(avg_pnl / std_pnl, 2) if std_pnl else 0

    return {
        "strategy":       strategy,
        "total_trades":   len(trades),
        "win_rate_pct":   win_rate,
        "total_pnl_usd":  total_pnl,
        "total_return_pct": round((capital/initial_capital - 1)*100, 2),
        "avg_win_pct":    avg_win,
        "avg_loss_pct":   avg_loss,
        "sharpe_ratio":   sharpe,
        "best_trade":     max(pnls),
        "worst_trade":    min(pnls),
        "trades":         trades[-10:],  # Son 10 işlem
    }

# ─── Hafıza / Öğrenme ────────────────────────────────────────────────────────
def save_signal_to_memory(signal: dict):
    """Sinyali kaydet, sonra doğruluğunu takip et"""
    memory = load_json(MEMORY_FILE, {"signals": [], "stats": {}})
    memory["signals"].append({
        **signal,
        "verified": False,
        "outcome": None
    })
    # Son 500 sinyal tut
    memory["signals"] = memory["signals"][-500:]
    save_json(MEMORY_FILE, memory)

def update_signal_outcome(symbol: str, entry_price: float, current_price: float):
    """Eski sinyalin sonucunu güncelle — öğrenme için"""
    memory = load_json(MEMORY_FILE, {"signals": [], "stats": {}})
    for sig in reversed(memory["signals"]):
        if sig.get("symbol") == symbol and not sig.get("verified"):
            change = (current_price - entry_price) / entry_price * 100
            sig["verified"] = True
            sig["outcome"]  = "correct" if (
                (sig["signal"] in ["AL","GÜÇLÜ AL"] and change > 2) or
                (sig["signal"] in ["SAT","GÜÇLÜ SAT"] and change < -2)
            ) else "incorrect"
            sig["price_change_pct"] = round(change, 2)
            break
    save_json(MEMORY_FILE, memory)

def get_accuracy_stats() -> dict:
    """Geçmiş sinyal doğruluk istatistikleri"""
    memory = load_json(MEMORY_FILE, {"signals": [], "stats": {}})
    verified = [s for s in memory["signals"] if s.get("verified")]
    if not verified:
        return {"total": 0, "accuracy": 0}
    correct  = sum(1 for s in verified if s.get("outcome") == "correct")
    by_type  = {}
    for s in verified:
        t = s.get("signal", "?")
        if t not in by_type:
            by_type[t] = {"total": 0, "correct": 0}
        by_type[t]["total"] += 1
        if s.get("outcome") == "correct":
            by_type[t]["correct"] += 1
    return {
        "total":    len(verified),
        "correct":  correct,
        "accuracy": round(correct / len(verified) * 100, 1),
        "by_type":  by_type,
    }

# ─── Alarm Sistemi ───────────────────────────────────────────────────────────
def check_alerts(signal: dict) -> list:
    """Kayıtlı alarmları kontrol et"""
    alerts     = load_json(ALERTS_FILE, [])
    triggered  = []
    price      = signal.get("price", 0)
    symbol     = signal.get("symbol", "")

    for a in alerts:
        if a.get("symbol") != symbol or a.get("triggered"):
            continue
        condition = a.get("condition")
        target    = a.get("target_price", 0)
        if condition == "above" and price >= target:
            triggered.append(a); a["triggered"] = True
        elif condition == "below" and price <= target:
            triggered.append(a); a["triggered"] = True
        elif condition == "signal" and a.get("target_signal") == signal.get("signal"):
            triggered.append(a); a["triggered"] = True

    if triggered:
        save_json(ALERTS_FILE, alerts)
    return triggered

def add_alert(symbol: str, condition: str, target_price: float = None, target_signal: str = None):
    alerts = load_json(ALERTS_FILE, [])
    alerts.append({
        "symbol":        symbol,
        "condition":     condition,
        "target_price":  target_price,
        "target_signal": target_signal,
        "triggered":     False,
        "created_at":    datetime.now(timezone.utc).isoformat(),
    })
    save_json(ALERTS_FILE, alerts)
    return {"status": "ok", "alert_count": len(alerts)}

# ─── Ana Tarama Motoru ───────────────────────────────────────────────────────
WATCHLIST = [
    ("BTCUSDT",  "BTC"),
    ("ETHUSDT",  "ETH"),
    ("SOLUSDT",  "SOL"),
    ("BNBUSDT",  "BNB"),
    ("XRPUSDT",  "XRP"),
    ("ADAUSDT",  "ADA"),
    ("DOGEUSDT", "DOGE"),
    ("AVAXUSDT", "AVAX"),
    ("LINKUSDT", "LINK"),
    ("DOTUSDT",  "DOT"),
]

async def scan_market(timeframe: str = "1h") -> list:
    """Tüm watchlist'i tara, sinyal üret"""
    results = []
    for symbol, name in WATCHLIST:
        candles = await get_ohlcv(symbol, timeframe, 200)
        if not candles:
            continue
        signal = generate_signal(symbol, candles)
        if signal:
            signal["name"] = name
            save_signal_to_memory(signal)
            triggered = check_alerts(signal)
            signal["alerts_triggered"] = triggered
            results.append(signal)
        await asyncio.sleep(0.2)  # Rate limit
    # Skora göre sırala
    results.sort(key=lambda x: abs(x.get("score", 0)), reverse=True)
    save_json(SIGNALS_FILE, results)
    return results

async def quick_backtest(symbol: str = "BTCUSDT", timeframe: str = "4h") -> dict:
    """Hızlı backtest — birden fazla strateji karşılaştır"""
    candles = await get_ohlcv(symbol, timeframe, 500)
    if not candles:
        return {"error": "Veri alınamadı"}
    results = {}
    for strategy in ["rsi_macd", "bollinger"]:
        results[strategy] = run_backtest(candles, strategy)
    # En iyi stratejiyi seç
    best = max(results.items(),
               key=lambda x: x[1].get("total_return_pct", -999)
               if "error" not in x[1] else -999)
    save_json(BACKTEST_FILE, {"symbol": symbol, "timeframe": timeframe,
                               "results": results, "best": best[0],
                               "timestamp": datetime.now(timezone.utc).isoformat()})
    return {"symbol": symbol, "timeframe": timeframe,
            "results": results, "best_strategy": best[0]}
