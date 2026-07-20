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


def _rsi(closes: list, pencere: int = 14) -> float | None:
    """Wilder RSI (0-100). >50 alıcı baskın, <50 satıcı baskın. Veri yoksa None."""
    if not closes or len(closes) < pencere + 1:
        return None
    kazanc, kayip = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        kazanc.append(max(d, 0.0)); kayip.append(max(-d, 0.0))
    ag = sum(kazanc[:pencere]) / pencere
    al = sum(kayip[:pencere]) / pencere
    for i in range(pencere, len(kazanc)):     # Wilder yumuşatma
        ag = (ag * (pencere - 1) + kazanc[i]) / pencere
        al = (al * (pencere - 1) + kayip[i]) / pencere
    if al == 0:
        return 100.0
    rs = ag / al
    return 100.0 - (100.0 / (1.0 + rs))


def _efficiency_ratio(closes: list, pencere: int = 20) -> float | None:
    """
    Kaufman Efficiency Ratio: |net yol| / Σ|adım|. → 1 trend, → 0 range.
    Backtest'teki _gunluk_rejim ile aynı mantık (canlı rejim tespiti).
    """
    if not closes or len(closes) < pencere + 1:
        return None
    seri = closes[-(pencere + 1):]
    net = abs(seri[-1] - seri[0])
    toplam = sum(abs(seri[i] - seri[i - 1]) for i in range(1, len(seri)))
    if toplam <= 0:
        return None
    return net / toplam


async def _htf_vpfr_teyit(sembol: str, fiyat: float, tol_pct: float = 0.5):
    """
    HTF (haftalık) VPFR değer-alanı confluence: fiyat haftalık POC/VAH/VAL'ın
    ≤%0.5 yakınında mı? Backtest-kanıtlı yeni şampiyon bloğu (fade + htf_vpfr,
    BTC+ETH holdout SAĞLAM, PF ~2.3). Veri yoksa None (teyit uygulanmaz).
    Döner: (True/False, en_yakin_seviye_adı) veya (None, None).
    """
    try:
        from kiyotaka_engine import get_volume_profile
        import time
        simdi = int(time.time())
        hafta_basi = simdi - (simdi % 604800)          # haftalık pencere başı (UTC)
        vp = await get_volume_profile(sembol, hafta_basi, 604800)
    except Exception:
        return None, None
    if not vp or vp.get("error") or not vp.get("poc"):
        return None, None
    seviyeler = {"POC": vp.get("poc"), "VAH": vp.get("vah"), "VAL": vp.get("val")}
    en_yakin, en_mesafe = None, None
    for ad, sv in seviyeler.items():
        if not sv or fiyat <= 0:
            continue
        mesafe = abs(fiyat - sv) / fiyat * 100
        if en_mesafe is None or mesafe < en_mesafe:
            en_mesafe, en_yakin = mesafe, ad
    if en_mesafe is None:
        return None, None
    return bool(en_mesafe <= tol_pct), en_yakin


async def _whale_retail_teyit(sembol: str, yon: str):
    """
    Whale/Retail diverjans EK CONFIRM (Binance futures positioning):
      retail = globalLongShortAccountRatio (hesap-ağırlıklı → kalabalık)
      whale  = topLongShortPositionRatio  (pozisyon-ağırlıklı → büyük eller)
      WRD = whale% − retail%.
    OAR işlem yönü ile whale AYNI tarafta + retail KARŞIDA ise diverjans teyidi:
      LONG işlem → WRD > 0 (whale retail'den daha long, kalabalık geride)
      SHORT işlem → WRD < 0 (whale retail'den daha short)
    Veri yoksa (None, None, None). Döner: (aligned_bool, wrd, retail_long%).
    """
    try:
        import httpx
        FAPI = "https://fapi.binance.com"
        async with httpx.AsyncClient(timeout=12) as cl:
            rg = await cl.get(f"{FAPI}/futures/data/globalLongShortAccountRatio",
                              params={"symbol": sembol, "period": "5m", "limit": 1})
            rt = await cl.get(f"{FAPI}/futures/data/topLongShortPositionRatio",
                              params={"symbol": sembol, "period": "5m", "limit": 1})
        g = rg.json(); t = rt.json()
        if not (isinstance(g, list) and g and isinstance(t, list) and t):
            return None, None, None
        retail = float(g[-1]["longAccount"]) * 100
        whale = float(t[-1]["longAccount"]) * 100
    except Exception:
        return None, None, None
    wrd = whale - retail
    aligned = (wrd > 0) if yon == "LONG" else (wrd < 0)
    return bool(aligned), round(wrd, 2), round(retail, 1)


def _opsiyon_bias():
    """
    Opsiyon Path B son bileşik sinyalini diskten okur (ağ yok, ucuz).
    {dvol_skew_bearish, vol_sinyali, dvol_pct, ...} veya boş dict.
    Log yeterince birikmemişse dvol_skew_bearish None döner → overlay uygulanmaz.
    """
    try:
        from options_signals import son_bilesik_sinyal
        return son_bilesik_sinyal() or {}
    except Exception:
        return {}


_MARKET_GUNU_CACHE = {"gun": None, "val": None}


async def _market_fade_gunu():
    """
    Market-rejim kapısı: BTC ve ETH Asia range ≥ %1 ise o gün FADE-tradeable.
    İkisi de sağlıyorsa altcoinler dahil işlem açılabilir; biri bile <%1 ise
    o gün komple trade yok. Döner: (uygun_bool, btc_pct, eth_pct).

    Asia günde bir kez (00:00-04:00 UTC) oluşur → sonuç GÜN bazında cache'lenir.
    Her paper/altcoin tik'inde BTC+ETH klines'ı yeniden çekmez (yüz istek/gün → 1).
    Veri gelemezse cache YAZILMAZ (sonraki tik tekrar dener).
    """
    from datetime import datetime, timezone
    bugun = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _MARKET_GUNU_CACHE["gun"] == bugun and _MARKET_GUNU_CACHE["val"] is not None:
        return _MARKET_GUNU_CACHE["val"]
    try:
        b = await _asia_range_pct("BTCUSDT")
        e = await _asia_range_pct("ETHUSDT")
    except Exception:
        return None, None, None
    if b is None or e is None:
        return None, None, None
    val = (bool(b >= 1.0 and e >= 1.0), round(b, 2), round(e, 2))
    _MARKET_GUNU_CACHE["gun"] = bugun
    _MARKET_GUNU_CACHE["val"] = val
    return val


_SEMBOL_GUNU_CACHE = {}   # {sembol: {"gun": ..., "val": (bool, pct)}}


async def _sembol_fade_gunu(sembol: str):
    """
    PER-SEMBOL market kapısı: yalnız BU sembolün Asia range ≥ %1 mi.
    Backtest kanıtı (oar_kapi_analiz, kullanıcı onaylı ANAYASA #8 değişikliği):
    şampiyonlar her sembolü KENDİ Asia≥%1'iyle değerlendirince en yüksek getiri +
    likidasyon YOK (5x dahil). Eski iki-kapı (BTC VE ETH ≥%1) ETH'nin tek başına
    %1 yaptığı ~363 pozitif-beklentili günü çöpe atıp ETH getirisini ~yarıya
    indiriyordu, hiçbir risk faydası olmadan (maxDD birebir aynı). Döner:
    (uygun_bool | None, pct | None). Gün bazında SEMBOL BAŞINA cache'lenir
    (tik başına klines'ı yeniden çekmez). Veri gelemezse cache YAZILMAZ.
    """
    from datetime import datetime, timezone
    bugun = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    c = _SEMBOL_GUNU_CACHE.get(sembol)
    if c and c["gun"] == bugun and c["val"] is not None:
        return c["val"]
    try:
        p = await _asia_range_pct(sembol)
    except Exception:
        return None, None
    if p is None:
        return None, None
    val = (bool(p >= 1.0), round(p, 2))
    _SEMBOL_GUNU_CACHE[sembol] = {"gun": bugun, "val": val}
    return val


async def _asia_range_pct(sembol: str):
    """Bugünün Asia (00:00-04:00 UTC) range yüzdesi. Veri yoksa None."""
    mumlar = await _ohlcv_al(sembol, "15m", 112)
    if len(mumlar) < 20:
        return None
    from datetime import datetime, timezone
    simdi = datetime.now(timezone.utc)
    gun_basi = simdi.replace(hour=0, minute=0, second=0, microsecond=0)
    dakika_fark = (simdi - gun_basi).total_seconds() / 60
    son_idx = len(mumlar) - 1
    a_bas = max(0, son_idx - int(dakika_fark / 15))
    a_bit = min(len(mumlar), a_bas + 16)
    asia = mumlar[a_bas:a_bit] or mumlar[max(0, len(mumlar)-32):max(0, len(mumlar)-16)]
    if not asia:
        return None
    hi = max(m[1] for m in asia); lo = min(m[2] for m in asia)
    return (hi - lo) / lo * 100 if lo > 0 else None


def _prev_poc_kalicilik(mumlar: list, prev_poc: float):
    """
    Trend-devam sağlık metriği: fiyat önceki günün POC'unun ALTINDA KALICI mı?
    Altına wick atıp geri dönmek (likidite alma) trendi bozmaz; ama son mumların
    ÇOĞU POC altında KAPANIYORSA → trend zayıflaması. Döner: "guclu"/"zayif"/None.
    """
    if not prev_poc or len(mumlar) < 6:
        return None
    son = mumlar[-6:]
    alt_kapanis = sum(1 for m in son if m[3] < prev_poc)   # POC altında kapanan mum
    if alt_kapanis >= 4:
        return "zayif"      # çoğunluk altta kapandı → kalıcı altta → zayıflama
    return "guclu"          # üstte tutunuyor (wick'ler sayılmaz, kapanış esas)


def _oar_asia_teyit(mumlar: list, asia_high: float, asia_low: float):
    """
    OAR Asia Range fade teyidi (canlı): Asia ekstremini süpürüp geri kapanan mum.
      • sweep + reclaim → ekstremi geçip geri kapanan mum (tuzağa düşenler)
      • absorpsiyon     → o mumda yüksek hacim (vol_z ≥ 1)
    poc_taraf çağıran tarafta doğrulanır. Döner: (yon, vol_z) ya da None.
    """
    if len(mumlar) < 20 or asia_high <= 0 or asia_low <= 0:
        return None
    import statistics
    voller = [m[4] for m in mumlar[-20:]]
    ort = statistics.mean(voller)
    std = statistics.pstdev(voller) or 1.0
    for m in mumlar[-5:]:
        o, h, l, c, v = m
        vz = (v - ort) / std
        if vz < 1.0:
            continue                       # absorpsiyon yok → teyit yok
        if h > asia_high and c < asia_high:    # üst süpürme + reclaim → SHORT
            return ("SHORT", vz)
        if l < asia_low and c > asia_low:      # alt süpürme + reclaim → LONG
            return ("LONG", vz)
    return None


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

        # ─── REJİM: Asia range < %1 = trend-devam (fade YOK) / ≥ %1 = fade günü ──
        oar_rejim = "trend_devam" if asia_range_pct < 1.0 else "fade"

        # Önceki gün POC'u (Asia öncesi ~24s hacim-ağırlıklı) — trend-devam sağlığı
        prev_poc = None
        pv0 = max(0, asia_baslangic_index - 96)
        if asia_baslangic_index - pv0 >= 20:
            prev_dilim = mumlar[pv0:asia_baslangic_index]
            hacim_bin = {}
            for om in prev_dilim:
                pb = round(om[3], -1) if om[3] > 1000 else round(om[3], 2)
                hacim_bin[pb] = hacim_bin.get(pb, 0.0) + om[4]
            if hacim_bin:
                prev_poc = max(hacim_bin, key=hacim_bin.get)

        if oar_rejim == "trend_devam":
            # Bu gün FADE ALINMAZ (Asia sıkışması → trend-devam rejimi).
            # Sağlık: fiyat önceki gün POC'unun altında KALICI mı? (wick sorun değil)
            saglik = _prev_poc_kalicilik(mumlar, prev_poc)
            # RSI teyidi (kullanıcı okuması: RSI >50 kalıcı → long-bias, aşırı satım yok)
            rsi = _rsi([m[3] for m in mumlar], pencere=14)
            rsi_str = f" · RSI {rsi:.0f}" if rsi is not None else ""
            rsi_long = (rsi is not None and rsi >= 50)
            if saglik == "guclu":
                nedenler.append(
                    f"📈 TREND-DEVAM günü (Asia %{asia_range_pct:.2f} <%1{rsi_str}): fiyat önceki "
                    f"gün POC üstünde kalıcı → trend güçlü, LONG-bias, FADE ALMA")
                setup_listesi.append("Trend-Devam (güçlü) → LONG-bias, fade yok")
                if rsi_long:
                    nedenler.append(
                        f"✅ RSI {rsi:.0f} ≥50 → alıcı baskın, aşırı satım yok "
                        f"(long-bias teyidi)")
            elif saglik == "zayif":
                nedenler.append(
                    f"📉 TREND-DEVAM günü ama fiyat önceki gün POC altında kalıcı{rsi_str} "
                    f"→ trend zayıflaması, temkinli")
                setup_listesi.append("Trend-Devam (zayıf) → trend zayıflıyor")
                if rsi is not None and rsi < 50:
                    nedenler.append(
                        f"⚠️ RSI {rsi:.0f} <50 → satıcı baskın, trend zayıflamasını doğruluyor")
            else:
                nedenler.append(
                    f"➡️ TREND-DEVAM günü (Asia %{asia_range_pct:.2f} <%1{rsi_str}): fade rejimi değil")
        else:
            # ─── OAR ASIA RANGE fade teyidi (Asia ekstremini sweep+reclaim+absorpsiyon) ──
            teyit = _oar_asia_teyit(mumlar, asia_high, asia_low)
            if teyit:
                yon_t, vz = teyit
                poc_ok = ((yon_t == "SHORT" and fiyat >= asia_poc) or
                          (yon_t == "LONG" and fiyat <= asia_poc))
                if poc_ok:   # POC tarafı + absorpsiyon + reclaim hizalı
                    skor += (25 if yon_t == "LONG" else -25)   # heuristik güven katsayısı
                    nedenler.append(
                        f"⭐ OAR Asia Range fade ({yon_t}): POC tarafı + absorpsiyon "
                        f"(hacim z{vz:.1f}) + reclaim")
                    setup_listesi.append(f"OAR Asia Range → {yon_t}")

                    # HTF-VPFR confluence (backtest şampiyonu: fade+htf_vpfr, holdout SAĞLAM)
                    vpfr_ok, vpfr_ad = await _htf_vpfr_teyit(sembol, fiyat)
                    if vpfr_ok:
                        skor += (15 if yon_t == "LONG" else -15)   # heuristik
                        nedenler.append(
                            f"⭐⭐ HTF-VPFR confluence: fiyat haftalık {vpfr_ad} seviyesinde "
                            f"— backtest-kanıtlı (fade+htf_vpfr)")
                        setup_listesi.append(f"HTF-VPFR Confluence ({vpfr_ad}) → {yon_t}")

                    # Whale/Retail diverjans EK CONFIRM — SADECE fade gününde (mean-reversion
                    # edge'i; trend gününde retail chasing genelde haklı olur, o yüzden yok).
                    wr_ok, wrd, wr_retail = await _whale_retail_teyit(sembol, yon_t)
                    if wr_ok:
                        skor += (10 if yon_t == "LONG" else -10)   # heuristik
                        nedenler.append(
                            f"🐋 Whale/Retail diverjans teyidi ({yon_t}): WRD {wrd:+.1f} "
                            f"(whale işlem yönünde, retail %{wr_retail} karşı tarafta)")
                        setup_listesi.append(f"Whale-Retail Confirm → {yon_t}")

                    # Opsiyon Path B market-bias overlay (BTC-market geneli, diskten okunur).
                    # dvol_skew_bearish=True → düşüş baskısı: LONG fade'i kır, SHORT fade'i destekle.
                    ob = _opsiyon_bias()
                    if ob.get("dvol_skew_bearish") is True:
                        skor += (-8 if yon_t == "LONG" else 8)   # heuristik, bearish market
                        nedenler.append(
                            f"🩸 Opsiyon: DVOL↑+skew↑ düşüş baskısı (pct {ob.get('dvol_pct')}) "
                            f"→ {yon_t} fade {'zayıflatıldı' if yon_t=='LONG' else 'desteklendi'}")
                        setup_listesi.append("Opsiyon düşüş-bias overlay")
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

    # Rejim (Kaufman ER, son ~20 mum kapanışı) — trend/range ayrımı (canlı)
    er = _efficiency_ratio([m[3] for m in mumlar], pencere=20)
    rejim = ("trend" if er >= 0.40 else "range") if er is not None else "bilinmiyor"
    # OAR rejimi (Asia range eşiği): <%1 trend-devam (fade yok) / ≥%1 fade günü
    oar_rejim = ("trend_devam" if 0 < asia_range_pct < 1.0
                 else "fade" if asia_range_pct >= 1.0 else "bilinmiyor")

    return {
        "skor": skor,
        "yon": "LONG" if skor > 20 else "SHORT" if skor < -20 else "NEUTRAL",
        "aciklama": " | ".join(nedenler),
        "aktif_seans": aktif,
        "setup_listesi": setup_listesi,
        "rejim": rejim,
        "rejim_er": round(er, 3) if er is not None else None,
        "oar_rejim": oar_rejim,
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
