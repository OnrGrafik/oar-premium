"""
Session Agent — OAR Premium
══════════════════════════════════════════════════════════════════
Asia / London / New York seans analizi.

Her seans için tespit edilenler:
  - Seansın açılış/kapanış saatleri (UTC)
  - Seans içi range (High - Low)
  - Seans yönü (Bullish / Bearish / Neutral)
  - Asia buy mu oldu? (London devam mı break mi yapacak?)
  - London range yapılmış mı?
  - NY continuation mu yoksa reversal mı?

time_context.py'deki makro risk skoruyla birleşir.
"""

import asyncio
from datetime import datetime, timezone, time as dtime
from exchange_client import klines as _klines


# ─── Seans tanımları (UTC) ────────────────────────────────────────

SEANSLAR = {
    "ASIA":   {"baslangic": dtime(0,  0), "bitis": dtime(8,  0)},
    "LONDON": {"baslangic": dtime(7,  0), "bitis": dtime(16, 0)},
    "NY":     {"baslangic": dtime(13, 0), "bitis": dtime(22, 0)},
}

# London açılış dalgası
LONDON_ACILIS = dtime(7, 0)
NY_ACILIS = dtime(13, 30)   # ABD piyasası açılışı


def _simdi_utc() -> datetime:
    return datetime.now(timezone.utc)


def _aktif_seans() -> str:
    """Şu anki aktif seans."""
    saat = _simdi_utc().time()
    if dtime(13, 0) <= saat < dtime(22, 0):
        return "NY"
    if dtime(7, 0) <= saat < dtime(16, 0):
        return "LONDON"
    return "ASIA"


def _sonraki_seans(aktif: str) -> str:
    sira = {"ASIA": "LONDON", "LONDON": "NY", "NY": "ASIA"}
    return sira[aktif]


# ─── Seans verisi çekme ───────────────────────────────────────────

def _seans_mumlarini_filtrele(candles: list, baslangic: dtime, bitis: dtime) -> list:
    """Belirli saatler arasındaki mumları filtrele (UTC)."""
    sonuc = []
    for c in candles:
        ts_saat = datetime.utcfromtimestamp(c[0] / 1000).time()
        if baslangic <= ts_saat < bitis:
            sonuc.append(c)
    return sonuc


def _seans_range(mumlar: list) -> dict:
    """Seans high / low ve yön."""
    if not mumlar:
        return {"high": 0.0, "low": 0.0, "range": 0.0, "yon": "BILINMIYOR"}
    high = max(c[2] for c in mumlar)
    low = min(c[3] for c in mumlar)
    acilis = mumlar[0][1]
    kapanis = mumlar[-1][4]
    yon = "BULLISH" if kapanis > acilis else "BEARISH" if kapanis < acilis else "NOTR"
    return {
        "high": round(high, 2),
        "low": round(low, 2),
        "range": round(high - low, 2),
        "acilis": round(acilis, 2),
        "kapanis": round(kapanis, 2),
        "yon": yon,
        "mum_sayisi": len(mumlar),
    }


# ─── OAR Seans Kuralları ──────────────────────────────────────────

def _oar_seans_kuralları(asia: dict, london: dict, ny: dict, aktif_seans: str) -> dict:
    """
    Senin sistemindeki seans kuralları:
      1. Asia buy oldu mu? → London continuation veya fakeout riski
      2. London range yapıldı mı? → NY'de break beklentisi
      3. NY: London range'ini kırıyor mu, yoksa reversal mı?
    """
    yorumlar = []
    trade_yonlendirme = "BEKLE"

    # Asia analizi
    asia_buy = asia.get("yon") == "BULLISH"
    asia_sell = asia.get("yon") == "BEARISH"
    asia_range = asia.get("range", 0)

    if asia_buy:
        yorumlar.append("Asia BUY: London'da continuation veya fakeout sonrası uzun ara.")
    elif asia_sell:
        yorumlar.append("Asia SELL: London'da aşağı devam veya short squeeze dikkat.")
    else:
        yorumlar.append("Asia nötr: London yön belirleyecek.")

    # London analizi
    london_range_yapildi = london.get("mum_sayisi", 0) > 3 and london.get("range", 0) > 0
    if london_range_yapildi:
        if london.get("yon") == "BULLISH":
            yorumlar.append(f"London BULLISH range: NY {london['high']:.0f} üzerini test edebilir.")
            if asia_buy:
                trade_yonlendirme = "LONG_ONCEKI"
        elif london.get("yon") == "BEARISH":
            yorumlar.append(f"London BEARISH range: NY {london['low']:.0f} altını test edebilir.")
            if asia_sell:
                trade_yonlendirme = "SHORT_ONCEKI"
        else:
            yorumlar.append("London range nötr: NY yönü belirsiz.")
    else:
        yorumlar.append("London verisi henüz yetersiz veya seans başlamadı.")

    # NY analizi
    if ny.get("mum_sayisi", 0) > 3:
        if ny.get("yon") == "BULLISH" and london.get("yon") == "BULLISH":
            yorumlar.append("NY London devamı — trend uyumu YÜKSEKtir.")
            trade_yonlendirme = "LONG_AKTIF"
        elif ny.get("yon") == "BEARISH" and london.get("yon") == "BULLISH":
            yorumlar.append("NY London'a zıt hareket — reversal ihtimali, dikkat.")
            trade_yonlendirme = "REVERSAL_RISKI"
        elif ny.get("yon") == "BEARISH" and london.get("yon") == "BEARISH":
            yorumlar.append("NY London devamı aşağı — trend uyumu YÜKSEKtir.")
            trade_yonlendirme = "SHORT_AKTIF"

    # Aktif seans bazlı ek uyarı
    if aktif_seans == "ASIA":
        yorumlar.append("Aktif seans: Asia — genellikle düşük volatilite, range oyunu.")
    elif aktif_seans == "LONDON":
        yorumlar.append("Aktif seans: London açılışı — en yüksek fakeout riski.")
    elif aktif_seans == "NY":
        yorumlar.append("Aktif seans: NY — trend kırılımları ve yüksek hacim.")

    return {
        "trade_yonlendirme": trade_yonlendirme,
        "yorumlar": yorumlar,
        "asia_buy": asia_buy,
        "asia_sell": asia_sell,
        "london_range_yapildi": london_range_yapildi,
    }


# ─── Ana Analiz ───────────────────────────────────────────────────

async def session_analiz(sembol: str = "BTCUSDT") -> dict:
    """
    Günlük seans analizini çalıştırır.

    Returns:
        {
            "aktif_seans":        str
            "sonraki_seans":      str
            "asia":               dict  — high/low/range/yon
            "london":             dict
            "ny":                 dict
            "oar_kurallar":       dict  — trade yönlendirmesi
            "trade_yonlendirme":  str
            "ozet":               str
            "sembol":             str
        }
    """
    try:
        # Son 24 saatin 15 dakikalık mumları
        candles_raw = await _klines(sembol, "15m", 96, futures=True)
        if not candles_raw:
            return _varsayilan(sembol)

        candles = [[float(x) for x in row[:6]] for row in candles_raw]

        # Her seans mumlarını filtrele
        asia_mumlar   = _seans_mumlarini_filtrele(candles, dtime(0, 0), dtime(8, 0))
        london_mumlar = _seans_mumlarini_filtrele(candles, dtime(7, 0), dtime(16, 0))
        ny_mumlar     = _seans_mumlarini_filtrele(candles, dtime(13, 0), dtime(22, 0))

        asia   = _seans_range(asia_mumlar)
        london = _seans_range(london_mumlar)
        ny     = _seans_range(ny_mumlar)

        aktif = _aktif_seans()
        sonraki = _sonraki_seans(aktif)
        kurallar = _oar_seans_kuralları(asia, london, ny, aktif)

        ozet = (
            f"Aktif: {aktif} → Sonraki: {sonraki} | "
            f"Asia={asia['yon']} ({asia['range']:.0f}$) | "
            f"London={london['yon']} ({london['range']:.0f}$) | "
            f"NY={ny['yon']} ({ny['range']:.0f}$) | "
            f"Yönlendirme: {kurallar['trade_yonlendirme']}"
        )

        return {
            "aktif_seans":       aktif,
            "sonraki_seans":     sonraki,
            "asia":              asia,
            "london":            london,
            "ny":                ny,
            "oar_kurallar":      kurallar,
            "trade_yonlendirme": kurallar["trade_yonlendirme"],
            "yorumlar":          kurallar["yorumlar"],
            "ozet":              ozet,
            "sembol":            sembol,
        }

    except Exception as e:
        return _varsayilan(sembol, str(e))


def _varsayilan(sembol: str, hata: str = "") -> dict:
    return {
        "aktif_seans": "BILINMIYOR",
        "sonraki_seans": "BILINMIYOR",
        "asia": {}, "london": {}, "ny": {},
        "oar_kurallar": {},
        "trade_yonlendirme": "BEKLE",
        "yorumlar": [],
        "ozet": f"Seans analizi başarısız: {hata}",
        "sembol": sembol,
    }


if __name__ == "__main__":
    import json
    r = asyncio.run(session_analiz("BTCUSDT"))
    print(json.dumps(r, ensure_ascii=False, indent=2))
    print("\n" + r["ozet"])
    for y in r.get("yorumlar", []):
        print(f"  → {y}")
