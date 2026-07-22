"""
oar_canli_footprint.py — CANLI GERÇEK FOOTPRINT (Faz 1+2, kullanıcı onaylı)
════════════════════════════════════════════════════════════════════════════════════
5e/canlı-veri açığı: canlı FADE girişi klines-PROXY'siydi (asia_poc=(H+L)/2 ORTANCA,
absorpsiyon=15m hacim-z; gerçek aggressor delta / hacim-profili POC YOK). Bu modül
gerçek footprint'i CANLIDA hesaplar.

KRİTİK VERİ KAYNAĞI (aggTrades'e GEREK YOK): Binance 1m klines yanıtı `takerBuyBase`
alanı taşır → per-dakika aggressor delta = 2·taker_buy − volume. Bu, backtest'in
aggTrades `is_buyer_maker`'dan hesapladığı per-dk delta ile BİREBİR AYNI (footprint_engine
mantığı). Tek REST isteğiyle tüm gün gelir (aggTrades sayfalama derdi YOK, proxy-uyumlu,
restart-dayanıklı — 00:00'dan yeniden çek).

HESAPLANAN (backtest _gun_hazirla ile hizalı, NO-LOOKAHEAD = yalnız 00:00→şimdi):
  • poc            : gün hacim-profili POC (1m tipik-fiyat×hacim bin argmax) — ORTANCA DEĞİL
  • cvd_map        : kümülatif delta (dk)          • delta_map: ham per-dk delta
  • vol_ort/vol_std: per-dk hacim z için            • delta_abs_esik: |delta| 80. pct (balina)

FAZ 1 (güvenli): footprint'i hesaplar + proxy'yle KIYASLAR (karsilastir), kararı DEĞİŞTİRMEZ.
FAZ 2 (#8, onaylı): oar_analiz gerçek POC + gerçek delta-absorpsiyon/kalıcılık kullanır;
  footprint gelmezse OTOMATİK proxy'ye düşer (fail-safe — canlıyı bozmaz).
"""
import asyncio
from datetime import datetime, timezone

_CACHE = {}          # {(sembol): {"gun": "YYYY-MM-DD", "ts": epoch_s, "fp": {...}}}
_CACHE_TTL_S = 60    # 60 sn tazelik (1m bar granülerliğiyle uyumlu, REST'i yormaz)


def _gun_basi_ms(simdi=None) -> int:
    """Bugünün 00:00:00 UTC epoch-ms (Asia açılışı = footprint gün başı)."""
    simdi = simdi or datetime.now(timezone.utc)
    gb = simdi.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(gb.timestamp() * 1000)


def _pbin(px: float) -> float:
    """POC hacim-profili bini — backtest ile aynı: 4 anlamlı hane."""
    if px <= 0:
        return px
    return float(f"{px:.4g}")


def _footprint_hesapla(rows: list) -> dict:
    """
    1m klines_taker satırlarından ([ts,o,h,l,c,vol,taker_buy]) footprint çıkar.
    NO-LOOKAHEAD: yalnız verilen (00:00→şimdi) barlar. Backtest _gun_hazirla ile hizalı.
    """
    if not rows:
        return {}
    delta_map = {}; vol_map = {}; poc_px = {}
    for ts, o, h, l, c, vol, taker in rows:
        dk = int(ts // 60000)                      # dakika-index (backtest 'dk' ile aynı ölçek)
        delta = 2.0 * taker - vol                  # net aggressor delta (2·alış − toplam)
        delta_map[dk] = delta_map.get(dk, 0.0) + delta
        vol_map[dk] = vol_map.get(dk, 0.0) + vol
        tip = (h + l + c) / 3.0                     # tipik fiyat (bar hacmini buraya ata)
        pb = _pbin(tip)
        poc_px[pb] = poc_px.get(pb, 0.0) + vol
    # CVD (kümülatif), POC (argmax), hacim istatistikleri, balina eşiği
    cvd_map = {}; kum = 0.0
    for dk in sorted(delta_map):
        kum += delta_map[dk]; cvd_map[dk] = kum
    voller = list(vol_map.values()) or [0.0]
    import statistics
    vol_ort = statistics.mean(voller)
    vol_std = (statistics.pstdev(voller) or 1.0)
    absd = sorted(abs(d) for d in delta_map.values())
    delta_abs_esik = absd[int(len(absd) * 0.8)] if absd else 0.0
    poc = max(poc_px, key=poc_px.get) if poc_px else None
    return {
        "poc": poc, "cvd_map": cvd_map, "delta_map": delta_map, "vol_map": vol_map,
        "vol_ort": vol_ort, "vol_std": vol_std, "delta_abs_esik": delta_abs_esik,
        "cvd_son": (cvd_map[max(cvd_map)] if cvd_map else 0.0), "bar_sayisi": len(rows),
    }


async def footprint_al(sembol: str = "BTCUSDT") -> dict | None:
    """
    Bugünün (00:00 UTC→şimdi) gerçek footprint'i. 60 sn cache. Veri gelmezse None
    (çağıran proxy'ye düşer — canlı bozulmaz). Tek REST isteği (1m klines_taker).
    """
    from datetime import datetime as _dt, timezone as _tz
    bugun = _dt.now(_tz.utc).strftime("%Y-%m-%d")
    simdi_s = _dt.now(_tz.utc).timestamp()
    c = _CACHE.get(sembol)
    if c and c["gun"] == bugun and (simdi_s - c["ts"]) < _CACHE_TTL_S and c["fp"]:
        return c["fp"]
    try:
        from exchange_client import klines_taker
        # 00:00'dan şimdiye 1m (limit 1000 = 16.6s; Asia+London+NY penceresini kapsar).
        rows = await klines_taker(sembol, "1m", 1000, futures=False, start_ms=_gun_basi_ms())
    except Exception:
        return None
    fp = _footprint_hesapla(rows)
    if not fp:
        return None
    _CACHE[sembol] = {"gun": bugun, "ts": simdi_s, "fp": fp}
    return fp


async def asia_poc_gercek(sembol: str = "BTCUSDT") -> float | None:
    """Sadece gerçek POC (oar_analiz'in (H+L)/2 ortancasını değiştirir). Veri yoksa None."""
    fp = await footprint_al(sembol)
    return fp.get("poc") if fp else None


async def karsilastir(sembol: str, asia_high: float, asia_low: float) -> dict:
    """
    FAZ 1: gerçek footprint ile mevcut proxy'yi KIYASLA (kararı değiştirmeden).
    Proxy POC = (H+L)/2 ortanca; gerçek POC = hacim-profili argmax. Sapmayı raporlar.
    """
    fp = await footprint_al(sembol)
    if not fp:
        return {"sembol": sembol, "durum": "footprint_yok", "not": "1m taker verisi gelmedi → proxy sürer"}
    proxy_poc = (asia_high + asia_low) / 2 if (asia_high and asia_low) else None
    gercek_poc = fp.get("poc")
    sapma_pct = (abs(gercek_poc - proxy_poc) / proxy_poc * 100
                 if (gercek_poc and proxy_poc) else None)
    return {
        "sembol": sembol, "durum": "ok",
        "proxy_poc_ortanca": round(proxy_poc, 2) if proxy_poc else None,
        "gercek_poc_hacim": round(gercek_poc, 2) if gercek_poc else None,
        "poc_sapma_pct": round(sapma_pct, 3) if sapma_pct is not None else None,
        "cvd_gunluk": round(fp.get("cvd_son", 0.0), 2),
        "bar_sayisi": fp.get("bar_sayisi"),
        "not": "gerçek POC hacim-profilinden; proxy ortancaydı. Sapma büyükse Faz 2 kararı iyileştirir.",
    }


if __name__ == "__main__":
    async def _t():
        for s in ("BTCUSDT", "ETHUSDT"):
            fp = await footprint_al(s)
            if fp:
                print(f"{s}: POC {fp['poc']} · CVD {fp['cvd_son']:.0f} · barlar {fp['bar_sayisi']} "
                      f"· vol_ort {fp['vol_ort']:.1f} · balina_eşik {fp['delta_abs_esik']:.1f}")
            else:
                print(f"{s}: footprint yok (veri gelmedi)")
    asyncio.run(_t())
