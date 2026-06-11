"""
Live Agent - Bot Sinyal Takibi & Backtest
GitHub/Render/Vercel'deki botların sinyal endpoint'lerini izler,
sinyalleri kaydeder, geçmişe dönük başarı oranı hesaplar.
"""

import json
import httpx
import asyncio
from pathlib import Path
from datetime import datetime, timezone, timedelta
from brain import get_ohlcv

import os as _os_dd
DATA_DIR = Path(_os_dd.environ.get("DATA_DIR", "data"))
DATA_DIR.mkdir(exist_ok=True)
SOURCES_FILE = DATA_DIR / "bot_sources.json"
SIGLOG_FILE  = DATA_DIR / "bot_signals_log.json"

def _load(path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default

def _save(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

# ─── Kaynak Yönetimi ─────────────────────────────────────────────────────────
def list_sources() -> list:
    return _load(SOURCES_FILE, [])

def add_source(name: str, url: str, kind: str = "json") -> dict:
    """url: botunun sinyal verdiği herhangi bir adres
       (Vercel API, Render endpoint, GitHub raw JSON...)"""
    sources = list_sources()
    sources.append({
        "id":   max([s.get("id", 0) for s in sources], default=0) + 1,
        "name": name, "url": url, "kind": kind,
        "added_at": datetime.now(timezone.utc).isoformat(),
        "last_status": "bekliyor",
    })
    _save(SOURCES_FILE, sources)
    return {"status": "ok", "count": len(sources)}

def delete_source(source_id: int) -> dict:
    sources = [s for s in list_sources() if s.get("id") != source_id]
    _save(SOURCES_FILE, sources)
    return {"status": "ok"}

# ─── Sinyal Çekme ────────────────────────────────────────────────────────────
def _extract_signals(data, source_name: str) -> list:
    """Esnek parser: çeşitli JSON formatlarından sinyal çıkar.
    Render bot (UTBot/STC), Vercel API, GitHub raw JSON, OAR botları desteklenir."""
    signals = []
    items = []

    # OAR /webhook formatı: {"coin":"BTCUSDT","signal":"LONG","price":...}
    if isinstance(data, dict) and "signal" in data and "coin" in data:
        items = [{"signal": data["signal"], "symbol": data["coin"],
                  "price": data.get("price", 0)}]
    # Render bot /health formatı: {"position":{"side":"Buy",...},...}
    elif isinstance(data, dict) and "position" in data and isinstance(data["position"], dict):
        pos = data["position"]
        side = pos.get("side", "")
        sym  = data.get("symbol", "ETHUSDT")
        if side:
            items = [{"signal": "LONG" if side=="Buy" else "SHORT",
                      "symbol": sym, "health": True}]
    # Vercel alarm-levels formatı: {"seviyeler":{"genel":{"call_wall":...}}}
    elif isinstance(data, dict) and "seviyeler" in data:
        # Bu formattan sinyal çıkarılmaz, bilgi amaçlı
        return []
    elif isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        for key in ["signals", "data", "results", "items", "alerts",
                    "trades", "positions", "orders"]:
            if isinstance(data.get(key), list):
                items = data[key]; break
        else:
            items = [data]

    for it in items[:50]:
        if not isinstance(it, dict):
            continue
        # Yön bul
        direction = ""
        for k in ["signal", "direction", "side", "action", "type", "trend",
                  "yon", "yön", "tip", "pozisyon"]:
            v = str(it.get(k, "")).upper()
            if any(x in v for x in ["LONG", "BUY", "AL", "BULL", "UP", "ALIM"]):
                direction = "LONG"; break
            if any(x in v for x in ["SHORT", "SELL", "SAT", "BEAR", "DOWN", "SATIM"]):
                direction = "SHORT"; break
        if not direction:
            continue
        # Sembol bul
        symbol = ""
        for k in ["symbol", "pair", "coin", "asset", "ticker",
                  "instrument", "sembol"]:
            if it.get(k):
                symbol = str(it[k]).upper().replace("/","").replace("-","")
                break
        if not symbol:
            symbol = "BTCUSDT"
        if not symbol.endswith("USDT"):
            symbol += "USDT"
        signals.append({
            "bot":       source_name,
            "symbol":    symbol,
            "direction": direction,
            "raw":       {k: it[k] for k in list(it)[:8]},
        })
    return signals

async def fetch_source_signals(source: dict) -> list:
    """Tek kaynaktan sinyalleri çek — Render/Vercel/GitHub destekli"""
    url = source["url"]
    try:
        headers = {"User-Agent": "OAR-Premium/1.0", "Accept": "application/json"}
        # Render ücretsiz plan uyandırma için 2 deneme
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(
                    timeout=30 if attempt == 0 else 15,
                    follow_redirects=True
                ) as cl:
                    r = await cl.get(url, headers=headers)
                    if r.status_code == 200:
                        break
                    if r.status_code in (502, 503, 504) and attempt == 0:
                        await asyncio.sleep(5); continue
                    source["last_status"] = f"HTTP {r.status_code}"
                    return []
            except Exception as conn_e:
                if attempt == 0:
                    await asyncio.sleep(5); continue
                source["last_status"] = f"Bağlantı hatası: {str(conn_e)[:40]}"
                return []
        try:
            data = r.json()
        except Exception:
            # GitHub raw → düz metin JSON olabilir
            try:
                import json as _json
                data = _json.loads(r.text)
            except Exception:
                source["last_status"] = "JSON değil"
                return []
        source["last_status"] = "ok"
        return _extract_signals(data, source["name"])
    except Exception as e:
        source["last_status"] = str(e)[:60]
        return []

async def poll_all_sources() -> list:
    """Tüm kaynakları paralel tara, yeni sinyalleri logla"""
    sources = list_sources()
    if not sources:
        return []
    results = await asyncio.gather(*[fetch_source_signals(s) for s in sources])
    _save(SOURCES_FILE, sources)  # last_status güncellendi

    log = _load(SIGLOG_FILE, {"signals": [], "next_id": 1})
    seen = {(s["bot"], s["symbol"], s["direction"],
             s.get("logged_at", "")[:13]) for s in log["signals"]}
    now = datetime.now(timezone.utc)
    all_current = []
    for sig_list in results:
        for sig in sig_list:
            all_current.append(sig)
            key = (sig["bot"], sig["symbol"], sig["direction"], now.isoformat()[:13])
            if key in seen:
                continue
            # Giriş fiyatı al
            candles = await get_ohlcv(sig["symbol"], "1h", 2)
            entry = candles[-1]["close"] if candles else 0
            log["signals"].append({
                "id":        log["next_id"],
                "bot":       sig["bot"],
                "symbol":    sig["symbol"],
                "direction": sig["direction"],
                "entry_price": entry,
                "logged_at": now.isoformat(),
                "evaluated": False,
                "outcome":   None,
            })
            log["next_id"] += 1
            seen.add(key)
    log["signals"] = log["signals"][-5000:]
    _save(SIGLOG_FILE, log)
    return all_current

# ─── Backtest / Değerlendirme ────────────────────────────────────────────────
async def evaluate_signals(hours: int = 24, threshold_pct: float = 1.0) -> dict:
    """Yaşı dolmuş sinyalleri değerlendir: LONG sonrası fiyat threshold% arttıysa WIN"""
    log = _load(SIGLOG_FILE, {"signals": [], "next_id": 1})
    now = datetime.now(timezone.utc)
    evaluated = 0
    price_cache = {}
    for sig in log["signals"]:
        if sig.get("evaluated") or not sig.get("entry_price"):
            continue
        t = datetime.fromisoformat(sig["logged_at"])
        if now - t < timedelta(hours=hours):
            continue
        sym = sig["symbol"]
        if sym not in price_cache:
            candles = await get_ohlcv(sym, "1h", 2)
            price_cache[sym] = candles[-1]["close"] if candles else 0
        cur = price_cache[sym]
        if not cur:
            continue
        change = (cur - sig["entry_price"]) / sig["entry_price"] * 100
        win = (sig["direction"] == "LONG" and change >= threshold_pct) or \
              (sig["direction"] == "SHORT" and change <= -threshold_pct)
        sig["evaluated"] = True
        sig["outcome"] = "WIN" if win else "LOSS"
        sig["change_pct"] = round(change, 2)
        evaluated += 1
    _save(SIGLOG_FILE, log)
    return {"evaluated": evaluated}

def get_bot_stats() -> dict:
    """Bot bazında başarı istatistikleri (haftalık + genel)"""
    log = _load(SIGLOG_FILE, {"signals": []})
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)
    stats = {}
    for sig in log["signals"]:
        bot = sig["bot"]
        if bot not in stats:
            stats[bot] = {"total": 0, "evaluated": 0, "wins": 0,
                          "week_total": 0, "week_wins": 0, "week_evaluated": 0,
                          "last_signal": None}
        s = stats[bot]
        s["total"] += 1
        s["last_signal"] = sig["logged_at"]
        t = datetime.fromisoformat(sig["logged_at"])
        is_week = t >= week_ago
        if is_week:
            s["week_total"] += 1
        if sig.get("evaluated"):
            s["evaluated"] += 1
            if is_week:
                s["week_evaluated"] += 1
            if sig.get("outcome") == "WIN":
                s["wins"] += 1
                if is_week:
                    s["week_wins"] += 1
    for bot, s in stats.items():
        s["win_rate"] = round(s["wins"] / s["evaluated"] * 100, 1) if s["evaluated"] else None
        s["week_win_rate"] = round(s["week_wins"] / s["week_evaluated"] * 100, 1) if s["week_evaluated"] else None
    return stats

def get_recent_signals(n: int = 40) -> list:
    log = _load(SIGLOG_FILE, {"signals": []})
    return list(reversed(log["signals"][-n:]))
