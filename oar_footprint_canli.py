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

_INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000,
    "1d": 86_400_000,
}

# Sembol başına makul varsayılan footprint tick (frontend override edebilir).
_VARSAYILAN_TICK = {"BTCUSDT": 10.0, "ETHUSDT": 1.0}

_CACHE = {}          # {(sembol,interval,tick,limit,borsalar): {"ts":epoch, "veri":...}}
_CACHE_TTL_S = 15    # canlı ama REST'i yormayan tazelik


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

    from exchange_client import klines, agg_trades
    ms = _INTERVAL_MS[interval]
    try:
        kl = await klines(sembol, interval, limit + 1, futures=True)
    except Exception:
        return {"sembol": sembol, "interval": interval, "durum": "veri_yok",
                "not": "mum verisi gelmedi"}
    if not kl:
        return {"sembol": sembol, "interval": interval, "durum": "veri_yok"}

    mumlar_ham = kl[-limit:]
    mum_ts_list = [int(r[0]) for r in mumlar_ham]
    bas_ms = mum_ts_list[0]
    son_ms = mum_ts_list[-1] + ms
    spot = float(mumlar_ham[-1][4])
    if not tick or tick <= 0:
        tick = varsayilan_tick(sembol, spot)

    # ── Footprint tick verisi (Binance perp = tam tarihsel) ──
    trades = []
    if "binance_perp" in borsalar or "binance_spot" in borsalar or not borsalar:
        try:
            trades = await agg_trades(sembol, bas_ms, son_ms,
                                      futures=("binance_spot" not in borsalar))
        except Exception:
            trades = []
    # ── Diğer borsalar (WS canlı buffer — Faz 2, varsa) ──
    try:
        import oar_ws_akis
        ekstra = oar_ws_akis.footprint_trades(sembol, borsalar, bas_ms, son_ms)
        if ekstra:
            trades = trades + ekstra
    except Exception:
        pass

    tablo = _footprint_derle(mum_ts_list, trades, tick)

    mumlar = []
    birlesik_hucre = {}
    for r in mumlar_ham:
        ts = int(r[0])
        hucreler = tablo.get(ts, {})
        alis = sum(a for a, _ in hucreler.values())
        satis = sum(s for _, s in hucreler.values())
        hacim = {p: (a + s) for p, (a, s) in hucreler.items()}
        poc = max(hacim, key=hacim.get) if hacim else None
        for p, (a, s) in hucreler.items():
            bh = birlesik_hucre.setdefault(p, [0.0, 0.0])
            bh[0] += a; bh[1] += s
        mumlar.append({
            "t": ts, "o": float(r[1]), "h": float(r[2]), "l": float(r[3]),
            "c": float(r[4]), "v": float(r[5]),
            "alis": round(alis, 4), "satis": round(satis, 4),
            "delta": round(alis - satis, 4),
            "poc": round(poc, 4) if poc is not None else None,
            "seviyeler": _seviye_listesi(hucreler),
        })

    b_alis = sum(a for a, _ in birlesik_hucre.values())
    b_satis = sum(s for _, s in birlesik_hucre.values())
    birlesik = {
        "alis": round(b_alis, 4), "satis": round(b_satis, 4),
        "delta": round(b_alis - b_satis, 4),
        "seviyeler": _seviye_listesi(birlesik_hucre),
        **_poc_va(birlesik_hucre),
    }
    veri = {
        "sembol": sembol, "interval": interval, "tick": tick,
        "borsalar": list(borsalar), "spot": spot,
        "trade_sayisi": len(trades), "mumlar": mumlar, "birlesik": birlesik,
        "durum": "ok",
    }
    _CACHE[anahtar] = {"ts": simdi, "veri": veri}
    return veri


async def orderbook_dom(sembol: str = "BTCUSDT", seviye: int = 25,
                        futures: bool = True) -> dict:
    """Canlı DOM ladder + metrikler (imbalance/true_pressure). oar_orderbook kullanır."""
    from exchange_client import depth
    from oar_orderbook import metrikler
    try:
        ob = await depth(sembol, limit=max(seviye, 50), futures=futures)
    except Exception:
        return {"sembol": sembol, "durum": "veri_yok"}
    bids = ob.get("bids", [])[:seviye]
    asks = ob.get("asks", [])[:seviye]
    m = metrikler(ob.get("bids", []), ob.get("asks", []), seviye)
    return {
        "sembol": sembol, "durum": "ok",
        "bids": [[round(p, 4), round(q, 4)] for p, q in bids],
        "asks": [[round(p, 4), round(q, 4)] for p, q in asks],
        "metrik": m,
    }


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
