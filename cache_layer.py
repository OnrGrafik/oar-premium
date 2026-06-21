"""
cache_layer.py — OAR Premium Hız Katmanı
Render Starter (0.5 CPU) için tüm ağır API çağrılarını önbelleğe alır.
TTL dolmadıkça disk'ten döner, API'ye gitmez.
"""
import os, json, time, asyncio, hashlib
from pathlib import Path
from datetime import datetime, timezone
from typing import Callable, Any

DATA_DIR  = Path(os.environ.get("DATA_DIR", "data"))
CACHE_DIR = DATA_DIR / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# TTL saniye cinsinden
TTL = {
    "ticker":        30,      # BTC fiyatı — 30sn
    "oar_fib":       300,     # Asia range — 5dk
    "piyasa_durumu": 300,     # Piyasa durumu — 5dk
    "opsiyon_yorum": 900,     # Opsiyon — 15dk
    "makro":         3600,    # Makro — 1 saat
    "grafik_yorum":  600,     # Grafik yorum — 10dk
    "leader_report": 600,     # Leader raporu — 10dk
    "opt_score":     300,     # OAR Score — 5dk
    "kiyotaka":      900,     # Kiyotaka VPFR/TPO — 15dk
    "signals":       60,      # Sinyaller — 1dk
    "deribit_chain": 600,     # Deribit chain — 10dk
    "default":       300,
}

def _key_path(key: str) -> Path:
    h = hashlib.md5(key.encode()).hexdigest()[:12]
    return CACHE_DIR / f"{h}.json"

def cache_get(key: str, ttl_category: str = "default") -> Any:
    """Cache'den oku. TTL geçmişse None döner."""
    path = _key_path(key)
    try:
        if not path.exists(): return None
        raw = json.loads(path.read_text())
        age = time.time() - raw.get("ts", 0)
        ttl = TTL.get(ttl_category, TTL["default"])
        if age > ttl: return None
        return raw.get("data")
    except Exception:
        return None

def cache_set(key: str, data: Any) -> None:
    """Cache'e yaz."""
    try:
        _key_path(key).write_text(
            json.dumps({"ts": time.time(), "data": data}, ensure_ascii=False)
        )
    except Exception:
        pass

def cache_del(key: str) -> None:
    try: _key_path(key).unlink(missing_ok=True)
    except Exception: pass

def cache_clear_all() -> int:
    """Tüm cache sil. Dönüş: silinen dosya sayısı."""
    n = 0
    for f in CACHE_DIR.glob("*.json"):
        try: f.unlink(); n += 1
        except Exception: pass
    return n

def cache_stats() -> dict:
    """Cache istatistikleri."""
    files = list(CACHE_DIR.glob("*.json"))
    total_kb = sum(f.stat().st_size for f in files) / 1024
    valid = 0
    for f in files:
        try:
            raw = json.loads(f.read_text())
            age = time.time() - raw.get("ts", 0)
            if age < TTL["default"]: valid += 1
        except Exception:
            pass
    return {
        "toplam_dosya":  len(files),
        "gecerli":       valid,
        "disk_kb":       round(total_kb, 1),
        "cache_dir":     str(CACHE_DIR),
    }

# ── Dekoratör: async fonksiyon sonuçlarını otomatik cache'le ──────────────────
def cached(category: str, key_fn: Callable = None):
    """
    Kullanım:
    @cached("makro")
    async def hesapla_makro(): ...
    """
    def decorator(fn):
        async def wrapper(*args, **kwargs):
            key = key_fn(*args, **kwargs) if key_fn else f"{fn.__name__}:{args}:{kwargs}"
            hit = cache_get(key, category)
            if hit is not None: return hit
            result = await fn(*args, **kwargs)
            if result is not None: cache_set(key, result)
            return result
        wrapper.__name__ = fn.__name__
        return wrapper
    return decorator

# ── Startup Isıtma ────────────────────────────────────────────────────────────
async def startup_warm(app_state: dict = None):
    """
    Sunucu açılırken kritik verileri önceden yükle.
    main.py startup'ında çağrılır.
    """
    import httpx
    tasks = []

    # BTC fiyatı
    async def _btc():
        try:
            async with httpx.AsyncClient(timeout=5) as cl:
                r = await cl.get("https://fapi.binance.com/fapi/v1/ticker/price",
                                  params={"symbol": "BTCUSDT"})
                if r.status_code == 200:
                    cache_set("ticker:BTCUSDT", r.json())
        except Exception: pass

    # Asia range (bugün)
    async def _asia():
        try:
            from datetime import datetime, timezone
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            cache_key = f"oar_fib:{today}"
            # Zaten varsa atla
            if cache_get(cache_key, "oar_fib"): return
        except Exception: pass

    tasks = [_btc(), _asia()]
    await asyncio.gather(*tasks, return_exceptions=True)
    print("[Cache] Startup ısıtma tamamlandı")
