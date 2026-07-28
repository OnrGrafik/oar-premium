"""
oar_footprint_grafik.py — FOOTPRINT GÖRSELİ İÇİN VERİ (site grafiği)
════════════════════════════════════════════════════════════════════════════════════
NEDEN VAR (kullanıcı: "footprint komple gitmiş, nerede hata yaptık?"):
  Footprint bugüne dek YALNIZ MOTOR TARAFINDA vardı — `oar_canli_footprint` per-dakika
  delta/CVD + gün POC'u hesaplayıp ŞAMPİYONUN `poc_taraf` bloğunu besliyordu. `/api/oar-footprint`
  ise bir TEŞHİS endpoint'i (gerçek POC vs eski proxy ortanca kıyası) — çizim verisi DEĞİL.
  Site (live.html) footprint'i hiç çağırmıyordu; git geçmişi de gösteriyor ki footprint UI'ı
  HİÇ YAZILMAMIŞTI. Yani "gitmiş" değil, "görsel hiç yapılmamış". Bu modül o boşluğu kapatır.

YÖNTEM (şampiyonla BİREBİR AYNI matematik — yeni/uydurma gösterge YOK):
  • delta = 2·taker_buy − volume  (Binance 1m klines alan[9] = agresif ALIŞ hacmi)
    → backtest'in aggTrades/is_buyer_maker delta'sıyla AYNI (bkz. oar_canli_footprint).
  • fiyat bini = `_pbin` (4 anlamlı hane → BTC'de ~$10, ETH'de ~$1) — backtest POC'uyla aynı.
  • Her TF mumu, içindeki 1m barlardan kurulur: her 1m bar hacmi kendi tipik fiyatı
    ((H+L+C)/3) binine yazılır → mum başına fiyat-merdiveni (ladder) + alış/satış ayrımı.

⚠️ DÜRÜST SINIR (kullanıcıya da UI'da yazılır): bu 1-DAKİKA granülasyonlu footprint'tir.
  Gerçek tick-seviyesi bid×ask merdiveni için aggTrades (tick verisi) gerekir — canlıda her
  mum için tick çekmek 512MB sunucuda ağır/rate-limitli. 1m granülasyon: mum başına en fazla
  (TF/1dk) fiyat kademesi verir (5m→5, 15m→15, 1h→60) ve delta/CVD TAM DOĞRUDUR (yaklaşım değil).
"""
import asyncio
from datetime import datetime, timezone

_TF_DK = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240}
_CACHE = {}
_CACHE_TTL_S = 45


def _pbin(px: float) -> float:
    """Fiyat bini — oar_canli_footprint/_backtest ile AYNI (4 anlamlı hane)."""
    if px <= 0:
        return px
    return float(f"{px:.4g}")


def _mum_kur(rows: list, tf_dk: int) -> list:
    """1m satırlarını ([ts,o,h,l,c,vol,taker_buy]) TF mumlarına topla + ladder çıkar."""
    kova = {}
    for ts, o, h, l, c, vol, taker in rows:
        # TF mum başlangıcı (ms) — dakika bazlı hizalama
        dk = int(ts // 60000)
        mum_dk = (dk // tf_dk) * tf_dk
        mts = mum_dk * 60000
        m = kova.get(mts)
        if m is None:
            m = kova[mts] = {"ts": mts, "o": o, "h": h, "l": l, "c": c,
                             "vol": 0.0, "buy": 0.0, "sell": 0.0, "ladder": {}}
        m["h"] = max(m["h"], h)
        m["l"] = min(m["l"], l)
        m["c"] = c                                   # son 1m kapanışı = TF kapanışı
        sat = max(vol - taker, 0.0)                  # agresif SATIŞ = toplam − agresif alış
        m["vol"] += vol
        m["buy"] += taker
        m["sell"] += sat
        pb = _pbin((h + l + c) / 3.0)                # tipik fiyat bini (backtest ile aynı)
        lad = m["ladder"].setdefault(pb, {"buy": 0.0, "sell": 0.0})
        lad["buy"] += taker
        lad["sell"] += sat
    # sırala + delta/CVD hesapla
    mumlar = []
    kum = 0.0
    for mts in sorted(kova):
        m = kova[mts]
        delta = m["buy"] - m["sell"]                 # = 2·taker_buy − vol
        kum += delta
        merdiven = [{"p": p, "buy": round(v["buy"], 4), "sell": round(v["sell"], 4),
                     "delta": round(v["buy"] - v["sell"], 4)}
                    for p, v in sorted(m["ladder"].items())]
        mumlar.append({
            "ts": m["ts"], "o": m["o"], "h": m["h"], "l": m["l"], "c": m["c"],
            "vol": round(m["vol"], 4), "buy": round(m["buy"], 4), "sell": round(m["sell"], 4),
            "delta": round(delta, 4), "cvd": round(kum, 4), "ladder": merdiven,
        })
    return mumlar


async def footprint_grafik(sembol: str = "BTCUSDT", tf: str = "5m", mum: int = 60) -> dict:
    """
    Site grafiği için footprint verisi. TF mumları + her mumun fiyat-merdiveni + delta/CVD.
    Tek REST isteği (1m klines_taker, limit≤1000) → hafif, restart-dayanıklı.
    """
    tf_dk = _TF_DK.get(tf, 5)
    mum = max(5, min(int(mum or 60), 200))
    gerekli_dk = tf_dk * mum
    if gerekli_dk > 1000:                            # tek istek sınırı → mum sayısını kıs
        mum = 1000 // tf_dk
        gerekli_dk = tf_dk * mum

    ck = f"{sembol}:{tf}:{mum}"
    simdi = datetime.now(timezone.utc).timestamp()
    c = _CACHE.get(ck)
    if c and (simdi - c["ts"]) < _CACHE_TTL_S:
        return c["veri"]

    baslangic = int((simdi - gerekli_dk * 60) * 1000)
    try:
        from exchange_client import klines_taker
        rows = await klines_taker(sembol, "1m", min(gerekli_dk + 5, 1000),
                                  futures=False, start_ms=baslangic)
    except Exception as e:
        return {"durum": "veri_yok", "aciklama": f"1m taker verisi alınamadı: {str(e)[:80]}",
                "sembol": sembol, "tf": tf, "mumlar": []}
    if not rows:
        return {"durum": "veri_yok", "aciklama": "1m taker verisi boş",
                "sembol": sembol, "tf": tf, "mumlar": []}

    mumlar = _mum_kur(rows, tf_dk)[-mum:]
    # CVD'yi GÖSTERİLEN pencereye tabanla: kırpılan mumların deltası taşınmasın →
    # cvd_son == ekrandaki deltaların toplamı (kullanıcı gözle doğrulayabilir).
    if mumlar:
        taban = mumlar[0]["cvd"] - mumlar[0]["delta"]
        for m in mumlar:
            m["cvd"] = round(m["cvd"] - taban, 4)
    # gün hacim-profili POC'u (şampiyonun kullandığıyla aynı yöntem)
    poc_px = {}
    for ts, o, h, l, cl, vol, taker in rows:
        poc_px[_pbin((h + l + cl) / 3.0)] = poc_px.get(_pbin((h + l + cl) / 3.0), 0.0) + vol
    poc = max(poc_px, key=poc_px.get) if poc_px else None

    veri = {
        "durum": "ok", "sembol": sembol, "tf": tf,
        "mum_sayisi": len(mumlar), "poc": poc,
        "cvd_son": mumlar[-1]["cvd"] if mumlar else 0.0,
        "delta_son": mumlar[-1]["delta"] if mumlar else 0.0,
        "mumlar": mumlar,
        "granulasyon": "1m",
        "aciklama": ("Agresif alış/satış ayrımı 1 dakikalık veriden (delta = 2·alış − toplam). "
                     "Delta/CVD tam doğrudur; fiyat kademeleri 1 dakika çözünürlüğündedir."),
    }
    _CACHE[ck] = {"ts": simdi, "veri": veri}
    return veri


if __name__ == "__main__":
    async def _t():
        r = await footprint_grafik("BTCUSDT", "5m", 12)
        print(r.get("durum"), r.get("mum_sayisi"), "POC", r.get("poc"))
        for m in (r.get("mumlar") or [])[-3:]:
            print(m["ts"], "delta", m["delta"], "cvd", m["cvd"], "kademe", len(m["ladder"]))
    asyncio.run(_t())
