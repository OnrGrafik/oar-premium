"""
veri_dogrula.py — Canlı Veri Çekme Doğrulama Aracı (TEŞHİS)
═══════════════════════════════════════════════════════════════════════════
AMAÇ: Binance, Deribit ve Kiyotaka'dan BTC/ETH verisinin GERÇEKTEN çekilip
çekilmediğini, ve ne kadar geçmişe gidilebildiğini KANITLAR.

Gerçek kod yollarını kullanır (exchange_client / options_engine / kiyotaka_engine)
— yani "test geçiyor ama prod farklı" durumu olmaz; canlı sistemle aynı çağrılar.

ÇALIŞTIRMA (Railway shell ya da lokal — ağ erişimi olan yerde):
    python veri_dogrula.py
Kiyotaka için KIYOTAKA_API_KEY env'i gerekir (yoksa o bölüm atlanır).

NOT: Bu araç bir borsaya çıkamayan sandbox'ta 'BAŞARISIZ' der — bu beklenir;
canlı ortamda (Railway) çalıştır.
"""
import asyncio
import os
from datetime import datetime, timezone


def _ts(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _satir(ad, ok, detay):
    isaret = "✅" if ok else "❌"
    print(f"{isaret} {ad:28s} {detay}")


async def dogrula_binance():
    print("\n── BINANCE (exchange_client.klines) ──────────────────────────")
    from exchange_client import klines
    for sym in ("BTCUSDT", "ETHUSDT"):
        for futures, etiket in ((True, "futures"), (False, "spot")):
            try:
                # Son mum
                son = await klines(sym, "1d", 2, futures=futures)
                # En eski mum (startTime=0 → borsa en eski veriyi döndürür)
                ilk = await klines(sym, "1d", 1, futures=futures, start_ms=0)
                if not son or not ilk:
                    _satir(f"{sym} {etiket}", False, "BOŞ yanıt")
                    continue
                son_ts, son_close = son[-1][0], son[-1][4]
                ilk_ts = ilk[0][0]
                _satir(f"{sym} {etiket}", True,
                       f"son close ${son_close:,.2f} @ {_ts(son_ts)} | "
                       f"en eski veri: {_ts(ilk_ts)}")
            except Exception as e:
                _satir(f"{sym} {etiket}", False, f"HATA: {str(e)[:70]}")


async def dogrula_deribit():
    print("\n── DERIBIT (options_engine.gex_ozet) ─────────────────────────")
    from options_engine import gex_ozet
    for cur in ("BTC", "ETH"):
        try:
            g = await gex_ozet(cur)
            if g.get("error") or g.get("hata"):
                _satir(cur, False, str(g.get("error") or g.get("hata"))[:70])
                continue
            spot = g.get("spot")
            rejim = g.get("gamma_rejim", "—")
            zg = g.get("zero_gamma")
            _satir(cur, bool(spot), f"spot ${spot:,.0f} | gamma_rejim {rejim} | "
                                    f"zero_gamma {zg}")
        except Exception as e:
            _satir(cur, False, f"HATA: {str(e)[:70]}")


async def dogrula_kiyotaka():
    print("\n── KIYOTAKA (kiyotaka_engine.canli_ozet) ─────────────────────")
    key = os.environ.get("KIYOTAKA_API_KEY", "")
    if not key:
        _satir("KIYOTAKA_API_KEY", False, "env yok — bu bölüm atlandı")
        return
    from kiyotaka_engine import canli_ozet
    for sym in ("BTCUSDT", "ETHUSDT"):
        try:
            o = await canli_ozet(sym, key)
            err = (o.get("errors") or {})
            if err.get("vpfr") or err.get("tpo"):
                _satir(sym, False, f"vpfr={err.get('vpfr')} tpo={err.get('tpo')}")
                continue
            _satir(sym, True, f"VPFR POC {o.get('vpfr_poc')} | TPO POC {o.get('tpo_poc')}")
        except Exception as e:
            _satir(sym, False, f"HATA: {str(e)[:70]}")


async def kiyotaka_gecmis_derinligi():
    """Kiyotaka geçmiş derinliği: kaç gün geriye veri dönüyor (ikili arama)."""
    key = os.environ.get("KIYOTAKA_API_KEY", "")
    if not key:
        return
    print("\n── KIYOTAKA geçmiş derinliği (BTCUSDT VPFR) ───────────────────")
    from kiyotaka_engine import get_volume_profile
    from datetime import timedelta
    bugun = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    # 1, 7, 30, 90, 180, 365, 730 gün geriye dene
    for geri in (1, 7, 30, 90, 180, 365, 730):
        gun = bugun - timedelta(days=geri)
        ds = int(gun.timestamp())
        try:
            r = await get_volume_profile("BTCUSDT", ds, 86400, key)
            ok = not r.get("error")
            _satir(f"{geri} gün önce ({gun.date()})", ok,
                   f"POC {r.get('poc')}" if ok else r.get("error"))
        except Exception as e:
            _satir(f"{geri} gün önce", False, f"HATA: {str(e)[:50]}")


async def main():
    print("═══ OAR VERİ ÇEKME DOĞRULAMA ═══")
    print(f"Zaman: {datetime.now(timezone.utc).isoformat()}")
    await dogrula_binance()
    await dogrula_deribit()
    await dogrula_kiyotaka()
    await kiyotaka_gecmis_derinligi()
    print("\nBitti. ❌ satırları o kaynaktan veri ÇEKİLEMEDİĞİNİ gösterir.")


if __name__ == "__main__":
    asyncio.run(main())
