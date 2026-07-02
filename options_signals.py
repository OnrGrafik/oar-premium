"""
options_signals.py — Opsiyon-türevli bileşik piyasa sinyalleri (Path A)
═══════════════════════════════════════════════════════════════════════════════
Kullanıcı kuralları (bilime/piyasa yapısına dayalı, hardcoded eşik yok — trend
karşılaştırmaları gerçek seriye göre):

  1) dvol_skew_bearish (DÜŞÜŞ):
       DVOL percentile YÜKSEK (≥%60) + DVOL yükseliyor + skew (25Δ RR) yükseliyor
       → piyasa aşağı korunması satın alıyor → bearish.

  2) vol_sinyali (VOLATİLİTE GELİYOR):
       BTC spot-vol korelasyonu düşüyor + IV vade yapısı slope'u yükseliyor
       (contango dikleşiyor) → volatilite genişlemesi.

Veri kaynakları:
  • DVOL geçmişi  → Deribit get_volatility_index_data (2021+, ÜCRETSİZ, backtest'e uygun)
  • Skew (25Δ RR) + ATM vade yapısı → options_engine.iv_skew (CANLI)
  • Spot günlük   → Binance klines (korelasyon için)
  • Skew/slope TREND'i → kendi günlük log'umuz (opsiyon_gunluk.json) — birkaç gün
    birikince aktifleşir; yoksa ilgili bileşik None döner (uydurma yapılmaz).

Her çağrı ham metrikleri günlüğe yazar → zamanla FORWARD backtest veri seti oluşur.
"""
import os, json, time, math, statistics
from pathlib import Path

import httpx

DERIBIT = "https://www.deribit.com/api/v2/public"
HDR = {"Accept": "application/json", "User-Agent": "OAR-Premium/2.0"}

DATA_DIR = Path(os.environ.get("DATA_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
                or ("/var/data" if Path("/var/data").exists() else "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = DATA_DIR / "opsiyon_gunluk.json"

PCT_ESIK = 60.0          # DVOL percentile "yüksek" eşiği (dağılım referanslı)
TREND_GUN = 5            # kaç gün öncesine göre "yükseliyor/düşüyor"
KOREL_PENCERE = 30       # spot-vol korelasyonu penceresi (gün)


# ─── Yardımcılar ─────────────────────────────────────────────────────────────
def _percentile_rank(seri, x):
    """x'in seri içindeki yüzdelik sırası (0-100)."""
    if not seri:
        return None
    n = sum(1 for v in seri if v <= x)
    return round(100.0 * n / len(seri), 1)


def _korelasyon(a, b):
    """Pearson korelasyonu; yetersiz/eşit varyans → None."""
    if len(a) != len(b) or len(a) < 3:
        return None
    try:
        return statistics.correlation(a, b)
    except Exception:
        return None


async def _drb(cl, method, params=None):
    qs = "&".join(f"{k}={v}" for k, v in (params or {}).items())
    url = f"{DERIBIT}/{method}" + (f"?{qs}" if qs else "")
    try:
        r = await cl.get(url, headers=HDR)
        if r.status_code != 200:
            return None
        return r.json().get("result")
    except Exception:
        return None


async def dvol_gecmis(cl, currency="BTC", gun=120):
    """
    DVOL günlük kapanış serisi (Deribit get_volatility_index_data).
    2021+ ücretsiz. Döner: [(ts_ms, close), ...] eskiden yeniye.
    """
    now = int(time.time() * 1000)
    bas = now - gun * 86400 * 1000
    res = await _drb(cl, "get_volatility_index_data", {
        "currency": currency, "start_timestamp": bas,
        "end_timestamp": now, "resolution": "43200",   # 12s → gün başına ~2 nokta
    })
    if not res or "data" not in res:
        return []
    # data: [[ts, open, high, low, close], ...]
    seri = [(int(r[0]), float(r[4])) for r in res["data"] if r and r[4]]
    # Gün başına son kapanış (12s → günlük indir)
    gunluk = {}
    for ts, c in seri:
        gunluk[ts // (86400 * 1000)] = (ts, c)
    return [gunluk[k] for k in sorted(gunluk)]


async def _spot_gunluk(cl, gun=120):
    """BTC günlük kapanış (Binance) — spot-vol korelasyonu için. Döner: {gun_idx: close}."""
    try:
        r = await cl.get("https://api.binance.com/api/v3/klines",
                         params={"symbol": "BTCUSDT", "interval": "1d", "limit": min(gun, 1000)},
                         headers=HDR)
        if r.status_code != 200:
            return {}
        return {int(k[0]) // (86400 * 1000): float(k[4]) for k in r.json()}
    except Exception:
        return {}


# ─── Günlük log (forward dataset) ────────────────────────────────────────────
def _log_yukle():
    try:
        if LOG_FILE.exists():
            return json.loads(LOG_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def _log_kaydet(kayit):
    """Bugünün metriğini günlüğe ekle (gün başına tek kayıt; üzerine yaz)."""
    log = _log_yukle()
    bugun = kayit["tarih"]
    log = [x for x in log if x.get("tarih") != bugun]
    log.append(kayit)
    log = log[-400:]   # ~1 yıl tut
    try:
        LOG_FILE.write_text(json.dumps(log, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return log


def _log_serisi(log, alan):
    """Log'tan bir alanın None-olmayan zaman serisi."""
    return [x[alan] for x in log if x.get(alan) is not None]


# ─── Ana hesap ───────────────────────────────────────────────────────────────
async def opsiyon_metrikleri(currency="BTC"):
    """
    Ham metrikleri hesapla + günlüğe yaz + iki bileşik sinyali türet.
    Trend gerektiren bileşenler yeterli veri yoksa None (uydurma yapılmaz).
    """
    from options_engine import iv_skew
    async with httpx.AsyncClient(timeout=30) as cl:
        dvol_seri = await dvol_gecmis(cl, currency, 120)
        spot_map = await _spot_gunluk(cl, 120)
    skew = await iv_skew(currency)

    if not dvol_seri:
        return {"hata": "DVOL geçmişi alınamadı", "tarih": _bugun()}

    dvol_closes = [c for _, c in dvol_seri]
    dvol_simdi = dvol_closes[-1]
    dvol_pct = _percentile_rank(dvol_closes, dvol_simdi)
    dvol_onceki = dvol_closes[-1 - TREND_GUN] if len(dvol_closes) > TREND_GUN else None
    dvol_yukseliyor = (dvol_simdi > dvol_onceki) if dvol_onceki is not None else None

    # Skew (ön-vade 25Δ RR) ve ATM vade slope (arka − ön)
    rr_seri = (skew or {}).get("risk_reversal", [])
    atm_seri = (skew or {}).get("atm_vade", [])
    skew_rr = rr_seri[0]["rr"] if rr_seri else None                  # en yakın vade RR
    ts_slope = (atm_seri[-1]["iv"] - atm_seri[0]["iv"]) if len(atm_seri) >= 2 else None

    # Spot-vol korelasyonu: son KOREL_PENCERE gün spot getirisi vs DVOL değişimi
    spot_vol_corr = None
    if spot_map and len(dvol_seri) >= KOREL_PENCERE + 1:
        gunler = sorted(spot_map)[-(KOREL_PENCERE + 1):]
        spot_ret = [spot_map[gunler[i]] / spot_map[gunler[i - 1]] - 1
                    for i in range(1, len(gunler))]
        dv = dvol_closes[-(KOREL_PENCERE + 1):]
        dvol_chg = [dv[i] - dv[i - 1] for i in range(1, len(dv))]
        m = min(len(spot_ret), len(dvol_chg))
        spot_vol_corr = _korelasyon(spot_ret[-m:], dvol_chg[-m:])

    kayit = {
        "tarih": _bugun(), "dvol": round(dvol_simdi, 2), "dvol_pct": dvol_pct,
        "skew_rr": skew_rr, "ts_slope": ts_slope,
        "spot_vol_corr": round(spot_vol_corr, 3) if spot_vol_corr is not None else None,
    }
    log = _log_kaydet(kayit)

    # ── Bileşik 1: dvol_skew_bearish ──
    skew_gecmis = _log_serisi(log[:-1], "skew_rr")      # bugün hariç
    skew_yukseliyor = None
    if skew_rr is not None and len(skew_gecmis) >= 3:
        skew_yukseliyor = skew_rr > statistics.mean(skew_gecmis[-TREND_GUN:])
    dvol_skew_bearish = None
    if dvol_pct is not None and dvol_yukseliyor is not None and skew_yukseliyor is not None:
        dvol_skew_bearish = bool(dvol_pct >= PCT_ESIK and dvol_yukseliyor and skew_yukseliyor)

    # ── Bileşik 2: vol_sinyali ──
    slope_gecmis = _log_serisi(log[:-1], "ts_slope")
    corr_gecmis = _log_serisi(log[:-1], "spot_vol_corr")
    slope_yukseliyor = None
    if ts_slope is not None and len(slope_gecmis) >= 3:
        slope_yukseliyor = ts_slope > statistics.mean(slope_gecmis[-TREND_GUN:])
    corr_dusuyor = None
    if spot_vol_corr is not None and len(corr_gecmis) >= 3:
        corr_dusuyor = spot_vol_corr < statistics.mean(corr_gecmis[-TREND_GUN:])
    vol_sinyali = None
    if slope_yukseliyor is not None and corr_dusuyor is not None:
        vol_sinyali = bool(slope_yukseliyor and corr_dusuyor)

    return {
        "tarih": _bugun(),
        "dvol": round(dvol_simdi, 2), "dvol_pct": dvol_pct,
        "dvol_yukseliyor": dvol_yukseliyor,
        "skew_rr": skew_rr, "skew_yukseliyor": skew_yukseliyor,
        "ts_slope": round(ts_slope, 2) if ts_slope is not None else None,
        "slope_yukseliyor": slope_yukseliyor,
        "spot_vol_corr": kayit["spot_vol_corr"], "corr_dusuyor": corr_dusuyor,
        "dvol_skew_bearish": dvol_skew_bearish,
        "vol_sinyali": vol_sinyali,
        "log_gun": len(log),
    }


def _bugun():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


if __name__ == "__main__":
    import asyncio
    print(json.dumps(asyncio.run(opsiyon_metrikleri("BTC")), ensure_ascii=False, indent=2))
