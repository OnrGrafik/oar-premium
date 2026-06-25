"""
Makro Takvim — OAR Premium
══════════════════════════════════════════════════════════════════════════════
Research Agent — Bilimsel Bulgular ÖNERİ'sinin kod karşılığı:

  "OAR'a, makro verilerin (enflasyon, faiz kararları) açıklanacağı gün ve
   saatlerde sistemin risk iştahını otomatik olarak düşüren veya belirli
   hipotezleri askıya alan bir modül eklenmesi önerilir. Ayrıca 'move_source'
   bilgisinin belirlenmesi, rejim analizinin derinleşmesi için kritiktir."

Bu modül:
  1) Yüksek etkili makro açıklamaların (CPI, FOMC, NFP, PCE, PPI) pencere
     saatlerini bilir; pencere içindeyse risk_carpani < 1 döndürür
     (risk_skoru bunu kullanarak mutlak skoru/iştahı kısar).
  2) askiya_alinan_hipotezler() → bu pencerede devre dışı bırakılacak
     hipotez etiketlerini verir.
  3) move_source_belirle() → son fiyat hareketinin kaynağını (makro / opsiyon
     / likidite / teknik) sınıflandırır; rejim analizini derinleştirir.

Saatler ABD Doğu Saati (ET) referanslı yaygın açıklama saatleridir:
  • CPI / PPI / NFP / Retail / PCE : 08:30 ET
  • FOMC faiz kararı + basın       : 14:00–15:00 ET
Pencere = açıklamadan ÖNCE 60 dk + SONRA 90 dk (volatilite kuyruğu).
"""

from __future__ import annotations
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

# Haftanın günü bağımsız, "tipik" yüksek etkili açıklama saatleri (ET).
# Gerçek tarih API'si yoksa bile gün-içi pencereyi yakalamak için saat bazlı.
MAKRO_PENCERELER = [
    # (etiket, saat ET, dakika, önce_dk, sonra_dk, risk_carpani, etki)
    ("CPI/PPI/NFP/PCE (08:30 ET veri bloğu)", 8, 30, 60, 90, 0.4, "YÜKSEK"),
    ("FOMC faiz kararı (14:00 ET)",          14,  0, 45, 60, 0.3, "ÇOK YÜKSEK"),
    ("FOMC basın toplantısı (14:30 ET)",     14, 30, 15, 75, 0.3, "ÇOK YÜKSEK"),
]

# Bu pencerelerde askıya alınacak hipotez/strateji etiketleri.
PENCEREDE_ASKIYA = ["ters", "mean_reversion", "gridbot", "range_fade", "countertrend"]


def _et_now() -> datetime:
    return datetime.now(ET)


def aktif_olay_penceresi(now: datetime | None = None) -> dict | None:
    """
    Şu an yüksek etkili bir makro açıklama penceresinde miyiz?
    Döner: {etiket, aciklama, risk_carpani, etki, dakika_kala} | None
    Hafta sonu pencere yok (ABD verisi açıklanmaz).
    """
    n = (now or _et_now()).astimezone(ET)
    if n.weekday() >= 5:   # Cmt/Pazar
        return None
    for etiket, sa, dk, once, sonra, carpan, etki in MAKRO_PENCERELER:
        hedef = n.replace(hour=sa, minute=dk, second=0, microsecond=0)
        bas = hedef - timedelta(minutes=once)
        bit = hedef + timedelta(minutes=sonra)
        if bas <= n <= bit:
            kala = int((hedef - n).total_seconds() // 60)
            durum = (f"{abs(kala)} dk {'sonra' if kala < 0 else 'kala'}")
            return {
                "etiket":       etiket,
                "aciklama":     f"{etiket} — {durum}",
                "risk_carpani": carpan,
                "etki":         etki,
                "dakika_kala":  kala,
            }
    return None


def askiya_alinan_hipotezler(now: datetime | None = None) -> list[str]:
    """Aktif makro penceresinde askıya alınacak hipotez etiketleri."""
    if aktif_olay_penceresi(now):
        return list(PENCEREDE_ASKIYA)
    return []


def hipotez_askida_mi(hipotez_etiketi: str, now: datetime | None = None) -> bool:
    """Verilen hipotez/strateji etiketi şu an askıda mı?"""
    if not hipotez_etiketi:
        return False
    et = hipotez_etiketi.lower()
    return any(a in et for a in askiya_alinan_hipotezler(now))


# ── move_source: hareketin kaynağını sınıflandır ──────────────────────────────
def move_source_belirle(*, fiyat_chg_pct: float = 0.0,
                        oi_chg_pct: float = 0.0,
                        funding: float = 0.0,
                        gamma_rejim: str = "",
                        cvd_yon: str = "",
                        makro_penceresi: bool | None = None) -> dict:
    """
    Son fiyat hareketinin baskın kaynağını sınıflandırır.

    Döner: {"source": MACRO|OPTIONS|LIQUIDATION|SPOT_FLOW|TECHNICAL,
            "guven": 0-100, "aciklama": str}
    """
    if makro_penceresi is None:
        makro_penceresi = aktif_olay_penceresi() is not None

    adaylar = []  # (source, guven, aciklama)

    if makro_penceresi and abs(fiyat_chg_pct) >= 0.4:
        adaylar.append(("MACRO", 80,
                        "Hareket makro açıklama penceresinde — kaynak makro."))

    if "NEGATİF" in (gamma_rejim or "") and abs(fiyat_chg_pct) >= 0.6:
        adaylar.append(("OPTIONS", 70,
                        "Negatif gamma + sert hareket — dealer hedging baskısı."))

    # OI düşerken büyük fiyat hareketi → likidasyon (pozisyon kapanması)
    if oi_chg_pct <= -1.5 and abs(fiyat_chg_pct) >= 0.8:
        adaylar.append(("LIQUIDATION", 65,
                        "OI düşüşü + sert hareket — zincirleme likidasyon."))

    # OI artarken yön + CVD aynı → yeni spot/futures akışı
    if oi_chg_pct >= 1.5 and cvd_yon and abs(fiyat_chg_pct) >= 0.5:
        adaylar.append(("SPOT_FLOW", 60,
                        f"OI artışı + CVD {cvd_yon} — yeni yönlü akış."))

    if abs(funding) >= 0.05:
        adaylar.append(("LIQUIDATION", 50,
                        f"Aşırı funding (%{funding:.3f}) — squeeze riski."))

    if not adaylar:
        return {"source": "TECHNICAL", "guven": 40,
                "aciklama": "Belirgin makro/opsiyon/akış tetiği yok — teknik hareket."}

    adaylar.sort(key=lambda x: x[1], reverse=True)
    src, guven, acik = adaylar[0]
    return {"source": src, "guven": guven, "aciklama": acik,
            "alternatifler": [a[0] for a in adaylar[1:]]}


if __name__ == "__main__":
    import json
    print("Aktif pencere:", json.dumps(aktif_olay_penceresi(), ensure_ascii=False))
    print("Askıdaki hipotezler:", askiya_alinan_hipotezler())
    print("move_source:", json.dumps(
        move_source_belirle(fiyat_chg_pct=1.2, oi_chg_pct=-2.0,
                            gamma_rejim="NEGATİF GAMMA"), ensure_ascii=False))
