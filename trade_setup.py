"""
Trade Setup Engine — OAR Premium
═══════════════════════════════════════════════════════
Lider Agent'in "canlı grafik yorumu" için somut işlem fikri üretir:
  • Yön + işlem skoru → CIO Konfidans Motoru (7 agent)
  • Giriş / TP / SL  → OAR Fibonacci seviyeleri + opsiyon duvarları

İki zaman ölçeği:
  SCALP — en yakın destek/dirence göre dar hedef (intraday)
  SWING — call/put wall + fib uzantısına göre geniş hedef (çok günlük)

Mantık: sistemin kendi yapısal seviyeleri kullanılır (uydurma fiyat yok).
LONG'da girişin altındaki en yakın destek kümesi, üstündeki dirençler hedef olur.
"""

import asyncio
from exchange_client import klines as _klines, ticker_price as _ticker
from datetime import datetime, timezone


# ─────────────────────────────────────────────────────────────────
# SEVİYE TOPLAMA
# ─────────────────────────────────────────────────────────────────

async def _oar_fib(sembol: str) -> dict:
    """Asia Range (UTC 00-04) Fibonacci seviyeleri."""
    try:
        rows = await _klines(sembol, "15m", 100, futures=False)
        if not rows:
            return {}
        now = datetime.now(timezone.utc)
        bugun = now.date()
        asia = []
        for x in rows:
            t = datetime.fromtimestamp(x[0] / 1000, tz=timezone.utc)
            if t.date() == bugun and 0 <= t.hour < 4:
                asia.append((x[2], x[3]))  # high, low
        if len(asia) < 4:
            asia = [(x[2], x[3]) for x in rows[-32:-16]]
        if not asia:
            return {}
        hi = max(a[0] for a in asia)
        lo = min(a[1] for a in asia)
        rng = hi - lo
        return {
            "asia_high": round(hi, 1),
            "asia_low": round(lo, 1),
            "fib_0":      round(lo, 1),
            "fib_0377":   round(lo + rng * 0.377, 1),
            "fib_0618":   round(lo + rng * 0.618, 1),
            "fib_1":      round(hi, 1),
            "fib_n0272":  round(lo - rng * 0.272, 1),   # LONG ekstrem (alt sweep)
            "fib_1272":   round(hi + rng * 0.272, 1),   # SHORT ekstrem (üst sweep)
            "range_pct":  round(rng / lo * 100, 2),
        }
    except Exception:
        return {}


async def _opsiyon_duvarlar(cur: str) -> dict:
    """Call wall, put wall, zero gamma, max pain."""
    try:
        from options_engine import alarm_levels
        lv = await alarm_levels(cur)
        if lv.get("error"):
            return {}
        g = lv.get("genel", {})
        return {
            "call_wall":  g.get("call_wall"),
            "put_wall":   g.get("put_wall"),
            "zero_gamma": g.get("zero_gamma"),
            "max_pain":   g.get("max_pain"),
            "spot":       lv.get("spot"),
        }
    except Exception:
        return {}


def _seviye_kumeleri(fiyat: float, fib: dict, opt: dict) -> tuple[list, list]:
    """
    Tüm yapısal seviyeleri fiyatın altı (destek) ve üstü (direnç) olarak ayır.
    Döner: (destekler_artan, dirençler_artan) — her biri [(seviye, etiket)]
    """
    havuz = []
    for k, etiket in [
        ("asia_high", "Asia High"), ("asia_low", "Asia Low"),
        ("fib_0377", "Fib 0.377"), ("fib_0618", "Fib 0.618"),
        ("fib_n0272", "Fib -0.272 (sweep)"), ("fib_1272", "Fib 1.272 (sweep)"),
    ]:
        v = fib.get(k)
        if v:
            havuz.append((v, etiket))
    for k, etiket in [
        ("call_wall", "Call Wall"), ("put_wall", "Put Wall"),
        ("zero_gamma", "Zero Gamma"), ("max_pain", "Max Pain"),
    ]:
        v = opt.get(k)
        if v:
            havuz.append((float(v), etiket))

    destekler = sorted([(v, e) for v, e in havuz if v < fiyat], reverse=True)  # yakın→uzak
    direncler = sorted([(v, e) for v, e in havuz if v > fiyat])                # yakın→uzak
    return destekler, direncler


# ─────────────────────────────────────────────────────────────────
# SETUP ÜRETİMİ
# ─────────────────────────────────────────────────────────────────

def _setup_uret(yon: str, fiyat: float, destekler: list, direncler: list) -> dict:
    """
    OAR fib + duvar seviyelerine göre scalp ve swing giriş/TP/SL üretir.
    """
    if yon not in ("LONG", "SHORT"):
        return {}

    def _r(giris, sl, tp):
        risk = abs(giris - sl)
        odul = abs(tp - giris)
        return round(odul / risk, 2) if risk > 0 else None

    if yon == "LONG":
        # Giriş: yakın destek (pullback). SL: girişin altında küçük yapısal tampon.
        d1 = destekler[0][0] if len(destekler) >= 1 else fiyat * 0.995
        d2 = destekler[1][0] if len(destekler) >= 2 else d1 * 0.99
        r1 = direncler[0][0] if len(direncler) >= 1 else fiyat * 1.005
        r2 = direncler[1][0] if len(direncler) >= 2 else r1 * 1.01
        # Swing hedefi: en uzak yapısal direnç (genelde Call Wall / fib 1.272)
        r_uzak     = direncler[-1][0] if direncler else r2 * 1.02
        r_uzak_et  = direncler[-1][1] if direncler else "üst direnç"

        scalp_sl = min(d1 * 0.997, d2)   # girişin %0.3 altı ya da 2. destek (yakın olan)
        scalp = {
            "giris": round(d1, 1),
            "sl":    round(scalp_sl, 1),
            "tp":    round(r1, 1),
            "rr":    _r(d1, scalp_sl, r1),
            "giris_etiket": destekler[0][1] if destekler else "yakın destek",
            "tp_etiket":    direncler[0][1] if direncler else "yakın direnç",
        }
        swing_sl = d2 * 0.997   # 2. desteğin altı = yapı bozulursa çık
        swing = {
            "giris": round(d1, 1),
            "sl":    round(swing_sl, 1),
            "tp":    round(r2, 1),
            "tp2":   round(r_uzak, 1),
            "rr":    _r(d1, swing_sl, r_uzak),
            "giris_etiket": destekler[0][1] if destekler else "yakın destek",
            "tp_etiket":    r_uzak_et,
        }
    else:  # SHORT
        r1 = direncler[0][0] if len(direncler) >= 1 else fiyat * 1.005
        r2 = direncler[1][0] if len(direncler) >= 2 else r1 * 1.01
        d1 = destekler[0][0] if len(destekler) >= 1 else fiyat * 0.995
        d2 = destekler[1][0] if len(destekler) >= 2 else d1 * 0.99
        d_uzak    = destekler[-1][0] if destekler else d2 * 0.98
        d_uzak_et = destekler[-1][1] if destekler else "alt destek"

        scalp_sl = max(r1 * 1.003, r2)
        scalp = {
            "giris": round(r1, 1),
            "sl":    round(scalp_sl, 1),
            "tp":    round(d1, 1),
            "rr":    _r(r1, scalp_sl, d1),
            "giris_etiket": direncler[0][1] if direncler else "yakın direnç",
            "tp_etiket":    destekler[0][1] if destekler else "yakın destek",
        }
        swing_sl = r2 * 1.003
        swing = {
            "giris": round(r1, 1),
            "sl":    round(swing_sl, 1),
            "tp":    round(d2, 1),
            "tp2":   round(d_uzak, 1),
            "rr":    _r(r1, swing_sl, d_uzak),
            "giris_etiket": direncler[0][1] if direncler else "yakın direnç",
            "tp_etiket":    d_uzak_et,
        }

    return {"scalp": scalp, "swing": swing}


async def trade_fikri(sembol: str = "BTCUSDT", karar: dict = None) -> dict:
    """
    Bir sembol için tam işlem fikri:
      yön + işlem skoru (CIO) + scalp/swing giriş-TP-SL (fib + duvar).

    karar verilirse tekrar hesaplanmaz (toplu çağrılarda performans).
    """
    cur = "BTC" if "BTC" in sembol else "ETH" if "ETH" in sembol else sembol.replace("USDT", "")

    # CIO kararı (yön + skor)
    if karar is None:
        try:
            from confidence_engine import confidence_karar
            karar = await confidence_karar(sembol)
        except Exception as e:
            karar = {"karar": "NO_TRADE", "konfidans": 0, "karar_nedeni": str(e)[:80]}

    fib, opt, fiyat = await asyncio.gather(
        _oar_fib(sembol),
        _opsiyon_duvarlar(cur),
        _ticker(sembol, futures=False),
        return_exceptions=True,
    )
    if isinstance(fib, Exception): fib = {}
    if isinstance(opt, Exception): opt = {}
    if isinstance(fiyat, Exception) or not fiyat:
        fiyat = opt.get("spot") or fib.get("asia_high") or 0

    destekler, direncler = _seviye_kumeleri(fiyat, fib, opt) if fiyat else ([], [])

    yon = karar.get("karar", "NO_TRADE")
    setuplar = _setup_uret(yon, fiyat, destekler, direncler) if yon in ("LONG", "SHORT") else {}

    return {
        "sembol": sembol,
        "fiyat": round(fiyat, 1) if fiyat else None,
        "yon": yon,
        "islem_skoru": karar.get("konfidans", 0),
        "conviction": karar.get("conviction", "LOW"),
        "rejim": (karar.get("rejim", {}) or {}).get("rejim", "—"),
        "karar_nedeni": karar.get("karar_nedeni", ""),
        "setuplar": setuplar,
        "seviyeler": {
            "fib": fib,
            "opsiyon": opt,
            "destekler": [{"seviye": v, "etiket": e} for v, e in destekler[:4]],
            "direncler": [{"seviye": v, "etiket": e} for v, e in direncler[:4]],
        },
        "tarih": datetime.now(timezone.utc).isoformat(),
    }


async def coklu_trade_fikri(semboller: list = None) -> dict:
    """BTC + ETH için paralel işlem fikri."""
    semboller = semboller or ["BTCUSDT", "ETHUSDT"]
    sonuclar = await asyncio.gather(*[trade_fikri(s) for s in semboller],
                                    return_exceptions=True)
    cikti = {}
    for s, r in zip(semboller, sonuclar):
        cikti[s] = r if not isinstance(r, Exception) else {"hata": str(r)[:80]}
    return cikti


if __name__ == "__main__":
    import json
    r = asyncio.run(trade_fikri("BTCUSDT"))
    print(json.dumps(r, ensure_ascii=False, indent=2))
