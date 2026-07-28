"""
oar_footprint_canli.py — CANLI FOOTPRINT / AKIŞ (site Komuta Merkezi "📊 Akış" paneli)
════════════════════════════════════════════════════════════════════════════════════
Kullanıcı isteği: BTC+ETH için mum-başına fiyat-seviyesi footprint (sağda alış, solda
satış, delta), tick ayarlanabilir, sağda tüm görünür mumların birleşik büyük mumu,
mum üstünde toplam alış/satış/delta, borsa seçimi + aggregated, DOM/orderbook.

VERİ KAYNAĞI (Faz 1 = Binance tam tarihsel):
  • OHLC          : exchange_client.klines (mum gövdesi)
  • Footprint tick: exchange_client.agg_trades (agresif alış/satış, is_buyer_maker)
    → (mum × fiyat-tick bin) → seviye başına alis/satis/delta. footprint_engine ile
    AYNI aggressor mantığı (buy = not isBuyerMaker), yalnız canlı + REST.
  • DOM           : exchange_client.depth + oar_orderbook.metrikler

⚠ ÇOKLU-BORSA: yalnız Binance ücretsiz TAM tarihsel aggTrades verir. Diğer borsalar
  (Bybit/OKX/Coinbase) yalnız RECENT trade → onlar WS ile CANLI birikir (oar_ws_akis,
  Faz 2). Bu modül aggregated için oar_ws_akis buffer'ını da (varsa) toplar.

NO-LOOKAHEAD gerekmez (bu canlı görselleştirme; şampiyon karar akışına DOKUNMAZ — #8).
"""
import asyncio
import time

_INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000,
    "1d": 86_400_000,
}

# Sembol başına makul varsayılan footprint tick (frontend override edebilir).
_VARSAYILAN_TICK = {"BTCUSDT": 10.0, "ETHUSDT": 1.0}

_CACHE = {}          # {(sembol,interval,tick,limit,borsalar): {"ts":epoch, "veri":...}}
_CACHE_TTL_S = 2     # kısa (arka plan doldukça yeni seviyeler görünsün)

# Per-mum footprint önbelleği (GEÇMİŞ mum DEĞİŞMEZ → bir kez çekilir, kalıcı cache).
# {(sembol,interval,tick,mum_ts): {"seviyeler":[...], "alis","satis","delta","poc"}}
_FP_CACHE = {}
_FP_ISLENIYOR = set()   # {(sembol,interval,tick)} — aynı anda tek arka-plan doldurucu
_FP_TANI = {}           # {(sembol,interval): {"son_trade","son_hata","denendi","kaynak"}}
_FP_SON = {}            # {(sembol,interval): epoch} — son doldurma tetiği (backoff için)
_KFP_CACHE = {}         # {(sembol,interval,bar_ts): kiyotaka bar footprint} — GERÇEK veri, kalıcı


async def _fp_doldur(sembol, interval, tick, mum_ts_list, futures):
    """
    Eksik mumların per-seviye footprint'ini aggTrades'ten mum-mum çeker → _FP_CACHE.
    Yeniden→eskiye; tur başına en fazla 8 mum (nazik, rate-limit dostu). GEÇMİŞ mum
    bir kez çekilir (dolu cache); GÜNCEL mum her turda tazelenir.
    ⚠ BOŞ sonuç KALICI cache'lenmez → tekrar denenir (0/40'ta takılma çözümü).
    ⚠ Futures aggTrades boş dönerse SPOT aggTrades fallback (weight 1, daha güvenli).
    Teşhis (_FP_TANI): kaç trade geldi / hata ne (frontend rozetinde görünür).
    """
    key0 = (sembol, interval, tick)
    if key0 in _FP_ISLENIYOR:
        return
    _FP_ISLENIYOR.add(key0)
    tani = {"son_trade": 0, "son_hata": "", "denendi": 0, "kaynak": ""}
    try:
        from exchange_client import agg_trades
        ms = _INTERVAL_MS.get(interval, 300_000)
        now = time.time() * 1000
        islenen = 0
        for cts in mum_ts_list:
            if islenen >= 8:
                break
            ck = (sembol, interval, tick, cts)
            guncel = cts <= now < cts + ms
            if ck in _FP_CACHE and _FP_CACHE[ck]["seviyeler"] and not guncel:
                continue
            son = min(cts + ms, int(now) + 1000)
            trades = []
            kaynak = ""
            try:
                trades = await agg_trades(sembol, cts, son, futures=futures,
                                          max_trade=40000)
                kaynak = "futures" if futures else "spot"
                if not trades and futures:      # futures boş → spot fallback
                    trades = await agg_trades(sembol, cts, son, futures=False,
                                              max_trade=40000)
                    kaynak = "spot"
            except Exception as e:
                tani["son_hata"] = str(e)[:90]
            tani["denendi"] += 1
            if not trades:
                continue                        # BOŞ → cache'leme, sonra tekrar dene
            hucreler = {}
            for tr in trades:
                fb = _bin(tr["p"], tick)
                h = hucreler.setdefault(fb, [0.0, 0.0])
                if tr["buy"]:
                    h[0] += tr["q"]
                else:
                    h[1] += tr["q"]
            alis = sum(a for a, _ in hucreler.values())
            satis = sum(s for _, s in hucreler.values())
            hacim = {p: (a + s) for p, (a, s) in hucreler.items()}
            poc = max(hacim, key=hacim.get) if hacim else None
            _FP_CACHE[ck] = {
                "seviyeler": _seviye_listesi(hucreler),
                "alis": round(alis, 4), "satis": round(satis, 4),
                "delta": round(alis - satis, 4),
                "poc": round(poc, 4) if poc is not None else None,
            }
            tani["son_trade"] = len(trades); tani["kaynak"] = kaynak
            islenen += 1
            now = time.time() * 1000
            await asyncio.sleep(0.2)
        if len(_FP_CACHE) > 4000:
            for k in list(_FP_CACHE)[:1500]:
                _FP_CACHE.pop(k, None)
    finally:
        _FP_TANI[(sembol, interval)] = tani
        _FP_ISLENIYOR.discard(key0)


def varsayilan_tick(sembol: str, spot: float = 0.0) -> float:
    """Sembol için makul tick. Bilinmiyorsa spot'un ~%0.02'sinden türet."""
    t = _VARSAYILAN_TICK.get(sembol)
    if t:
        return t
    if spot > 0:
        # 2 anlamlı haneye yuvarlanmış ~%0.02 adım
        adim = spot * 0.0002
        us = 10 ** (len(str(int(adim))) - 1) if adim >= 1 else 1
        return max(round(adim / us) * us, us) or 1.0
    return 1.0


def _bin(px: float, tick: float) -> float:
    """Fiyatı tick bin merkezine yuvarla."""
    if tick <= 0:
        return px
    return round(px / tick) * tick


def _footprint_derle(mum_ts_list: list, trades: list, tick: float) -> dict:
    """
    Trade listesini (mum × fiyat-seviyesi) footprint tablosuna derler.
    Döner: {mum_ts: {fiyat_bin: [alis, satis]}}  (yalnız verilen mum_ts kovaları).
    """
    interval_ms = None
    if len(mum_ts_list) >= 2:
        interval_ms = mum_ts_list[1] - mum_ts_list[0]
    tablo = {ts: {} for ts in mum_ts_list}
    ts_set = set(mum_ts_list)
    for tr in trades:
        if interval_ms:
            mts = (tr["t"] // interval_ms) * interval_ms
        else:
            mts = mum_ts_list[0]
        if mts not in ts_set:
            # pencere dışı/kayan bar → en yakın kovaya değil, atla (temiz kalsın)
            continue
        fb = _bin(tr["p"], tick)
        hucre = tablo[mts].setdefault(fb, [0.0, 0.0])
        if tr["buy"]:
            hucre[0] += tr["q"]      # alış (sağ)
        else:
            hucre[1] += tr["q"]      # satış (sol)
    return tablo


def _seviye_listesi(hucreler: dict) -> list:
    """{fiyat: [alis,satis]} → sıralı [{p,alis,satis,delta}] (yüksekten alçağa)."""
    out = []
    for p in sorted(hucreler, reverse=True):
        alis, satis = hucreler[p]
        out.append({"p": round(p, 4), "alis": round(alis, 4),
                    "satis": round(satis, 4), "delta": round(alis - satis, 4)})
    return out


def _poc_va(hucreler: dict) -> dict:
    """Birleşik profilden POC + %70 değer alanı (VAH/VAL)."""
    if not hucreler:
        return {"poc": None, "vah": None, "val": None}
    hacim = {p: (a + s) for p, (a, s) in hucreler.items()}
    toplam = sum(hacim.values()) or 1.0
    poc = max(hacim, key=hacim.get)
    # POC'tan yukarı+aşağı komşuları ekleyerek %70'e ulaş (Market Profile VA)
    fiyatlar = sorted(hacim, reverse=True)
    idx = fiyatlar.index(poc)
    alt = ust = idx
    birikmis = hacim[poc]
    hedef = toplam * 0.70
    while birikmis < hedef and (ust > 0 or alt < len(fiyatlar) - 1):
        ust_v = hacim[fiyatlar[ust - 1]] if ust > 0 else -1
        alt_v = hacim[fiyatlar[alt + 1]] if alt < len(fiyatlar) - 1 else -1
        if ust_v >= alt_v and ust > 0:
            ust -= 1; birikmis += ust_v
        elif alt < len(fiyatlar) - 1:
            alt += 1; birikmis += alt_v
        else:
            break
    return {"poc": round(poc, 4),
            "vah": round(fiyatlar[ust], 4),
            "val": round(fiyatlar[alt], 4)}


async def _yedek_doldur(sembol: str, interval: str, mum_sayisi: int,
                        futures: bool = True) -> dict:
    """
    ANAHTARSIZ footprint yedeği: Binance 1m klines taker-buy → per-mum fiyat kademeleri.
    Kiyotaka (dış API, anahtar ister) yoksa/boşsa devreye girer; footprint asla komple
    ölmez. PERP (futures=True) birincil. Döner: {mum_ts: {seviyeler, alis, satis, delta, poc}}.
    """
    try:
        from oar_footprint_grafik import footprint_grafik
        d = await footprint_grafik(sembol, interval, max(5, mum_sayisi), futures=futures)
    except Exception:
        return {}
    if not d or d.get("durum") != "ok":
        return {}
    out = {}
    for m in d.get("mumlar", []):
        sev = [{"p": l["p"], "alis": l["buy"], "satis": l["sell"], "delta": l["delta"]}
               for l in (m.get("ladder") or [])]
        if not sev:
            continue
        hacim = {s["p"]: s["alis"] + s["satis"] for s in sev}
        out[int(m["ts"])] = {
            "seviyeler": sev, "alis": m["buy"], "satis": m["sell"],
            "delta": m["delta"],
            "poc": max(hacim, key=hacim.get) if hacim else None,
        }
    return out


async def footprint(sembol: str = "BTCUSDT", interval: str = "5m",
                    tick: float = 0.0, limit: int = 40,
                    borsalar: tuple = ("binance_perp",)) -> dict:
    """
    Görünür pencere (son `limit` mum) için footprint. 15s cache.
    Döner: {sembol,interval,tick,borsalar,spot,mumlar[],birlesik{}}.
    """
    interval = interval if interval in _INTERVAL_MS else "5m"
    limit = max(5, min(limit, 120))
    anahtar = (sembol, interval, tick, limit, tuple(borsalar))
    import time as _t
    simdi = _t.time()
    c = _CACHE.get(anahtar)
    if c and (simdi - c["ts"]) < _CACHE_TTL_S:
        return c["veri"]

    from exchange_client import klines
    ms = _INTERVAL_MS[interval]
    futures = ("binance_spot" not in borsalar)
    try:
        kl = await klines(sembol, interval, limit + 1, futures=futures)
    except Exception:
        return {"sembol": sembol, "interval": interval, "durum": "veri_yok",
                "not": "mum verisi gelmedi"}
    if not kl:
        return {"sembol": sembol, "interval": interval, "durum": "veri_yok"}

    mumlar_ham = kl[-limit:]
    bas_ms = int(mumlar_ham[0][0])
    spot = float(mumlar_ham[-1][4])
    if not tick or tick <= 0:
        tick = varsayilan_tick(sembol, spot)

    # ── GERÇEK footprint: KIYOTAKA VOLUME_PROFILE_AGG per-mum (aggTrades ÇÖP) ──
    # Geçmiş mum DEĞİŞMEZ → kalıcı cache; eksik + oluşmakta olan mumu Kiyotaka'dan
    # PARALEL çek. Kiyotaka hazır per-mum footprint verir → "1 mumda var" sorunu biter.
    now = time.time() * 1000
    bar_sec = ms // 1000
    eksik = [int(r[0]) for r in mumlar_ham
             if (sembol, interval, int(r[0])) not in _KFP_CACHE
             or (int(r[0]) <= now < int(r[0]) + ms)]     # güncel mumu tazele
    hata = ""
    if eksik:
        try:
            import kiyotaka_engine as _kiy
            sem = asyncio.Semaphore(10)

            async def _cek(bts):
                async with sem:
                    bf = await _kiy.bar_footprint(sembol, bts // 1000, bar_sec)
                if bf:
                    _KFP_CACHE[(sembol, interval, bts)] = bf
            await asyncio.gather(*[_cek(t) for t in eksik])
        except Exception as e:
            hata = str(e)[:90]
        if len(_KFP_CACHE) > 6000:
            for k in list(_KFP_CACHE)[:2000]:
                _KFP_CACHE.pop(k, None)

    # ── YEDEK (Kiyotaka HİÇ seviye vermediyse — KIYOTAKA_API_KEY yok/401/kota/kesinti):
    #  ① aggTrades GERÇEK TICK (ANAHTARSIZ, endüstri standardı): her fiyat seviyesinde
    #     o seviyede dönen tüm trade'ler toplanır → KALIN per-fiyat seviye (Binance
    #     footprint'iyle aynı ölçek). Kademeli doldurma (tur başına ≤8 mum) + kalıcı cache.
    #     Bu, "seviyeler 13-15 ince" (1dk-taker) sorununu kökten çözer — gerçek tick.
    #  ② aggTrades da vermezse → 1m klines taker (coarse ama boş kalmasın, son çare).
    yedek = {}
    kaynak_ad = "kiyotaka"
    mum_ts = [int(r[0]) for r in mumlar_ham]
    if not any((sembol, interval, ts) in _KFP_CACHE for ts in mum_ts):
        # aggTrades GERÇEK tick'i ARKA PLANDA doldur — endpoint'i BLOKE ETME. (Inline await
        # 8 mum×sayfalama ~10s bloke ediyordu → frontend fetch timeout → "yükleniyor"da
        # takılıyordu.) _FP_ISLENIYOR kilidi çift doldurucuyu önler; her poll'de cache dolar.
        try:
            asyncio.ensure_future(_fp_doldur(sembol, interval, tick, list(mum_ts), futures))
        except Exception as e:
            hata = (hata + " | agg:" + str(e)[:40]) if hata else str(e)[:70]
        agg_var = any((sembol, interval, tick, ts) in _FP_CACHE for ts in mum_ts)
        # ANLIK boyama: 1dk taker (tek istek, hızlı) → aggTrades doldukça per-mum ÜSTÜNE geçer
        yedek = await _yedek_doldur(sembol, interval, len(mumlar_ham), futures)
        kaynak_ad = "binance_aggtrades" if agg_var else ("binance_1m_taker_yedek" if yedek else "yok")

    mumlar = []
    birlesik_hucre = {}
    for r in mumlar_ham:
        ts = int(r[0])
        bf = (_KFP_CACHE.get((sembol, interval, ts))
              or _FP_CACHE.get((sembol, interval, tick, ts)) or yedek.get(ts))
        if bf:
            seviyeler = bf["seviyeler"]; alis = bf["alis"]; satis = bf["satis"]; poc = bf["poc"]
        else:
            seviyeler = []; alis = 0.0; satis = 0.0; poc = None
        for s in seviyeler:
            bh = birlesik_hucre.setdefault(s["p"], [0.0, 0.0])
            bh[0] += s["alis"]; bh[1] += s["satis"]
        mumlar.append({
            "t": ts, "o": float(r[1]), "h": float(r[2]), "l": float(r[3]),
            "c": float(r[4]), "v": float(r[5]),
            "alis": round(alis, 4), "satis": round(satis, 4),
            "delta": round(alis - satis, 4),
            "poc": poc, "seviyeler": seviyeler,
        })

    b_alis = sum(a for a, _ in birlesik_hucre.values())
    b_satis = sum(s for _, s in birlesik_hucre.values())
    birlesik = {
        "alis": round(b_alis, 4), "satis": round(b_satis, 4),
        "delta": round(b_alis - b_satis, 4),
        "seviyeler": _seviye_listesi(birlesik_hucre),
        **_poc_va(birlesik_hucre),
    }
    # gerçek tick = Kiyotaka bin aralığı (medyan komşu-fiyat farkı) → satır yüksekliği doğru
    tpx = sorted({s["p"] for m in mumlar for s in m["seviyeler"]})
    if len(tpx) >= 3:
        gaps = sorted(tpx[i + 1] - tpx[i] for i in range(len(tpx) - 1) if tpx[i + 1] > tpx[i])
        if gaps:
            g = gaps[len(gaps) // 2]
            if g > 0:
                tick = round(g, 4)

    seviyeli = sum(1 for m in mumlar if m["seviyeler"])
    veri = {
        "sembol": sembol, "interval": interval, "tick": tick,
        "borsalar": list(borsalar), "spot": spot, "kaynak": kaynak_ad,
        "seviyeli_mum": seviyeli, "toplam_mum": len(mumlar),
        "eksik": len([1 for m in mumlar if not m["seviyeler"]]),
        "tani": {k: v for k, v in {
            "son_hata": hata,
            "kaynak": kaynak_ad,
            "agg": _FP_TANI.get((sembol, interval), {}),   # aggTrades trade/hata teşhisi
            "yedek": ("Kiyotaka seviye vermedi → aggTrades gerçek tick yedeği" if kaynak_ad == "binance_aggtrades"
                      else "Kiyotaka+aggTrades yok → 1dk taker (coarse)" if kaynak_ad == "binance_1m_taker_yedek"
                      else ""),
        }.items() if v},
        "mumlar": mumlar, "birlesik": birlesik, "durum": "ok",
    }
    _CACHE[anahtar] = {"ts": simdi, "veri": veri}
    return veri


async def orderbook_dom(sembol: str = "BTCUSDT", seviye: int = 25,
                        futures: bool = True) -> dict:
    """Canlı DOM ladder + metrikler (imbalance/true_pressure). oar_orderbook kullanır."""
    from exchange_client import depth
    from oar_orderbook import metrikler
    try:                                   # liq map için geniş kapsama (limit 500)
        ob = await depth(sembol, limit=500 if seviye > 30 else max(seviye, 50), futures=futures)
    except Exception:
        return {"sembol": sembol, "durum": "veri_yok"}
    # liq map: yakın 50 yerine TÜM derinlikten EN BÜYÜK duvarlar (geniş bölge kapsaması)
    tum_b = ob.get("bids", []); tum_a = ob.get("asks", [])
    bids = sorted(tum_b, key=lambda x: -x[1])[:seviye] if seviye > 30 else tum_b[:seviye]
    asks = sorted(tum_a, key=lambda x: -x[1])[:seviye] if seviye > 30 else tum_a[:seviye]
    m = metrikler(tum_b, tum_a, min(seviye, 25))
    return {
        "sembol": sembol, "durum": "ok",
        "bids": [[round(p, 4), round(q, 4)] for p, q in bids],
        "asks": [[round(p, 4), round(q, 4)] for p, q in asks],
        "metrik": m,
    }


_LH_CACHE = {}   # {sembol: {"ts":epoch,"veri":...}}


async def likidasyon_haritasi(sembol: str = "BTCUSDT", futures: bool = True) -> dict:
    """
    TAHMİNİ likidasyon haritası (Coinglass/CoinAnk yaklaşımı): kaldıraçlı pozisyonların
    zorunlu kapanacağı fiyat seviyeleri. Gerçek pozisyon verisi kimsede yok → MODEL:
    son ~10 gün fiyat/hacim = pozisyonların açıldığı yerler (proxy), yaygın kaldıraç
    kademelerinde (10/25/50/100x) likidasyon fiyatları projelendirilir, hacimle
    ağırlıklanıp fiyat bin'lerine toplanır → seviye başına 'likidasyon yoğunluğu'.
      long liq = entry·(1−1/L)  (fiyat DÜŞERSE likide, spot ALTI)
      short liq= entry·(1+1/L)  (fiyat ÇIKARSA likide, spot ÜSTÜ)
    ⚠ TAHMİN (gerçek değil); order-book likiditesinden FARKLI (o duran emir).
    """
    import time as _t
    c = _LH_CACHE.get(sembol)
    if c and (_t.time() - c["ts"]) < 120:
        return c["veri"]
    from exchange_client import klines
    try:
        kl = await klines(sembol, "1h", 240, futures=futures)   # ~10 gün
    except Exception:
        return {"sembol": sembol, "durum": "veri_yok"}
    if not kl:
        return {"sembol": sembol, "durum": "veri_yok"}
    spot = float(kl[-1][4])
    if spot <= 0:
        return {"sembol": sembol, "durum": "veri_yok"}
    tiers = [(10, 0.12), (25, 0.28), (50, 0.32), (100, 0.28)]   # kaldıraç: ağırlık
    bin_sz = max(spot * 0.0008, 0.01)
    longs = {}; shorts = {}
    for r in kl:
        entry = float(r[4]); vol = float(r[5])
        if vol <= 0:
            continue
        for L, w in tiers:
            lb = round(entry * (1 - 1.0 / L) / bin_sz) * bin_sz
            longs[lb] = longs.get(lb, 0.0) + vol * w
            sb = round(entry * (1 + 1.0 / L) / bin_sz) * bin_sz
            shorts[sb] = shorts.get(sb, 0.0) + vol * w
    lo, hi = spot * 0.86, spot * 1.14
    seviyeler = []; mx = 1.0
    for p, v in longs.items():
        if lo <= p <= hi:
            seviyeler.append(["long", round(p, 2), v]); mx = max(mx, v)
    for p, v in shorts.items():
        if lo <= p <= hi:
            seviyeler.append(["short", round(p, 2), v]); mx = max(mx, v)
    out = [{"taraf": s[0], "fiyat": s[1], "yogunluk": round(s[2] / mx, 4)}
           for s in seviyeler if s[2] / mx >= 0.04]
    out.sort(key=lambda s: -s["yogunluk"])
    veri = {"sembol": sembol, "durum": "ok", "spot": spot,
            "seviyeler": out[:220],
            "not": "tahmini likidasyon (OI/kaldıraç modeli) — gerçek pozisyon değil"}
    _LH_CACHE[sembol] = {"ts": _t.time(), "veri": veri}
    return veri


def borsa_listesi() -> dict:
    """Seçilebilir borsalar + Faz durumu (frontend borsa seçici)."""
    return {
        "borsalar": [
            {"id": "binance_perp", "ad": "Binance Perp", "footprint": "tam",
             "acilis": True},
            {"id": "binance_spot", "ad": "Binance Spot", "footprint": "tam"},
            {"id": "bybit", "ad": "Bybit", "footprint": "canli"},
            {"id": "okx", "ad": "OKX", "footprint": "canli"},
            {"id": "coinbase", "ad": "Coinbase", "footprint": "canli"},
        ],
        "aggregated": True,
        "not": "tam = geçmiş dahil anında; canli = şu andan itibaren birikir (WS)",
    }


if __name__ == "__main__":
    async def _t():
        fp = await footprint("BTCUSDT", "5m", limit=12)
        print("durum:", fp.get("durum"), "trade:", fp.get("trade_sayisi"),
              "tick:", fp.get("tick"))
        if fp.get("mumlar"):
            m = fp["mumlar"][-1]
            print("son mum delta:", m["delta"], "seviye:", len(m["seviyeler"]))
            print("birleşik POC/VAH/VAL:", fp["birlesik"]["poc"],
                  fp["birlesik"]["vah"], fp["birlesik"]["val"])
    asyncio.run(_t())
