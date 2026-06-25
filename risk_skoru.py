"""
Risk Skoru — OAR Premium  (risk-on / risk-off entegrasyonu)
══════════════════════════════════════════════════════════════════════════════
Lider Agent'in "SİSTEM GELİŞTİRME" yorumunun kod karşılığı:

  Makro rejimleri (expansion/contraction), opsiyon piyasası dinamiklerini
  (gamma rejimleri) ve varlık bazlı backtest hipotezlerini TEK BİR
  risk-off/risk-on skoruna entegre eder. Bu skor yalnızca backtest
  sonuçlarını değil mevcut piyasa koşullarını da dikkate alarak hangi
  varlık sınıfında hangi stratejinin (ters / trend) daha yüksek olasılıkla
  başarılı olacağını öngören DİNAMİK bir ağırlıklandırma sunar.

Skor aralığı:  -100 (tam risk-off)  →  +100 (tam risk-on)
  skor >  +40  → güçlü risk-on  (trend/long edge yüksek)
  skor < -40  → güçlü risk-off (ters/short edge yüksek)  ← özel inceleme bölgesi
  -40..+40    → nötr / range

Bileşenler (ağırlıklar dinamik — makro olay penceresinde yeniden ölçeklenir):
  • Makro eğilim        (macro_engine.makro_veri)
  • Piyasa rejimi       (regime_engine.rejim_tespit)
  • Gamma rejimi        (options_engine.gex_ozet)
  • Backtest hipotezi   (memory / hypothesis edge — opsiyonel)
  • Makro olay penceresi(makro_takvim) → risk iştahını otomatik kısar
"""

from __future__ import annotations
import asyncio

try:
    from macro_engine import makro_veri
except Exception:
    makro_veri = None
try:
    from regime_engine import rejim_tespit
except Exception:
    rejim_tespit = None
try:
    from options_engine import gex_ozet
except Exception:
    gex_ozet = None
try:
    from makro_takvim import aktif_olay_penceresi
except Exception:
    aktif_olay_penceresi = None


# ── Temel ağırlıklar (toplam 1.0) ─────────────────────────────────────────────
TEMEL_AGIRLIK = {
    "makro":     0.30,
    "rejim":     0.30,
    "gamma":     0.20,
    "hipotez":   0.20,
}


def _makro_puan(makro: dict) -> tuple[float, str]:
    """Makro eğilimi -100..+100 puana çevirir."""
    if not makro:
        return 0.0, "makro veri yok"
    egilim = (makro.get("egilim") or "").upper()
    olumlu = makro.get("olumlu", 0)
    olumsuz = makro.get("olumsuz", 0)
    fark = olumlu - olumsuz
    if "POZİTİF" in egilim:
        puan = min(100, 40 + fark * 12)
    elif "NEGATİF" in egilim:
        puan = max(-100, -40 + fark * 12)
    else:
        puan = max(-40, min(40, fark * 15))
    return float(puan), f"makro {egilim or 'NÖTR'} ({olumlu}+/{olumsuz}-)"


def _rejim_puan(rejim: dict) -> tuple[float, str]:
    """Piyasa rejimini risk-on/off puanına çevirir."""
    if not rejim:
        return 0.0, "rejim yok"
    r = rejim.get("rejim", "UNKNOWN")
    guven = rejim.get("guvenis", 50) / 100.0
    tablo = {
        "TREND_UP":   80, "TREND_DOWN": -80,
        "RANGE":       0, "HIGH_VOL":  -45,
        "PANIC":     -90, "UNKNOWN":     0,
    }
    puan = tablo.get(r, 0) * guven
    return float(puan), f"rejim {r} (güven %{rejim.get('guvenis', 0)})"


def _gamma_puan(gex: dict) -> tuple[float, str]:
    """
    Gamma rejimi: POZİTİF gamma → dealer'lar fiyatı stabilize eder (range,
    ters/mean-reversion edge ↑). NEGATİF gamma → fiyat hareketi güçlenir
    (trend edge ↑, ama volatil → risk-off eğilimli).
    """
    if not gex or gex.get("error"):
        return 0.0, "gamma yok"
    rejim = (gex.get("gamma_rejim") or "")
    if "NEGATİF" in rejim:
        return -25.0, "negatif gamma (volatil)"
    if "POZİTİF" in rejim:
        return 10.0, "pozitif gamma (stabil)"
    return 0.0, "gamma nötr"


def _hipotez_puan(hipotez_edge: float | None) -> tuple[float, str]:
    """
    Backtest hipotez edge'i: dışarıdan verilir (ör. son aktif hipotezin
    yönlü beklentisi, -1..+1). Yoksa nötr.
    """
    if hipotez_edge is None:
        return 0.0, "hipotez edge yok"
    puan = max(-100, min(100, hipotez_edge * 100))
    return float(puan), f"hipotez edge {hipotez_edge:+.2f}"


def _strateji_onerisi(skor: float, rejim: dict | None) -> dict:
    """
    Skor + rejimden varlık/strateji ağırlıklandırması üretir.
    'edge' = hangi yönde/stratejide olasılık yüksek.
    """
    r = (rejim or {}).get("rejim", "UNKNOWN")
    if skor >= 40:
        yon, strateji = "RISK_ON", "TREND/LONG"
        not_ = "Trend takip + breakout long. Range/ters setupları zayıf."
    elif skor <= -40:
        yon, strateji = "RISK_OFF", "TERS/SHORT"
        not_ = ("Güçlü ters hareket bölgesi (skor<-40). Makro+gamma kesişimi "
                "incelenmeli — short/mean-reversion edge yüksek, trend-long riskli.")
    else:
        yon, strateji = "NÖTR", "RANGE/GRID"
        not_ = "Range piyasa — gridbot/mean-reversion mantığı, geniş trend pozisyonundan kaçın."

    # Range rejimi trend skorunu baskılar
    if r == "RANGE" and abs(skor) > 40:
        not_ += " (Rejim RANGE → trend edge güveni düşür.)"

    return {"yon": yon, "strateji": strateji, "not": not_}


async def risk_skoru_hesapla(sembol: str = "BTCUSDT",
                             hipotez_edge: float | None = None) -> dict:
    """
    Tüm bileşenleri toplayıp birleşik risk-on/risk-off skorunu döndürür.
    Bileşenlerden biri patlasa da diğerleriyle devam eder (kısmi başarı).
    """
    kok = "BTC" if sembol.upper().startswith("BTC") else \
          ("ETH" if sembol.upper().startswith("ETH") else "BTC")

    gorevler = {
        "makro": makro_veri() if makro_veri else _none(),
        "rejim": rejim_tespit(sembol) if rejim_tespit else _none(),
        "gamma": gex_ozet(kok) if gex_ozet else _none(),
    }
    sonuc = await asyncio.gather(*gorevler.values(), return_exceptions=True)
    veri = {}
    for ad, r in zip(gorevler.keys(), sonuc):
        veri[ad] = r if not isinstance(r, Exception) else None

    bilesen = {}
    bilesen["makro"]   = _makro_puan(veri["makro"])
    bilesen["rejim"]   = _rejim_puan(veri["rejim"])
    bilesen["gamma"]   = _gamma_puan(veri["gamma"])
    bilesen["hipotez"] = _hipotez_puan(hipotez_edge)

    # ── Dinamik ağırlık: makro olay penceresinde risk iştahını kıs ────────────
    agirlik = dict(TEMEL_AGIRLIK)
    olay = aktif_olay_penceresi() if aktif_olay_penceresi else None
    risk_carpani = 1.0
    olay_not = None
    if olay:
        # Makro veri açıklaması yakın → makronun ağırlığı artar, skor küçülür
        agirlik["makro"] += 0.15
        agirlik["rejim"] -= 0.10
        agirlik["gamma"] -= 0.05
        risk_carpani = olay.get("risk_carpani", 0.5)
        olay_not = olay.get("aciklama")

    toplam_a = sum(agirlik.values())
    agirlik = {k: v / toplam_a for k, v in agirlik.items()}

    skor = sum(bilesen[k][0] * agirlik[k] for k in bilesen)
    skor *= risk_carpani  # makro olay → mutlak skor küçülür (iştah düşer)
    skor = max(-100.0, min(100.0, skor))

    oneri = _strateji_onerisi(skor, veri["rejim"])

    return {
        "sembol":      sembol,
        "skor":        round(skor, 1),
        "yon":         oneri["yon"],
        "strateji":    oneri["strateji"],
        "not":         oneri["not"],
        "bilesenler":  {k: {"puan": round(v[0], 1), "aciklama": v[1]}
                        for k, v in bilesen.items()},
        "agirliklar":  {k: round(v, 3) for k, v in agirlik.items()},
        "risk_carpani": round(risk_carpani, 2),
        "makro_olay":  olay_not,
        "guclu_ters":  skor <= -40,
        "guclu_trend": skor >= 40,
    }


async def _none():
    return None


def skor_ozet(s: dict) -> str:
    """Telegram/log için okunur özet."""
    emoji = "🟢" if s["skor"] >= 40 else ("🔴" if s["skor"] <= -40 else "🟡")
    satir = [
        f"{emoji} RISK SKORU {s['sembol']}: {s['skor']:+.0f} → {s['yon']} ({s['strateji']})",
        f"   {s['not']}",
    ]
    for k, b in s["bilesenler"].items():
        satir.append(f"   • {k}: {b['puan']:+.0f}  ({b['aciklama']})")
    if s.get("makro_olay"):
        satir.append(f"   ⚠️ Makro olay penceresi: {s['makro_olay']} (risk ×{s['risk_carpani']})")
    return "\n".join(satir)


if __name__ == "__main__":
    import json
    r = asyncio.run(risk_skoru_hesapla("BTCUSDT"))
    print(json.dumps(r, ensure_ascii=False, indent=2))
    print()
    print(skor_ozet(r))
