"""
oar_ws_akis.py — CANLI WS AKIŞ KATMANI (footprint aggregated + heatmap + liq map)
════════════════════════════════════════════════════════════════════════════════════
Faz 2/3: çoklu-borsa CANLI akış. Binance tam tarihsel aggTrades REST'ten gelir
(oar_footprint_canli); DİĞER borsalar (Bybit/OKX/Coinbase) yalnız RECENT trade verir →
onlar WS ile ŞU ANDAN İTİBAREN birikir (kullanıcı "canlı birikim" onayı).

TOPLANAN (borsa × sembol, bellekte halka tampon):
  • trades : {t,p,q,buy} → footprint aggregated (WS'ten gelen non-Binance borsalar)
  • depth  : zaman-serisi L2 ladder → HEATMAP (zaman×fiyat×likidite)
  • liq    : forceOrder/liquidation olayları → LIQ MAP

DÜRÜST SINIR: tarihsel L2/liq YOK → heatmap+liq yalnız site açık kaldıkça birikir.
websockets yoksa (sandbox) modül sessizce pasif kalır (aktif=False); Railway'de çalışır.
⚠ Görselleştirme katmanı — şampiyon karar akışına DOKUNMAZ (Anayasa #8).
"""
import asyncio
import json
import time
from collections import deque

# ── Halka tamponlar (borsa,sembol) → deque ──────────────────────────
_TRADES = {}     # {(borsa,sembol): deque[{t,p,q,buy}]}
_DEPTH = {}      # {(borsa,sembol): deque[{t,bids,asks}]}  (heatmap zaman serisi)
_LIQ = {}        # {(borsa,sembol): deque[{t,taraf,fiyat,miktar}]}
_SON_DEPTH = {}  # {(borsa,sembol): {t,bids,asks}}  (anlık en son ladder)
_DURUM = {}      # {borsa: 'baglandi'/'kapali'/'hata'}

_TRADE_TTL_S = 2 * 3600      # footprint için 2 saat
_DEPTH_TTL_S = 30 * 60       # heatmap için 30 dk
_LIQ_TTL_S = 3 * 3600        # liq için 3 saat
_DEPTH_ARALIK_S = 3          # heatmap için depth örnekleme adımı


def _dq(harita, anahtar):
    d = harita.get(anahtar)
    if d is None:
        d = deque(maxlen=200_000)
        harita[anahtar] = d
    return d


def _trim(d, ttl_s):
    esik = time.time() * 1000 - ttl_s * 1000
    while d and d[0]["t"] < esik:
        d.popleft()


# ── Sembol çevirisi (borsa başına) ──────────────────────────────────
def _sembol(borsa: str, sembol: str) -> str:
    kok = sembol.replace("USDT", "")
    return {
        "bybit": sembol,
        "okx": f"{kok}-USDT-SWAP",
        "coinbase": f"{kok}-USD",
    }.get(borsa, sembol)


# ═══════════════════════════════════════════════════════════════════
# BORSA WS ADAPTÖRLERİ (url + abonelik + mesaj ayrıştırıcı)
# ═══════════════════════════════════════════════════════════════════

def _binance_perp(sembol):
    s = sembol.lower()
    url = (f"wss://fstream.binance.com/stream?streams="
           f"{s}@aggTrade/{s}@depth20@500ms/{s}@forceOrder")
    return url, None   # abonelik URL'de → sub mesajı yok

def _bybit(sembol):
    return "wss://stream.bybit.com/v5/public/linear", {
        "op": "subscribe",
        "args": [f"publicTrade.{sembol}", f"orderbook.50.{sembol}",
                 f"liquidation.{sembol}"]}

def _okx(sembol):
    inst = _sembol("okx", sembol)
    return "wss://ws.okx.com:8443/ws/v5/public", {
        "op": "subscribe",
        "args": [{"channel": "trades", "instId": inst},
                 {"channel": "books5", "instId": inst}]}

def _coinbase(sembol):
    prod = _sembol("coinbase", sembol)
    return "wss://ws-feed.exchange.coinbase.com", {
        "type": "subscribe", "product_ids": [prod],
        "channels": ["matches", {"name": "level2_batch", "product_ids": [prod]}]}


_WS_URL = {"binance_perp": _binance_perp, "bybit": _bybit, "okx": _okx,
           "coinbase": _coinbase}


def _isle(borsa, sembol, msg):
    """Gelen WS mesajını ayrıştırıp uygun tampona yaz. Hata sessiz (tek mesaj atlar)."""
    try:
        d = json.loads(msg)
    except Exception:
        return
    now = time.time() * 1000
    try:
        if borsa == "binance_perp":
            data = d.get("data") or {}
            stream = d.get("stream", "")
            if "aggTrade" in stream:
                _dq(_TRADES, (borsa, sembol)).append(
                    {"t": int(data["T"]), "p": float(data["p"]),
                     "q": float(data["q"]), "buy": (not data["m"])})
            elif "depth" in stream:
                _depth_yaz(borsa, sembol, data.get("b", []), data.get("a", []), now)
            elif "forceOrder" in stream:
                o = data.get("o") or {}
                taraf = "long" if o.get("S") == "SELL" else "short"
                _dq(_LIQ, (borsa, sembol)).append(
                    {"t": int(o.get("T", now)), "taraf": taraf,
                     "fiyat": float(o.get("ap") or o.get("p") or 0),
                     "miktar": float(o.get("q", 0))})
        elif borsa == "bybit":
            topic = d.get("topic", "")
            if topic.startswith("publicTrade"):
                for tr in d.get("data", []):
                    _dq(_TRADES, (borsa, sembol)).append(
                        {"t": int(tr["T"]), "p": float(tr["p"]), "q": float(tr["v"]),
                         "buy": (tr["S"] == "Buy")})
            elif topic.startswith("orderbook"):
                ob = d.get("data", {})
                _depth_yaz(borsa, sembol, ob.get("b", []), ob.get("a", []), now)
            elif topic.startswith("liquidation"):
                for li in ([d["data"]] if isinstance(d.get("data"), dict) else d.get("data", [])):
                    taraf = "long" if li.get("side") == "Sell" else "short"
                    _dq(_LIQ, (borsa, sembol)).append(
                        {"t": int(li.get("updatedTime", li.get("T", now))),
                         "taraf": taraf, "fiyat": float(li.get("price", 0)),
                         "miktar": float(li.get("size", li.get("v", 0)))})
        elif borsa == "okx":
            arg = d.get("arg", {})
            ch = arg.get("channel", "")
            if ch == "trades":
                for tr in d.get("data", []):
                    _dq(_TRADES, (borsa, sembol)).append(
                        {"t": int(tr["ts"]), "p": float(tr["px"]), "q": float(tr["sz"]),
                         "buy": (tr["side"] == "buy")})
            elif ch == "books5":
                for ob in d.get("data", []):
                    _depth_yaz(borsa, sembol, ob.get("bids", []), ob.get("asks", []), now)
        elif borsa == "coinbase":
            tip = d.get("type")
            if tip in ("match", "last_match"):
                # Coinbase 'side' = maker tarafı → taker agresör TERSİ
                buy = (d.get("side") == "sell")
                _dq(_TRADES, (borsa, sembol)).append(
                    {"t": now, "p": float(d["price"]), "q": float(d["size"]), "buy": buy})
            elif tip == "snapshot":
                _depth_yaz(borsa, sembol, d.get("bids", [])[:50], d.get("asks", [])[:50], now)
    except Exception:
        return


def _depth_yaz(borsa, sembol, bids, asks, now):
    """L2 ladder'ı anlık sakla; heatmap zaman-serisine örnek adımıyla ekle."""
    try:
        b = [[float(p), float(q)] for p, q, *_ in bids][:50]
        a = [[float(p), float(q)] for p, q, *_ in asks][:50]
    except Exception:
        return
    if not b or not a:
        return
    _SON_DEPTH[(borsa, sembol)] = {"t": now, "bids": b, "asks": a}
    dq = _dq(_DEPTH, (borsa, sembol))
    if not dq or (now - dq[-1]["t"]) >= _DEPTH_ARALIK_S * 1000:
        dq.append({"t": now, "bids": b, "asks": a})
        _trim(dq, _DEPTH_TTL_S)


# ═══════════════════════════════════════════════════════════════════
# SORGU FONKSİYONLARI (endpoint + footprint modülü kullanır)
# ═══════════════════════════════════════════════════════════════════

def footprint_trades(sembol, borsalar, bas_ms, son_ms):
    """
    [bas_ms, son_ms) penceresindeki WS trade'leri (yalnız NON-Binance borsalar —
    Binance zaten REST'ten geliyor, çift-sayım olmasın). Aggregated için birleştirir.
    """
    out = []
    for borsa in borsalar:
        if borsa.startswith("binance"):
            continue
        d = _TRADES.get((borsa, sembol))
        if not d:
            continue
        for tr in d:
            if bas_ms <= tr["t"] < son_ms:
                out.append(tr)
    return out


def heatmap(sembol, borsalar):
    """
    Likidite ısı haritası: seçili borsaların depth zaman-serisini birleştirip
    (zaman-dilimi × fiyat-bin × toplam likidite) ızgarası döndürür.
    """
    borsalar = [b for b in borsalar] or ["binance_perp"]
    seriler = []
    for b in borsalar:
        d = _DEPTH.get((b, sembol))
        if d:
            seriler.append(list(d))
    if not seriler:
        return {"symbol": sembol, "durum": "birikiyor",
                "not": "ısı haritası şu andan itibaren canlı birikir (WS)"}
    # tüm snapshot'ları topla → fiyat aralığı + zaman kovaları
    tum = [snap for s in seriler for snap in s]
    if not tum:
        return {"symbol": sembol, "durum": "birikiyor"}
    minP = min(min(p for p, _ in s["bids"]) for s in tum if s["bids"])
    maxP = max(max(p for p, _ in s["asks"]) for s in tum if s["asks"])
    if not (maxP > minP):
        return {"symbol": sembol, "durum": "birikiyor"}
    NB = 60                                   # fiyat bin sayısı
    NT = 80                                   # zaman kova sayısı
    tick = (maxP - minP) / NB
    t0 = min(s["t"] for s in tum); t1 = max(s["t"] for s in tum)
    tspan = (t1 - t0) or 1
    izgara = [[0.0] * NB for _ in range(NT)]
    for s in tum:
        ti = min(NT - 1, int((s["t"] - t0) / tspan * NT))
        for p, q in s["bids"] + s["asks"]:
            pi = int((p - minP) / (maxP - minP) * (NB - 1))
            if 0 <= pi < NB:
                izgara[ti][pi] += q
    kareler = []
    maxV = max((max(r) for r in izgara if r), default=1) or 1
    for ti in range(NT):
        for pi in range(NB):
            v = izgara[ti][pi]
            if v > 0:
                kareler.append([ti, pi, round(v / maxV, 4)])
    return {"symbol": sembol, "durum": "ok", "borsalar": borsalar,
            "min_fiyat": round(minP, 4), "max_fiyat": round(maxP, 4),
            "t0": t0, "t1": t1, "nb": NB, "nt": NT, "tick": round(tick, 6),
            "kareler": kareler}


def liq_map(sembol, borsalar):
    """Likidasyon olayları (seçili borsalar birleşik) + fiyat-kümesi özeti."""
    borsalar = [b for b in borsalar] or ["binance_perp"]
    olaylar = []
    for b in borsalar:
        d = _LIQ.get((b, sembol))
        if d:
            for o in d:
                olaylar.append({**o, "borsa": b})
    if not olaylar:
        return {"symbol": sembol, "durum": "birikiyor",
                "not": "likidasyon olayları şu andan itibaren canlı birikir (WS)"}
    olaylar.sort(key=lambda o: o["t"], reverse=True)
    # fiyat kümesi (kaba bin) — nerede yığılma var
    kume = {}
    for o in olaylar:
        b = round(o["fiyat"], -1)
        k = kume.setdefault(b, {"long": 0.0, "short": 0.0})
        k[o["taraf"]] += o["miktar"]
    kumeler = [{"fiyat": p, "long": round(v["long"], 3), "short": round(v["short"], 3)}
               for p, v in sorted(kume.items(), reverse=True)]
    return {"symbol": sembol, "durum": "ok", "borsalar": borsalar,
            "olaylar": olaylar[:200], "kumeler": kumeler}


def durum():
    """Borsa bağlantı durumu (endpoint/lider)."""
    return {"borsalar": dict(_DURUM),
            "trade_tampon": {f"{b}:{s}": len(d) for (b, s), d in _TRADES.items()},
            "depth_tampon": {f"{b}:{s}": len(d) for (b, s), d in _DEPTH.items()}}


# ═══════════════════════════════════════════════════════════════════
# WS DÖNGÜLERİ (main.py startup)
# ═══════════════════════════════════════════════════════════════════

async def _borsa_dongu(borsa, semboller):
    """Tek borsa için tüm sembolleri dinle (reconnect'li). websockets yoksa çık."""
    try:
        import websockets
    except Exception:
        _DURUM[borsa] = "websockets_yok"
        return
    while True:
        for sembol in semboller:
            asyncio.create_task(_ws_sembol(borsa, sembol))
        _DURUM[borsa] = "baglandi"
        await asyncio.sleep(3600)      # görev canlı; alt görevler reconnect eder


async def _ws_sembol(borsa, sembol):
    """Tek (borsa,sembol) WS bağlantısı — koparsa yeniden bağlanır."""
    import websockets
    yapici = _WS_URL.get(borsa)
    if not yapici:
        return
    while True:
        ping_task = None
        try:
            url, sub = yapici(sembol)
            async with websockets.connect(url, ping_interval=20, close_timeout=5,
                                          max_queue=2048) as ws:
                if sub:
                    await ws.send(json.dumps(sub))
                _DURUM[borsa] = "baglandi"
                # Bybit/OKX uygulama-seviyesi ping ister (yoksa ~30s'de kopar)
                if borsa in ("bybit", "okx"):
                    pmsg = "ping" if borsa == "okx" else json.dumps({"op": "ping"})
                    async def _ping():
                        while True:
                            await asyncio.sleep(18)
                            try:
                                await ws.send(pmsg)
                            except Exception:
                                return
                    ping_task = asyncio.create_task(_ping())
                async for msg in ws:
                    _isle(borsa, sembol, msg)
                    # periyodik trim (trade tamponu)
                    d = _TRADES.get((borsa, sembol))
                    if d and len(d) % 500 == 0:
                        _trim(d, _TRADE_TTL_S)
                    lq = _LIQ.get((borsa, sembol))
                    if lq and len(lq) % 50 == 0:
                        _trim(lq, _LIQ_TTL_S)
        except Exception as e:
            _DURUM[borsa] = f"kapali: {str(e)[:40]}"
            await asyncio.sleep(5)     # reconnect backoff
        finally:
            if ping_task:
                ping_task.cancel()


async def akis_loop(semboller=("BTCUSDT", "ETHUSDT"),
                    borsalar=("binance_perp", "bybit", "okx", "coinbase")):
    """
    SÜREKLİ WS akış toplayıcı (main.py startup task). Her borsa için ayrı görev.
    websockets yoksa (sandbox) sessizce pasif kalır. Ctrl+C / iptal güvenli.
    """
    print(f"[WS Akış] başlatılıyor: {borsalar} × {semboller}", flush=True)
    for b in borsalar:
        asyncio.create_task(_borsa_dongu(b, semboller))


if __name__ == "__main__":
    async def _t():
        await akis_loop()
        await asyncio.sleep(20)
        print("durum:", json.dumps(durum(), ensure_ascii=False)[:400])
    asyncio.run(_t())
