"""
OAR Altcoin Sistem — OAR Premium
═══════════════════════════════════════════════════════════════════════════════
Top-100 coin (BTC, ETH ve stablecoinler HARİÇ) → tüm altcoinleri OAR sistemine
göre CANLI paper-trade eder. OAR-CORE confluence sinyali gelen coinde pozisyon
açar (GİRİŞ), açıkken DEVAM, TP/SL vurunca KAPANIŞ. Her giriş/kapanış Telegram
grubuna bildirilir; HAFTALIK hafıza tutulur ve hafta sonu K/Z özeti gönderilir.

Tek tablo: static/live.html "OAR ALTCOIN SİSTEM". Backend bu modül.

Disiplin oar_paper_box ile aynı (saf fonksiyonlar oradan): OAR-CORE giriş,
TP=Asia POC, SL=süpürülen ekstrem, fee %0.13, time-stop, 5x kaldıraç (gösterim).
"""
import asyncio
import json
from datetime import datetime, timezone

from oar_paper_box import (_net_fiyat_pct, _ac_karar, _kapanis_kontrol,
                           _sure_saat, _son_hl, KALDIRAC, MAX_SAAT)

# Telegram hedefi: https://t.me/c/2142274543/1294/2901 → chat -100..., thread 1294
TG_CHAT = "-1002142274543"
TG_THREAD = "1294"
HARIC = {"BTCUSDT", "ETHUSDT"}     # stablecoinler _semboller_top'ta zaten ATLA'da
TARANACAK = 100                    # Top-100 (haricler çıkınca ~95 altcoin)
CHUNK = 6                          # paralellik (512MB Railway)


def _dosya():
    from data_ingest import hist_dir
    return hist_dir() / "oar_altcoin_sistem.json"


def _hafta_etiket(dt=None):
    dt = dt or datetime.now(timezone.utc)
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def _bos():
    return {"hafta": _hafta_etiket(), "acik": {}, "islemler": []}


def _yukle():
    yol = _dosya()
    if yol.exists():
        try:
            with open(yol, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return _bos()


def _kaydet(d):
    yol = _dosya()
    yol.parent.mkdir(parents=True, exist_ok=True)
    with open(yol, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


async def _tg(metin):
    try:
        from main import _telegram_gonder
        await _telegram_gonder(metin, thread_id=TG_THREAD, chat_id=TG_CHAT)
    except Exception as e:
        print(f"[OAR-Altcoin] telegram hatası: {str(e)[:60]}")


def _kisa(sembol):
    return sembol.replace("USDT", "")


async def _altcoin_listesi(n=TARANACAK):
    """Top-N coin → Binance USDT sembolleri, BTC/ETH/stablecoin hariç."""
    try:
        from main import get_coingecko_markets
        from komuta_merkezi import _semboller_top
        piyasa = await get_coingecko_markets(n + 15)
        coinler = _semboller_top(piyasa, n)
        return [c["sembol"] for c in coinler if c["sembol"] not in HARIC]
    except Exception as e:
        print(f"[OAR-Altcoin] coin listesi hatası: {str(e)[:60]}")
        return []


def _haftalik_ozet_metin(d, hafta):
    bu = [t for t in d["islemler"] if t.get("hafta") == hafta]
    if not bu:
        return None
    kazanan = [t for t in bu if t["equity_pct"] > 0]
    net = round(sum(t["equity_pct"] for t in bu), 2)
    wr = round(100 * len(kazanan) / len(bu), 1) if bu else 0
    return (f"📊 OAR ALTCOIN SİSTEM — Haftalık Özet ({hafta})\n"
            f"İşlem: {len(bu)} · Kazanan: {len(kazanan)} · WR %{wr}\n"
            f"Net (5x toplam): %{net}\n"
            f"En iyi: " + ", ".join(
                f"{_kisa(t['sembol'])} %{t['equity_pct']:+.1f}"
                for t in sorted(bu, key=lambda x: x["equity_pct"], reverse=True)[:3]))


async def _hafta_kontrol(d):
    """Hafta değiştiyse geçen haftanın K/Z özetini Telegram'a gönder, hafta etiketini güncelle."""
    su_an = _hafta_etiket()
    if d.get("hafta") != su_an:
        ozet = _haftalik_ozet_metin(d, d.get("hafta"))
        if ozet:
            await _tg(ozet)
        d["hafta"] = su_an
    return d


async def _ac(d, sembol, karar):
    karar = dict(karar)
    karar["acilis"] = datetime.now(timezone.utc).isoformat()
    d["acik"][sembol] = karar
    await _tg(f"🟢 OAR ALTCOIN GİRİŞ — {_kisa(sembol)} {karar['yon']}\n"
              f"Giriş: {karar['giris']} · TP: {karar['tp']} · SL: {karar['sl']}\n"
              f"OAR-CORE confluence (poc+absorpsiyon+reclaim)")
    print(f"[OAR-Altcoin] GİRİŞ {sembol} {karar['yon']} @ {karar['giris']}")


async def _kapat(d, sembol, cikis, sonuc):
    poz = d["acik"].pop(sembol)
    net = _net_fiyat_pct(poz["yon"], poz["giris"], cikis)
    equity_pct = round(KALDIRAC * net, 3)
    d["islemler"].append({
        "sembol": sembol, "yon": poz["yon"], "giris": poz["giris"], "cikis": round(cikis, 2),
        "tp": poz["tp"], "sl": poz["sl"], "sonuc": sonuc, "net_fiyat_pct": net,
        "equity_pct": equity_pct, "kaldirac": KALDIRAC,
        "acilis": poz["acilis"], "kapanis": datetime.now(timezone.utc).isoformat(),
        "hafta": _hafta_etiket(),
    })
    emoji = "✅" if equity_pct > 0 else "❌"
    await _tg(f"🔴 OAR ALTCOIN KAPANIŞ — {_kisa(sembol)} {poz['yon']}\n"
              f"{sonuc} @ {round(cikis,2)} · Sonuç {emoji} %{equity_pct:+.2f} (5x)")
    print(f"[OAR-Altcoin] KAPANIŞ {sembol} {sonuc} %{equity_pct}")


async def tik(d=None):
    """Bir tarama adımı: açıkları kontrol/kapat, yeni OAR-CORE sinyali olan altcoinlerde aç."""
    from oar_session_agent import oar_analiz
    d = d if d is not None else _yukle()
    await _hafta_kontrol(d)

    # 1) Açık pozisyonları kontrol et (TP/SL/time-stop)
    for sembol in list(d["acik"].keys()):
        poz = d["acik"][sembol]
        try:
            high, low = await _son_hl(sembol)
        except Exception:
            continue
        kap = _kapanis_kontrol(poz, high, low)
        if kap:
            await _kapat(d, sembol, kap[1], kap[0])
        elif _sure_saat(poz["acilis"]) >= MAX_SAAT:
            await _kapat(d, sembol, (high + low) / 2, "TIME_STOP")

    # 2) Yeni sinyal taraması (açık olmayan altcoinler)
    semboller = [s for s in await _altcoin_listesi() if s not in d["acik"]]
    for i in range(0, len(semboller), CHUNK):
        grup = semboller[i:i + CHUNK]
        sonuc = await asyncio.gather(*[oar_analiz(s) for s in grup],
                                     return_exceptions=True)
        for s, analiz in zip(grup, sonuc):
            if not isinstance(analiz, dict):
                continue
            karar = _ac_karar(analiz)
            if karar:
                await _ac(d, s, karar)
        await asyncio.sleep(1)   # API + bellek nefesi

    _kaydet(d)
    return d


async def dongu(interval: int = 600):
    """Arka plan döngüsü (main.py startup). 10 dakikada bir tarama."""
    await asyncio.sleep(120)   # komuta/diğer loop'lardan sonra
    while True:
        try:
            await tik()
        except Exception as e:
            print(f"[OAR-Altcoin] döngü hatası: {str(e)[:80]}")
        await asyncio.sleep(interval)


def durum_ozet() -> dict:
    """UI tablosu: açık (DEVAM) + bu haftanın kapananları (KAPANIŞ) + haftalık stat."""
    d = _yukle()
    hafta = _hafta_etiket()
    bu = [t for t in d.get("islemler", []) if t.get("hafta") == hafta]
    kazanan = [t for t in bu if t["equity_pct"] > 0]
    acik = [{"sembol": s, "durum": "DEVAM", **p} for s, p in d.get("acik", {}).items()]
    kapali = [{**t, "durum": "KAPANIŞ"} for t in reversed(bu)][:60]
    return {
        "hafta": hafta,
        "acik_sayisi": len(acik),
        "bu_hafta_islem": len(bu),
        "bu_hafta_kazanan": len(kazanan),
        "bu_hafta_wr": round(100 * len(kazanan) / len(bu), 1) if bu else 0,
        "bu_hafta_net_pct": round(sum(t["equity_pct"] for t in bu), 2),
        "acik_pozisyonlar": acik,
        "kapananlar": kapali,
    }


if __name__ == "__main__":
    print(json.dumps(durum_ozet(), ensure_ascii=False, indent=2))
