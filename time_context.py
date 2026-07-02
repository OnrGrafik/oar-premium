"""
Time Context Agent — OAR Premium
════════════════════════════════════════════════════════════════════
Piyasa zamanı bağlamını analiz eder ve Risk Skoru (0-100) üretir.

Takip edilen olaylar:
  FOMC/FED toplantıları
  Triple Witching (her quarter'ın 3. Cuması)
  BTC/ETH Options Expiry (her ayın son Cuması — Deribit)
  Hafta sonu / US tatilleri
  Quarter/Ay kapanışları
"""

import os
from datetime import datetime, timezone, date, timedelta
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or ("/var/data" if Path("/var/data").exists() else "data"))

# Bilinen FOMC toplantı tarihleri (2025-2026)
FOMC_TARIHLERI = {
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-11-05", "2025-12-17",
    "2026-01-28", "2026-03-18", "2026-05-06", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-11-04", "2026-12-16",
}

US_TATILLERI = {
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
}


def _son_cuma(yil: int, ay: int) -> date:
    """Verilen ay/yılın son Cuma günü."""
    if ay == 12:
        son = date(yil + 1, 1, 1) - timedelta(days=1)
    else:
        son = date(yil, ay + 1, 1) - timedelta(days=1)
    while son.weekday() != 4:
        son -= timedelta(days=1)
    return son


def _ucuncu_cuma(yil: int, ay: int) -> date:
    """Verilen ay/yılın 3. Cuma günü."""
    d = date(yil, ay, 1)
    cumalar = 0
    while True:
        if d.weekday() == 4:
            cumalar += 1
            if cumalar == 3:
                return d
        d += timedelta(days=1)


def _etkinlikleri_hesapla(bugun: date) -> list:
    """Bugün ve ±2 gün içindeki kritik piyasa etkinlikleri."""
    etkinlikler = []
    for i in range(-1, 4):
        kontrol = bugun + timedelta(days=i)
        tarih_str = kontrol.isoformat()
        yil, ay = kontrol.year, kontrol.month

        if tarih_str in FOMC_TARIHLERI:
            etkinlikler.append({
                "tip": "FOMC", "tarih": tarih_str, "gun_farki": i,
                "risk_puan": 22, "aciklama": f"FOMC Toplantısı ({tarih_str})"
            })

        if ay in (3, 6, 9, 12) and kontrol == _ucuncu_cuma(yil, ay):
            etkinlikler.append({
                "tip": "TRIPLE_WITCHING", "tarih": tarih_str, "gun_farki": i,
                "risk_puan": 28, "aciklama": f"Triple Witching ({tarih_str})"
            })

        if kontrol == _son_cuma(yil, ay):
            etkinlikler.append({
                "tip": "BTC_OPTIONS_EXPIRY", "tarih": tarih_str, "gun_farki": i,
                "risk_puan": 18, "aciklama": f"Deribit BTC/ETH Options Expiry ({tarih_str})"
            })

        if tarih_str in US_TATILLERI:
            etkinlikler.append({
                "tip": "US_TATIL", "tarih": tarih_str, "gun_farki": i,
                "risk_puan": 12, "aciklama": f"ABD Tatil — Düşük Likidite ({tarih_str})"
            })

        if ay in (3, 6, 9, 12) and kontrol.day >= 28:
            etkinlikler.append({
                "tip": "QUARTER_SONU", "tarih": tarih_str, "gun_farki": i,
                "risk_puan": 15, "aciklama": f"Quarter Kapanışı ({tarih_str})"
            })
        elif kontrol.day >= 28 or kontrol.day <= 2:
            etkinlikler.append({
                "tip": "AY_KAPANISI", "tarih": tarih_str, "gun_farki": i,
                "risk_puan": 8, "aciklama": f"Ay Sonu/Başı — Kurumsal rebalancing"
            })

    return etkinlikler


async def time_risk_skoru() -> dict:
    """
    Bugünkü piyasa etkinliklerine göre Risk Skoru (0-100) üret.
    Yüksek skor = riskli gün, işlemi azalt veya NO_TRADE tercih et.
    """
    bugun = datetime.now(timezone.utc).date()
    etkinlikler = _etkinlikleri_hesapla(bugun)

    toplam_risk = 0.0
    aktif = []

    for e in etkinlikler:
        gf = e["gun_farki"]
        if gf == 0:    katsayi = 1.0
        elif gf == 1:  katsayi = 0.7
        elif gf == -1: katsayi = 0.5
        else:          katsayi = 0.15
        toplam_risk += e["risk_puan"] * katsayi
        if katsayi >= 0.5:
            aktif.append(e)

    # Hafta sonu / Cuma kapanış riski
    hgunu = bugun.weekday()
    if hgunu == 4:
        toplam_risk += 12
        aktif.append({"tip": "CUMA", "aciklama": "Cuma kapanışı — weekend gap riski", "risk_puan": 12})
    elif hgunu >= 5:
        toplam_risk += 18
        aktif.append({"tip": "HAFTA_SONU", "aciklama": "Hafta sonu — düşük likidite", "risk_puan": 18})

    risk_skoru = round(min(100.0, toplam_risk), 1)
    seviye = ("KRİTİK" if risk_skoru > 65 else
              "YÜKSEK" if risk_skoru > 40 else
              "ORTA"   if risk_skoru > 20 else "DÜŞÜK")

    ozet_parcalar = [f"Zaman Risk Seviyesi: {seviye} ({risk_skoru}/100)"]
    for e in aktif:
        ozet_parcalar.append(f"  ⚠ {e['aciklama']}")

    return {
        "risk_skoru": risk_skoru,
        "seviye": seviye,
        "bugun": bugun.isoformat(),
        "aktif_etkinlikler": aktif,
        "ozet": "\n".join(ozet_parcalar)
    }
