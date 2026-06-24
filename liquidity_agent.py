"""
Liquidity Agent — OAR Premium
══════════════════════════════════════════════════════════════════
Likidite havuzlarını, tuzak hareketlerini ve stop kümelerini tespit eder.

Tespit edilen yapılar:
  - Equal High (EQH) / Equal Low (EQL)       — stop avı potansiyeli
  - SFP (Swing Failure Pattern)               — sahte kırılım / fakeout
  - Stop Cluster                              — yoğun stop bölgesi tahmini
  - Liquidity Sweep                          — zaten gerçekleşmiş sweep
"""

import asyncio
from exchange_client import klines as _klines


# ─── Equal High / Low ─────────────────────────────────────────────

def _equal_seviyeler(candles: list, tolerans_pct: float = 0.05) -> dict:
    """
    Birbirine çok yakın (tolerans içinde) swing high / low çiftleri.
    Bu seviyeler stop avı için potansiyel havuz.
    tolerans_pct: 0.05 = %0.05 fark toleransı
    """
    highs = [(i, c[2]) for i, c in enumerate(candles)]
    lows = [(i, c[3]) for i, c in enumerate(candles)]
    fiyat = candles[-1][4]
    tol = fiyat * tolerans_pct / 100

    eqh_list, eql_list = [], []

    # Son 30 mumu tara
    pencere = candles[-30:]
    h_vals = [(i, c[2]) for i, c in enumerate(pencere)]
    l_vals = [(i, c[3]) for i, c in enumerate(pencere)]

    for i in range(len(h_vals)):
        for j in range(i + 2, len(h_vals)):
            if abs(h_vals[i][1] - h_vals[j][1]) <= tol:
                seviye = (h_vals[i][1] + h_vals[j][1]) / 2
                eqh_list.append({
                    "seviye": round(seviye, 2),
                    "indeks1": h_vals[i][0],
                    "indeks2": h_vals[j][0],
                    "fark_pct": round(abs(h_vals[i][1] - h_vals[j][1]) / seviye * 100, 4),
                })

    for i in range(len(l_vals)):
        for j in range(i + 2, len(l_vals)):
            if abs(l_vals[i][1] - l_vals[j][1]) <= tol:
                seviye = (l_vals[i][1] + l_vals[j][1]) / 2
                eql_list.append({
                    "seviye": round(seviye, 2),
                    "indeks1": l_vals[i][0],
                    "indeks2": l_vals[j][0],
                    "fark_pct": round(abs(l_vals[i][1] - l_vals[j][1]) / seviye * 100, 4),
                })

    # En güçlüleri al (en az fark = en eşit seviye)
    eqh_list.sort(key=lambda x: x["fark_pct"])
    eql_list.sort(key=lambda x: x["fark_pct"])
    return {"EQH": eqh_list[:3], "EQL": eql_list[:3]}


# ─── SFP (Swing Failure Pattern) ─────────────────────────────────

def _sfp_tara(candles: list, pencere: int = 5) -> list:
    """
    SFP tespiti:
    - Mum, önceki swing high'ı geçer ama kapanış altında kalır → BEARISH SFP
    - Mum, önceki swing low'u geçer ama kapanış üstünde kalır → BULLISH SFP

    pencere: SFP geçerliliği için son N mum içinde olmalı
    """
    sfp_listesi = []
    if len(candles) < 10:
        return sfp_listesi

    for i in range(5, len(candles)):
        mum = candles[i]
        h, l, o, cl = mum[2], mum[3], mum[1], mum[4]

        # Önceki N mumun en yüksek high / en düşük low
        onceki = candles[max(0, i - pencere):i]
        prev_high = max(c[2] for c in onceki)
        prev_low = min(c[3] for c in onceki)

        # Bearish SFP: high önceki high'ı geçti ama kapanış altında
        if h > prev_high and cl < prev_high:
            sfp_listesi.append({
                "tip": "BEARISH_SFP",
                "mum_indeks": i,
                "ts": int(mum[0]),
                "sfp_seviye": round(prev_high, 2),
                "mum_high": round(h, 2),
                "kapanis": round(cl, 2),
                "wick_uzunluk": round(h - cl, 2),
            })

        # Bullish SFP: low önceki low'u geçti ama kapanış üstünde
        elif l < prev_low and cl > prev_low:
            sfp_listesi.append({
                "tip": "BULLISH_SFP",
                "mum_indeks": i,
                "ts": int(mum[0]),
                "sfp_seviye": round(prev_low, 2),
                "mum_low": round(l, 2),
                "kapanis": round(cl, 2),
                "wick_uzunluk": round(cl - l, 2),
            })

    # Son 5 SFP'yi döndür
    return sfp_listesi[-5:]


def _son_sfp(sfp_listesi: list, son_mum_indeks: int, max_yas: int = 10) -> dict | None:
    """Son geçerli SFP'yi döndür (çok eski değilse)."""
    gecerli = [s for s in sfp_listesi if son_mum_indeks - s["mum_indeks"] <= max_yas]
    return gecerli[-1] if gecerli else None


# ─── Stop Cluster Tahmini ─────────────────────────────────────────

def _stop_cluster(candles: list, atr: float) -> list:
    """
    Stop kümesi tahmini: güçlü swing high/low seviyelerinin hemen üstü/altı.
    Gerçek emir defteri olmadan tahmindir.
    """
    if not candles or atr == 0:
        return []

    cluster_list = []
    son = candles[-20:]

    # Her önemli pivot için stop bölgesi
    yuksekler = sorted([c[2] for c in son], reverse=True)[:5]
    dusukler = sorted([c[3] for c in son])[:5]

    for h in yuksekler:
        cluster_list.append({
            "tip": "STOP_ABOVE",
            "seviye": round(h + atr * 0.1, 2),
            "guc": "YUKSEK" if yuksekler.index(h) < 2 else "ORTA",
        })
    for l in dusukler:
        cluster_list.append({
            "tip": "STOP_BELOW",
            "seviye": round(l - atr * 0.1, 2),
            "guc": "YUKSEK" if dusukler.index(l) < 2 else "ORTA",
        })

    return cluster_list


# ─── Liquidity Sweep Tespiti ──────────────────────────────────────

def _sweep_tespit(candles: list, eq_seviyeleri: dict) -> list:
    """
    Zaten gerçekleşmiş sweep: fiyat EQH/EQL'yi geçip geri döndü mü?
    """
    sweeplar = []
    fiyat = candles[-1][4]

    for eq in eq_seviyeleri.get("EQH", []):
        s = eq["seviye"]
        # Son 5 mumda high bu seviyeyi geçti mi ama fiyat şu an altında mı?
        son_high = max(c[2] for c in candles[-5:])
        if son_high > s and fiyat < s:
            sweeplar.append({"tip": "BEARISH_SWEEP", "seviye": s, "guc": "YUKARI_SWEEP"})

    for eq in eq_seviyeleri.get("EQL", []):
        s = eq["seviye"]
        son_low = min(c[3] for c in candles[-5:])
        if son_low < s and fiyat > s:
            sweeplar.append({"tip": "BULLISH_SWEEP", "seviye": s, "guc": "ASAGI_SWEEP"})

    return sweeplar


# ─── ATR yardımcısı ───────────────────────────────────────────────

def _atr(candles: list, period: int = 14) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i][2], candles[i][3], candles[i - 1][4]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period


# ─── Ana Analiz ───────────────────────────────────────────────────

async def liquidity_analiz(
    sembol: str = "BTCUSDT",
    timeframe: str = "15m",
    limit: int = 100,
) -> dict:
    """
    Likidite yapısını analiz eder.

    Returns:
        {
            "eqh":           list — Equal High seviyeleri
            "eql":           list — Equal Low seviyeleri
            "sfp_listesi":   list — Tüm tespit edilen SFP'ler
            "son_sfp":       dict|None — En güncel SFP
            "stop_cluster":  list — Stop kümesi tahminleri
            "sweep":         list — Gerçekleşmiş sweep'ler
            "ozet":          str
            "sembol":        str
            "timeframe":     str
        }
    """
    try:
        data = await _klines(sembol, timeframe, limit, futures=True)
        if not data or len(data) < 20:
            return _varsayilan(sembol, timeframe)

        candles = [[float(x) for x in row[:6]] for row in data]
        atr = _atr(candles)

        eq = _equal_seviyeler(candles)
        sfp_listesi = _sfp_tara(candles)
        son_sfp = _son_sfp(sfp_listesi, len(candles) - 1)
        stops = _stop_cluster(candles, atr)
        sweeplar = _sweep_tespit(candles, eq)

        # Özet
        ozet_parcalar = []
        if eq["EQH"]:
            ozet_parcalar.append(f"EQH: {eq['EQH'][0]['seviye']}")
        if eq["EQL"]:
            ozet_parcalar.append(f"EQL: {eq['EQL'][0]['seviye']}")
        if son_sfp:
            ozet_parcalar.append(f"SFP: {son_sfp['tip']} @ {son_sfp.get('sfp_seviye', '?')}")
        if sweeplar:
            ozet_parcalar.append(f"Sweep: {sweeplar[0]['tip']}")
        if not ozet_parcalar:
            ozet_parcalar.append("Belirgin likidite yapısı yok")

        return {
            "eqh":          eq["EQH"],
            "eql":          eq["EQL"],
            "sfp_listesi":  sfp_listesi,
            "son_sfp":      son_sfp,
            "stop_cluster": stops,
            "sweep":        sweeplar,
            "ozet":         " | ".join(ozet_parcalar),
            "sembol":       sembol,
            "timeframe":    timeframe,
        }

    except Exception as e:
        return _varsayilan(sembol, timeframe, str(e))


def _varsayilan(sembol: str, timeframe: str, hata: str = "") -> dict:
    return {
        "eqh": [], "eql": [],
        "sfp_listesi": [], "son_sfp": None,
        "stop_cluster": [], "sweep": [],
        "ozet": f"Likidite analizi başarısız: {hata}",
        "sembol": sembol, "timeframe": timeframe,
    }


if __name__ == "__main__":
    import json
    r = asyncio.run(liquidity_analiz("BTCUSDT", "15m"))
    print(json.dumps(r, ensure_ascii=False, indent=2))
