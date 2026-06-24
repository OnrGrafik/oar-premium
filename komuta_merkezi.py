"""
KOMUTA MERKEZİ — Top-20 Coin Güvenilirlik Skor Motoru
═══════════════════════════════════════════════════════════════════
Mevcut confidence_engine.confidence_karar() 0-100 konfidans skorunu
4 güvenilirlik kutusuna eşler ve top-20 coini tarar:

    Az Güvenilir    0-45
    Orta Güvenilir  46-65
    Güvenli         66-84
    Yüksek Güvenilir 85-100

Bir coin "Güvenli" → "Yüksek Güvenilir" kutusuna geçtiği AN Telegram
bildirimi atılır (coin başına debounce ile spam önlenir).

Durum: data/komuta_durum.json  (coin → {onceki_kutu, son_telegram_iso})
Cache: data/komuta_son.json     (UI hızlı okusun diye son tarama)
"""
import os, asyncio, json
from pathlib import Path
from datetime import datetime, timezone, timedelta

DATA_DIR     = Path(os.environ.get("DATA_DIR", "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DURUM_FILE   = DATA_DIR / "komuta_durum.json"
SON_FILE     = DATA_DIR / "komuta_son.json"

# Kutu eşikleri (alt, üst) — dahil
KUTU_ESIKLERI = {
    "az":      (0, 45),
    "orta":    (46, 65),
    "guvenli": (66, 84),
    "yuksek":  (85, 100),
}
KUTU_ETIKET = {
    "az":      "Az Güvenilir",
    "orta":    "Orta Güvenilir",
    "guvenli": "Güvenli",
    "yuksek":  "Yüksek Güvenilir",
}
# Binance vadeli işlemde olmayan / stablecoin sembolleri ele
ATLA = {"USDT", "USDC", "DAI", "BUSD", "TUSD", "FDUSD", "WBTC", "STETH", "WETH", "WSTETH", "WEETH"}

TELEGRAM_DEBOUNCE_DK = 30
CHUNK = 2  # rate-limit + 512MB OOM koruması (tepe belleği düşük tut)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(path, default):
    try:
        if Path(path).exists():
            return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _save(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def skoru_kutula(konfidans: float) -> str:
    """0-100 konfidansı kutu anahtarına çevir."""
    k = max(0, min(100, konfidans or 0))
    for ad, (alt, ust) in KUTU_ESIKLERI.items():
        if alt <= k <= ust:
            return ad
    return "az"


async def _coin_skoru(sembol: str) -> dict | None:
    """Tek sembol için confidence_karar → kompakt kart verisi."""
    try:
        from confidence_engine import confidence_karar
        k = await confidence_karar(sembol)
    except Exception as e:
        return {"sembol": sembol, "hata": str(e)[:80]}

    konf = k.get("konfidans", 0)
    # En güçlü 2 agent nedenini çıkar (mutlak skora göre)
    agentlar = k.get("agent_skorlar", {})
    en_guclu = sorted(
        agentlar.items(), key=lambda kv: abs(kv[1].get("skor", 0)), reverse=True
    )[:2]
    nedenler = [f"{ad.upper()}: {v.get('aciklama','')[:70]}" for ad, v in en_guclu if v.get("aciklama")]

    return {
        "sembol": sembol,
        "konfidans": konf,
        "kutu": skoru_kutula(konf),
        "yon": k.get("karar", "NO_TRADE"),
        "conviction": k.get("conviction", "LOW"),
        "rejim": (k.get("rejim", {}) or {}).get("rejim", "—"),
        "nedenler": nedenler,
    }


def _semboller_top(piyasa: list, n: int) -> list:
    """CoinGecko market listesinden Binance USDT sembolleri üret."""
    out = []
    for c in piyasa:
        sym = (c.get("symbol") or "").upper()
        if not sym or sym in ATLA:
            continue
        binance_sym = f"{sym}USDT"
        out.append({
            "sembol": binance_sym,
            "fiyat": c.get("current_price"),
            "degisim_24h": c.get("price_change_percentage_24h"),
            "rank": c.get("market_cap_rank"),
        })
        if len(out) >= n:
            break
    return out


async def komuta_taramasi(n: int = 20, telegram: bool = True) -> dict:
    """
    Top-N coini tara, 4 kutuya dağıt, Güvenli→Yüksek geçişlerinde Telegram at.
    """
    try:
        from main import get_coingecko_markets
        piyasa = await get_coingecko_markets(max(n + 8, 28))  # eleme payı
    except Exception as e:
        return {"hata": f"Piyasa verisi alınamadı: {str(e)[:80]}", "tarih": _now()}

    coinler = _semboller_top(piyasa, n)
    meta = {c["sembol"]: c for c in coinler}

    # Skorları küçük chunk'lar halinde hesapla — her chunk sonrası belleği boşalt
    # (512MB instance: confidence_karar coin başına 7 agent + DataFrame yükler,
    #  tepe belleği düşük tutmak için CHUNK küçük + gc.collect + chunk arası nefes).
    import gc
    skorlar = []
    for i in range(0, len(coinler), CHUNK):
        grup = coinler[i:i + CHUNK]
        res = await asyncio.gather(*[_coin_skoru(c["sembol"]) for c in grup],
                                   return_exceptions=True)
        for r in res:
            if isinstance(r, dict) and "konfidans" in r:
                m = meta.get(r["sembol"], {})
                r["fiyat"] = m.get("fiyat")
                r["degisim_24h"] = m.get("degisim_24h")
                r["rank"] = m.get("rank")
                skorlar.append(r)
        del res, grup
        gc.collect()
        await asyncio.sleep(1)  # chunk arası nefes — API + bellek

    # Kutulara dağıt + skora göre azalan sırala
    kutular = {ad: [] for ad in KUTU_ESIKLERI}
    for s in sorted(skorlar, key=lambda x: x.get("konfidans", 0), reverse=True):
        kutular[s["kutu"]].append(s)

    # Geçiş tespiti + Telegram
    gecisler = await _gecis_kontrol(skorlar, telegram)

    cikti = {
        "kutular": kutular,
        "kutu_etiket": KUTU_ETIKET,
        "esikler": KUTU_ESIKLERI,
        "gecisler": gecisler,
        "coin_sayisi": len(skorlar),
        "tarih": _now(),
    }
    _save(SON_FILE, cikti)
    return cikti


async def _gecis_kontrol(skorlar: list, telegram: bool) -> list:
    """
    'guvenli' → 'yuksek' geçişi yapan coinler için Telegram bildirimi.
    Debounce: aynı coin TELEGRAM_DEBOUNCE_DK içinde tekrar tetiklemez.
    """
    durum = _load(DURUM_FILE, {})
    simdi = datetime.now(timezone.utc)
    gecisler = []

    for s in skorlar:
        sym = s["sembol"]
        kutu = s["kutu"]
        onceki = durum.get(sym, {})
        onceki_kutu = onceki.get("onceki_kutu")

        yukseldi = onceki_kutu == "guvenli" and kutu == "yuksek"
        if yukseldi:
            son_tg = onceki.get("son_telegram_iso")
            debounce_ok = True
            if son_tg:
                try:
                    fark = (simdi - datetime.fromisoformat(son_tg)).total_seconds() / 60
                    debounce_ok = fark >= TELEGRAM_DEBOUNCE_DK
                except Exception:
                    pass
            if debounce_ok:
                gecisler.append(sym)
                if telegram:
                    await _telegram_bildir(s)
                    onceki["son_telegram_iso"] = simdi.isoformat()

        onceki["onceki_kutu"] = kutu
        durum[sym] = onceki

    _save(DURUM_FILE, durum)
    return gecisler


async def _telegram_bildir(s: dict) -> None:
    """Güvenli→Yüksek geçen coin için Telegram mesajı."""
    yon_emoji = {"LONG": "📈", "SHORT": "📉"}.get(s.get("yon"), "⚡")
    satirlar = [
        f"🚨 KOMUTA MERKEZİ — Yüksek Güvenilir Geçiş",
        f"{yon_emoji} {s['sembol']}  ·  {s.get('yon','?')}",
        f"Skor: {s.get('konfidans',0)}/100  ·  Rejim: {s.get('rejim','—')}",
    ]
    for n in s.get("nedenler", [])[:3]:
        satirlar.append(f"• {n}")
    metin = "\n".join(satirlar)
    try:
        from main import _telegram_gonder
        await _telegram_gonder(metin)
    except Exception:
        pass


def son_tarama() -> dict:
    """UI için son kaydedilmiş taramayı döndür."""
    return _load(SON_FILE, {"durum": "henuz_tarama_yok"})


# ── Periyodik Döngü ────────────────────────────────────────────────
async def komuta_loop(aralik_sn: int = 900):
    """
    main.py startup'ta create_task ile başlatılır.
    512MB OOM önlemi: startup spike'ı geçtikten çok sonra başla (10 dk),
    aralık 15 dk, her tur sonrası gc.collect.
    """
    import gc
    await asyncio.sleep(600)  # diğer ağır loop'lar yerleşsin, startup spike'ı geçsin
    while True:
        try:
            await komuta_taramasi(20)
            print("[KomutaMerkezi] Tarama tamamlandı")
        except Exception as e:
            print(f"[KomutaMerkezi] Hata: {str(e)[:80]}")
        finally:
            gc.collect()
        await asyncio.sleep(aralik_sn)


if __name__ == "__main__":
    print(json.dumps(asyncio.run(komuta_taramasi(20, telegram=False)), ensure_ascii=False, indent=2)[:2000])
