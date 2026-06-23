"""
OAR Session Agent — OAR Premium
════════════════════════════════════════════════════════════════════
Asia Range, London, Silver Bullet ve NY seans analizini yapar.
OAR metodolojisinin kalbini oluşturur.

Seans saatleri (UTC):
  Asia         : 00:00 – 04:00
  London       : 07:00 – 10:00
  Silver Bullet: 10:00 – 11:00  (ICT konsepti)
  NY           : 13:30 – 16:30

Üretilen sorular:
  Asia buy zone mu? Sell zone mu?
  London breakout mu? Fakeout mu?
  Silver Bullet aktif mi?
  NY continuation mu? Reversal mi?
  Liquidity sweep oluştu mu?
  SFP (Swing Failure Pattern) var mı?
  Value acceptance/rejection?
"""

import asyncio
from datetime import datetime, timezone, timedelta
from exchange_client import klines as _ec_klines

SEANS_SAATLERI = {
    "asia":          (0, 4),
    "london":        (7, 10),
    "silver_bullet": (10, 11),
    "ny":            (13, 17),
}


def _aktif_seans(saat: int) -> str:
    for seans, (bas, bit) in SEANS_SAATLERI.items():
        if bas <= saat < bit:
            return seans
    return "off_session"


async def _ohlcv_al(sembol: str, interval: str = "15m", limit: int = 112) -> list:
    """exchange_client üzerinden OHLCV. [open, high, low, close, vol]"""
    try:
        rows = await _ec_klines(sembol, interval, limit, futures=False)
        return [[r[1], r[2], r[3], r[4], r[5]] for r in rows]
    except Exception:
        return []


def _sfp_tespit(mumlar: list, seviye: float, yon: str, esik_pct: float = 0.002) -> bool:
    """
    Swing Failure Pattern: son 3 mumda seviyeyi geçip kapanış döndü mü?
    yon="UP" → high > seviye ama close < seviye → bearish SFP
    yon="DOWN" → low < seviye ama close > seviye → bullish SFP
    """
    for m in mumlar[-3:]:
        o, h, l, c, _ = m
        if yon == "UP" and h > seviye * (1 + esik_pct) and c < seviye:
            return True
        if yon == "DOWN" and l < seviye * (1 - esik_pct) and c > seviye:
            return True
    return False


async def oar_analiz(sembol: str = "BTCUSDT") -> dict:
    """
    OAR tam seans analizi.

    Döner:
      skor       : -100 ile +100 arası (pozitif = LONG, negatif = SHORT)
      yon        : LONG / SHORT / NEUTRAL
      aciklama   : neden listesi
      aktif_seans: şu anki seans
      setup_listesi: aktif setup'lar
      asia       : Asia Range detayları
      guvenis    : 0-100
    """
    simdi = datetime.now(timezone.utc)
    saat_utc = simdi.hour
    aktif = _aktif_seans(saat_utc)

    # 15 dakikalık 112 mum = ~28 saat (1 tam Asia + London + NY siklusu)
    mumlar = await _ohlcv_al(sembol, "15m", 112)
    if len(mumlar) < 20:
        return {
            "skor": 0, "yon": "NEUTRAL",
            "aciklama": "Yeterli OHLCV verisi alınamadı",
            "aktif_seans": aktif, "setup_listesi": [], "guvenis": 0
        }

    fiyat = mumlar[-1][3]  # son kapanış
    skor = 0
    nedenler = []
    setup_listesi = []

    # ─── Asia Range (son gün 00:00-04:00 UTC = ilk 16 × 15m mum) ──
    # 112 mum geriye gidiyoruz, günün başındaki 16 mum ≈ Asia saatleri
    # Daha kesin: bugünkü 00:00 UTC'den sonraki mumlar
    gun_basi = simdi.replace(hour=0, minute=0, second=0, microsecond=0)
    asia_bitis = gun_basi.replace(hour=4)

    # Yaklaşım: son 112 mumun konumunu hesapla
    # Her mum 15dk → 112 mum = 28 saat geriye gider
    dakika_fark = (simdi - gun_basi).total_seconds() / 60
    asia_mum_sayisi = 16  # 4 saat × 4 mum/saat

    # Geriye giderek bugünün Asia mumlarını bul
    simdi_mum_index = len(mumlar) - 1
    asia_baslangic_index = simdi_mum_index - int(dakika_fark / 15)
    asia_bitis_index = asia_baslangic_index + asia_mum_sayisi

    # Sınır kontrolü
    asia_baslangic_index = max(0, asia_baslangic_index)
    asia_bitis_index = min(len(mumlar), asia_bitis_index)

    if asia_bitis_index > asia_baslangic_index:
        asia_mumlar = mumlar[asia_baslangic_index:asia_bitis_index]
    else:
        # Fallback: son 32 mumun ilk yarısı
        asia_mumlar = mumlar[max(0, len(mumlar)-32):max(0, len(mumlar)-16)]

    if asia_mumlar:
        asia_high = max(m[1] for m in asia_mumlar)
        asia_low  = min(m[2] for m in asia_mumlar)
        asia_open = asia_mumlar[0][0]
        asia_close = asia_mumlar[-1][3]
        asia_poc  = (asia_high + asia_low) / 2

        asia_range = asia_high - asia_low
        asia_range_pct = (asia_range / asia_low * 100) if asia_low > 0 else 0

        if asia_range_pct < 0.4:
            asia_durum = "BALANCED"
            nedenler.append(f"Asia Range DAR (%{asia_range_pct:.2f}) — Balanced → Expansion beklenir")
        elif asia_range_pct > 1.5:
            asia_durum = "EXPANDED"
            nedenler.append(f"Asia Range GENİŞ (%{asia_range_pct:.2f}) — Dikkat: Reversal mümkün")
        else:
            asia_durum = "NORMAL"
            nedenler.append(f"Asia Range normal (%{asia_range_pct:.2f})")

        # Fiyat Asia seviyelerine göre
        if fiyat > asia_high * 1.001:
            skor += 35
            nedenler.append(f"Fiyat Asia High üstünde (${asia_high:,.1f}) — Bullish Breakout")
            setup_listesi.append("Asia High Breakout → LONG")
        elif fiyat < asia_low * 0.999:
            skor -= 35
            nedenler.append(f"Fiyat Asia Low altında (${asia_low:,.1f}) — Bearish Breakout")
            setup_listesi.append("Asia Low Breakout → SHORT")
        elif fiyat > asia_poc:
            skor += 15
            nedenler.append(f"Fiyat Asia POC üstünde (${asia_poc:,.1f}) — Bullish bias")
        else:
            skor -= 15
            nedenler.append(f"Fiyat Asia POC altında (${asia_poc:,.1f}) — Bearish bias")

        # Asia yönü (open→close)
        if asia_close > asia_open * 1.001:
            skor += 10
            nedenler.append("Asia bullish kapandı (close > open)")
        elif asia_close < asia_open * 0.999:
            skor -= 10
            nedenler.append("Asia bearish kapandı (close < open)")

        # SFP tespiti
        if _sfp_tespit(mumlar[-8:], asia_high, "UP"):
            skor -= 15
            nedenler.append(f"Bearish SFP: Asia High ${asia_high:,.1f} kırılıp kapandı altında")
            setup_listesi.append("Bearish SFP @ Asia High")
        if _sfp_tespit(mumlar[-8:], asia_low, "DOWN"):
            skor += 15
            nedenler.append(f"Bullish SFP: Asia Low ${asia_low:,.1f} kırılıp kapandı üstünde")
            setup_listesi.append("Bullish SFP @ Asia Low")

        # Likidite sweep
        son_5 = mumlar[-5:]
        for m in son_5:
            o, h, l, c, _ = m
            if h > asia_high and c < asia_high:
                skor -= 12
                nedenler.append(f"Likidite Sweep ↑ Asia High ${asia_high:,.1f} → geri döndü")
                setup_listesi.append("Asia High Liquidity Sweep → SHORT setup")
            if l < asia_low and c > asia_low:
                skor += 12
                nedenler.append(f"Likidite Sweep ↓ Asia Low ${asia_low:,.1f} → geri döndü")
                setup_listesi.append("Asia Low Liquidity Sweep → LONG setup")
    else:
        asia_high = asia_low = asia_poc = 0
        asia_range_pct = 0
        asia_durum = "UNKNOWN"

    # ─── London Analizi ─────────────────────────────────────────
    if asia_high > 0 and len(mumlar) >= 48:
        # London = 07:00-10:00 UTC ≈ Asia'dan 3 saat sonra 12 mum
        london_baslangic = asia_bitis_index + 12  # +3 saat boşluk
        london_bitis = london_baslangic + 12       # 3 saat
        london_baslangic = max(0, min(london_baslangic, len(mumlar) - 12))
        london_bitis = min(len(mumlar), london_bitis)

        if london_bitis > london_baslangic:
            london_mumlar = mumlar[london_baslangic:london_bitis]
            lon_h = max(m[1] for m in london_mumlar)
            lon_l = min(m[2] for m in london_mumlar)

            if lon_h > asia_high and lon_l > asia_low:
                skor += 20
                nedenler.append(f"London: Asia High kırdı (${lon_h:,.1f}) — Bullish London Breakout")
                setup_listesi.append("London Bullish Breakout")
            elif lon_l < asia_low and lon_h < asia_high:
                skor -= 20
                nedenler.append(f"London: Asia Low kırdı (${lon_l:,.1f}) — Bearish London Breakout")
                setup_listesi.append("London Bearish Breakout")
            elif lon_h > asia_high and lon_l < asia_low:
                nedenler.append("London: Her iki yönü de kırdı — Fakeout / Manipülasyon riski")
                setup_listesi.append("London Fakeout Riski — İkisi birden kırıldı")
            else:
                nedenler.append("London: Asia Range içinde — Konsolidasyon")

    # ─── Silver Bullet (10:00-11:00 UTC) ───────────────────────
    if aktif == "silver_bullet":
        nedenler.append("Silver Bullet penceresi AÇIK (10:00-11:00 UTC) — ICT setup")
        setup_listesi.append("Silver Bullet Penceresi Aktif")
        skor = skor * 1.1  # Silver Bullet güçlü sinyal üretir, etkiyi artır

    # ─── NY Session (13:30-16:30 UTC) ──────────────────────────
    if aktif == "ny":
        # NY continuation: Asia/London yönünde mi gidiyoruz?
        son_yon = "LONG" if fiyat > (asia_poc or fiyat) else "SHORT"
        nedenler.append(f"NY Seansı Aktif — {son_yon} yönünde devam bekleniyor")
        setup_listesi.append(f"NY Continuation → {son_yon}")

    # ─── V-Reversal Tespiti ────────────────────────────────────
    if len(mumlar) >= 4:
        son4 = mumlar[-4:]
        kapanislar = [m[3] for m in son4]
        if kapanislar[2] < kapanislar[0] and kapanislar[-1] > kapanislar[1]:
            skor += 8
            nedenler.append("V-Reversal paterni oluşuyor (düşüş → toparlanma)")
            setup_listesi.append("V-Reversal → LONG")
        elif kapanislar[2] > kapanislar[0] and kapanislar[-1] < kapanislar[1]:
            skor -= 8
            nedenler.append("Ters V paterni (yükseliş → düşüş)")
            setup_listesi.append("Inverted V → SHORT")

    skor = max(-100, min(100, int(skor)))

    return {
        "skor": skor,
        "yon": "LONG" if skor > 20 else "SHORT" if skor < -20 else "NEUTRAL",
        "aciklama": " | ".join(nedenler),
        "aktif_seans": aktif,
        "setup_listesi": setup_listesi,
        "asia": {
            "high": round(asia_high, 2),
            "low": round(asia_low, 2),
            "poc": round(asia_poc, 2) if asia_poc else 0,
            "range_pct": round(asia_range_pct, 3),
            "durum": asia_durum
        },
        "guvenis": 85,
        "fiyat": round(fiyat, 2)
    }
