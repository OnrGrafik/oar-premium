"""
oar_orderbook.py — CANLI ORDER-BOOK (L2) TOPLAYICI + metrikler (ileri-test veri seti)
════════════════════════════════════════════════════════════════════════════════════
Kullanıcı isteği: bid/ask ratio, imbalance, true orderbook pressure. GEÇMİŞ L2 verisi YOK
(Binance ücretsiz dump vermez) → geriye-dönük backtest İMKÂNSIZ. Çözüm: BUGÜNDEN itibaren
canlı snapshot toplayıp İLERİ-VERİ seti kur → yeterli örnek birikince aynı metodoloji
(walk-forward OOS + DSR serap testi) uygulanır.

METRİKLER (ilk N seviye):
  bid_ask_ratio = bid_hacmi / ask_hacmi
  imbalance     = (bid − ask) / (bid + ask)  ∈ [−1, +1]
  true_pressure = MESAFE-AĞIRLIKLI dengesizlik (yakın seviyeler ağır → uzaktaki spoof
                  emirleri az etkiler; "true" = touch'a yakın gerçek likidite)
  spread_pct    = (ask0 − bid0) / mid × 100

DEPOLAMA: kalıcı disk (DATA_DIR/orderbook, Railway volume → restart'ta kaybolmaz) JSONL.
UYARI (dürüst): (1) order-book saniye-altı hareket eder, snapshot dinamiği kaçırır;
(2) spoofing imbalance'ı yanıltır (true_pressure kısmen filtreler); (3) şampiyon SAATLİK
fade yapar, order-book saniyelik → muhtemelen STANDALONE sinyal değil, GİRİŞ-ANI CONFIRM.
Sonuç ancak DSR≥0.95 serap testinden geçerse canlıya alınır (5p kuralı).
"""
import os
import json
import asyncio
from pathlib import Path
from datetime import datetime, timezone


def _veri_dir() -> Path:
    kok = os.environ.get("DATA_DIR") or os.environ.get("OAR_HIST_DIR") or str(Path(__file__).resolve().parent)
    d = Path(kok) / "orderbook"
    d.mkdir(parents=True, exist_ok=True)
    return d


def metrikler(bids: list, asks: list, seviye: int = 20) -> dict:
    """L2 snapshot'tan order-book metrikleri. bids/asks = [[fiyat, miktar]...]."""
    b = bids[:seviye]; a = asks[:seviye]
    if not b or not a:
        return {}
    bid0 = b[0][0]; ask0 = a[0][0]; mid = (bid0 + ask0) / 2
    if mid <= 0:
        return {}
    bid_vol = sum(q for _, q in b); ask_vol = sum(q for _, q in a)
    # mesafe-ağırlıklı (yakın seviye ağır): w = 1 / (1 + |fiyat−mid|/mid·100)
    def _w(px):
        return 1.0 / (1.0 + abs(px - mid) / mid * 100.0)
    pb = sum(q * _w(p) for p, q in b); pa = sum(q * _w(p) for p, q in a)
    tot = bid_vol + ask_vol
    ptot = pb + pa
    return {
        "mid": round(mid, 2), "spread_pct": round((ask0 - bid0) / mid * 100, 4),
        "bid_ask_ratio": round(bid_vol / ask_vol, 4) if ask_vol > 0 else None,
        "imbalance": round((bid_vol - ask_vol) / tot, 4) if tot > 0 else None,
        "true_pressure": round((pb - pa) / ptot, 4) if ptot > 0 else None,
        "bid_vol": round(bid_vol, 3), "ask_vol": round(ask_vol, 3), "seviye": seviye,
    }


async def snapshot(sembol: str, seviye: int = 20) -> dict | None:
    """Tek order-book snapshot + metrikler. Veri gelmezse None (httpx yoksa da None)."""
    try:
        from exchange_client import depth
        ob = await depth(sembol, limit=max(seviye, 50), futures=False)
    except Exception:
        return None
    m = metrikler(ob.get("bids", []), ob.get("asks", []), seviye)
    if not m:
        return None
    m["sembol"] = sembol
    m["ts"] = datetime.now(timezone.utc).isoformat()
    return m


def _kaydet(m: dict):
    """Snapshot'ı kalıcı JSONL'e ekle (sembol+gün başına dosya)."""
    if not m:
        return
    gun = m["ts"][:10]
    yol = _veri_dir() / f"{m['sembol']}_{gun}.jsonl"
    with open(yol, "a", encoding="utf-8") as f:
        f.write(json.dumps(m, ensure_ascii=False) + "\n")


async def olay_snapshot(sembol: str, olay: str, seviye: int = 20):
    """
    OAR OLAYINDA (ör. fade girişi) order-book durumunu etiketleyip kaydet → sonra
    'imbalance girişte fade'i öngörüyor mu' ileri-testi için. olay = 'fade_giris' vb.
    """
    m = await snapshot(sembol, seviye)
    if m:
        m["olay"] = olay
        _kaydet(m)
    return m


_SON = {}   # {sembol: son metrik} — endpoint/lider için


async def topla_loop(semboller=("BTCUSDT", "ETHUSDT"), aralik_s: int = 60, seviye: int = 20):
    """
    SÜREKLİ toplayıcı (main.py startup task): her aralik_s'de snapshot al + kaydet.
    İleri-veri seti burada birikir. Ctrl+C / iptal güvenli. httpx yoksa sessiz döner.
    """
    print(f"[OrderBook] toplayıcı başladı: {semboller} her {aralik_s}s → {_veri_dir()}", flush=True)
    while True:
        for s in semboller:
            try:
                m = await snapshot(s, seviye)
                if m:
                    _kaydet(m); _SON[s] = m
            except Exception as e:
                print(f"[OrderBook] {s} snapshot hata: {str(e)[:60]}", flush=True)
        await asyncio.sleep(aralik_s)


def durum() -> dict:
    """Son snapshot metrikleri (endpoint/lider bağlamı)."""
    return {"aciklama": "Canlı order-book metrikleri (ileri-veri toplanıyor; backtest için "
                        "yeterli örnek birikince DSR serap testi uygulanır).",
            "son": _SON, "veri_dizini": str(_veri_dir())}


if __name__ == "__main__":
    async def _t():
        for s in ("BTCUSDT", "ETHUSDT"):
            m = await snapshot(s)
            print(s, m if m else "veri yok (httpx? / ağ)")
    asyncio.run(_t())
