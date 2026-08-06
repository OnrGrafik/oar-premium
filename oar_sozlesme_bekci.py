"""
oar_sozlesme_bekci.py — SÖZLEŞME BEKÇİSİ (ajan bağımlılıkları değişince UYAR)
════════════════════════════════════════════════════════════════════════════════════
Kullanıcı isteği: "kod değişince agent sistemi Telegram'a bildirim atsın, güncelleme
uyarısı versin — ya da değişen sisteme uyum sağlayan yapıda kodlayalım."

TASARIM KARARI (bilinçli, kullanıcıya gerekçesi sunuldu):
  ❌ TAM OTOMATİK UYUM YOK. Sebep: canlı işlem sisteminde sessiz davranış değişimi en
     tehlikeli arıza sınıfıdır (bu oturumda yaşananlar: Kiyotaka ölünce footprint boş,
     FRED ölünce makro None, silinmiş botların bayat kaydı lider raporuna sızması).
     Ayrıca ANAYASA #8 (şampiyon onaysız değişmez) + §5s kanıtı (kendini yeniden-optimize
     eden sistem PF 3.05 → 1.31 çöküyor, maxDD %5 → %36).
  ✅ TESPİT + BİLDİRİM + KAPI. Bekçi ajanların bağımlı olduğu VERİ SÖZLEŞMELERİNİ izler;
     kırılınca/değişince Telegram'a "ne değişti + ne yapmalı" yazar. Otomatik uyum YALNIZ
     kanıtlı fail-safe olan yerde devrededir (ör. footprint Kiyotaka→Binance yedeği) ve
     bekçi bunu da "yedeğe düşüldü" diye BİLDİRİR (sessiz kalmaz).

İZLENEN SÖZLEŞMELER (hepsi gerçek arızalardan türetildi):
  1. Binance L/S alan adları (longAccount)      → whale/retail confirm'in temeli
  2. Binance klines taker alanı (index 9)       → footprint delta'sının temeli
  3. KIYOTAKA_API_KEY                            → footprint tam çözünürlük (yoksa yedek)
  4. FRED_API_KEY                                → makro göstergeler
  5. ŞAMPİYON PORTFÖY bütünlüğü                  → kanıtlı bloklar sessizce EZİLDİ Mİ
     (CLAUDE.md §5p/§5r: oar_coklu_sampiyon bunu İKİ KEZ serap combo'yla ezdi!)
  6. Serap kapısı (DSR≥0.95)                     → kanıt kapısının okunabilirliği
  7. WRD formülü                                 → canlı confirm eski formüle dönmüş mü
"""
import os
import json
import asyncio
from pathlib import Path
from datetime import datetime, timezone

_DATA_DIR = Path(os.environ.get("DATA_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
                 or ("/var/data" if Path("/var/data").exists() else "data"))
_DATA_DIR.mkdir(parents=True, exist_ok=True)
DURUM_FILE = _DATA_DIR / "sozlesme_durum.json"

KONTROL_ARALIK_S = 6 * 3600      # 6 saatte bir (sözleşmeler sık değişmez)

# KANITLI şampiyon blokları (CLAUDE.md §3 — serap testli DSR 1.0). Portföy json bundan
# SAPARSA sessiz ezilme olmuştur → kritik alarm.
KANITLI_BLOKLAR = {
    "ekstrem_donus_fade":  ["poc_taraf", "footprint_absorpsiyon", "footprint_kalicilik"],
    "kirilim_devam_trend": ["poc_taraf", "footprint_trapped", "gun_bias_uyum"],
}


def _now():
    return datetime.now(timezone.utc)


def _sonuc(ad, durum, detay, imza="", kritik=False):
    """durum: ok | kirik | yedek | erisilemedi"""
    return {"ad": ad, "durum": durum, "detay": detay, "imza": imza or durum, "kritik": kritik}


# ── 1+2. Binance alan sözleşmeleri ──────────────────────────────────────────────
async def k_binance_ls():
    """whale/retail confirm'in temeli: longAccount alanı duruyor mu?"""
    try:
        import httpx
        FAPI = "https://fapi.binance.com"
        async with httpx.AsyncClient(timeout=10) as cl:
            rg = await cl.get(f"{FAPI}/futures/data/globalLongShortAccountRatio",
                              params={"symbol": "BTCUSDT", "period": "5m", "limit": 1})
            rt = await cl.get(f"{FAPI}/futures/data/topLongShortPositionRatio",
                              params={"symbol": "BTCUSDT", "period": "5m", "limit": 1})
        g, t = rg.json(), rt.json()
        if not (isinstance(g, list) and g and isinstance(t, list) and t):
            return _sonuc("binance_ls", "erisilemedi", "L/S uçları boş döndü")
        eksik = [k for k, d in (("global", g[-1]), ("top", t[-1])) if "longAccount" not in d]
        if eksik:
            return _sonuc("binance_ls", "kirik",
                          f"'longAccount' alanı YOK ({', '.join(eksik)}) → whale/retail "
                          f"confirm ÖLÜR. Binance alan adını değiştirmiş olabilir.",
                          imza="alan_yok", kritik=True)
        return _sonuc("binance_ls", "ok", "longAccount alanları yerinde", imza="alan_var")
    except Exception as e:
        return _sonuc("binance_ls", "erisilemedi", f"ağ/httpx: {str(e)[:60]}")


async def k_binance_taker():
    """footprint delta'sının temeli: klines index 9 = takerBuyBase."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as cl:
            r = await cl.get("https://api.binance.com/api/v3/klines",
                             params={"symbol": "BTCUSDT", "interval": "1m", "limit": 1})
        d = r.json()
        if not (isinstance(d, list) and d and isinstance(d[0], list)):
            return _sonuc("binance_taker", "erisilemedi", "klines boş")
        n = len(d[0])
        if n < 10:
            return _sonuc("binance_taker", "kirik",
                          f"klines satırı {n} alan (≥10 bekleniyor) → index 9 takerBuyBase "
                          f"YOK. FOOTPRINT DELTA'SI ölür (2·taker_buy − hacim).",
                          imza=f"alan{n}", kritik=True)
        return _sonuc("binance_taker", "ok", f"klines {n} alan, index 9 mevcut", imza=f"alan{n}")
    except Exception as e:
        return _sonuc("binance_taker", "erisilemedi", f"ağ/httpx: {str(e)[:60]}")


# ── 3+4. Dış API anahtarları (sessiz ölüm kaynağı) ──────────────────────────────
def k_kiyotaka():
    if not os.environ.get("KIYOTAKA_API_KEY"):
        return _sonuc("kiyotaka", "yedek",
                      "KIYOTAKA_API_KEY TANIMSIZ → footprint tick çözünürlüğü YOK; "
                      "anahtarsız Binance 1m taker YEDEĞİ devrede (kademeler 1dk).",
                      imza="anahtar_yok")
    return _sonuc("kiyotaka", "ok", "anahtar tanımlı", imza="anahtar_var")


def k_fred():
    if not os.environ.get("FRED_API_KEY"):
        return _sonuc("fred", "kirik",
                      "FRED_API_KEY TANIMSIZ → makro göstergeleri (FedFaiz/CPI/İşsizlik/NFP) "
                      "okunamaz, hepsi None döner.", imza="anahtar_yok")
    return _sonuc("fred", "ok", "anahtar tanımlı", imza="anahtar_var")


# ── 5. ŞAMPİYON PORTFÖY BÜTÜNLÜĞÜ (sessiz ezilme İKİ KEZ yaşandı) ───────────────
def k_sampiyon_portfoy():
    yol = Path(__file__).resolve().parent / "oar_sampiyon_portfoy.json"
    try:
        d = json.loads(yol.read_text())
    except Exception as e:
        return _sonuc("sampiyon_portfoy", "kirik", f"portföy okunamadı: {str(e)[:60]}",
                      imza="okunamadi", kritik=True)
    stiller = d.get("stiller") or d.get("portfoy") or []
    mevcut = {s.get("stil"): list(s.get("bloklar") or []) for s in stiller}
    sapan = []
    for stil, bloklar in KANITLI_BLOKLAR.items():
        if stil not in mevcut:
            sapan.append(f"{stil} KAYIP")
        elif sorted(mevcut[stil]) != sorted(bloklar):
            sapan.append(f"{stil}: {mevcut[stil]} ≠ kanıtlı {bloklar}")
    if sapan:
        return _sonuc("sampiyon_portfoy", "kirik",
                      "⛔ KANITLI ŞAMPİYON BLOKLARI DEĞİŞMİŞ (sessiz ezilme?): "
                      + " | ".join(sapan)
                      + " → `git checkout oar_sampiyon_portfoy.json` ile GERİ AL; "
                        "serap-geçmemiş combo canlıya ALINMAZ (§5p/§5r).",
                      imza="sapma:" + ";".join(sorted(sapan)), kritik=True)
    return _sonuc("sampiyon_portfoy", "ok", "iki şampiyon da kanıtlı bloklarda",
                  imza="kanitli")


# ── 6. Serap kapısı ─────────────────────────────────────────────────────────────
def k_serap_kapisi():
    try:
        from oar_kanit_kapisi import canli_uygun
        durum = {s: bool(canli_uygun(s)) for s in KANITLI_BLOKLAR}
    except Exception as e:
        return _sonuc("serap_kapisi", "erisilemedi", f"kanıt kapısı okunamadı: {str(e)[:60]}")
    dusen = [s for s, ok in durum.items() if not ok]
    if dusen:
        return _sonuc("serap_kapisi", "kirik",
                      f"serap-geçer DEĞİL: {', '.join(dusen)} → kanıt kapısı bu sistemleri "
                      f"canlıya uygun görmüyor (serap_testi_sonuc.json eksik/bozuk olabilir).",
                      imza="dusen:" + ";".join(sorted(dusen)), kritik=True)
    return _sonuc("serap_kapisi", "ok", "iki şampiyon da serap-geçer (DSR≥0.95)", imza="gecer")


# ── 7. WRD formülü (eski 'true retail' formülüne dönülmüş mü) ───────────────────
def k_wrd_formul():
    # Modülü IMPORT ETME (httpx vb. yoksa düşer) — kaynak dosyayı doğrudan oku.
    try:
        tam = (Path(__file__).resolve().parent / "oar_session_agent.py").read_text()
        bas = tam.index("async def _whale_retail_teyit")
        src = tam[bas:bas + 2000]
    except Exception as e:
        return _sonuc("wrd_formul", "erisilemedi", f"kaynak okunamadı: {str(e)[:60]}")
    yeni = "whale - retail" in src.replace("  ", " ")
    eski = ("0.2" in src and "0.8" in src)
    if eski or not yeni:
        return _sonuc("wrd_formul", "kirik",
                      "Canlı WRD formülü beklenenden FARKLI (yeni: whale − retail). "
                      "Eski 'true retail' (gl−0.2·wl)/0.8 yalnız 1.25× ölçek çarpanıydı, "
                      "sinyal-bot'ta KALDIRILDI — OAR da düz farkı kullanmalı.",
                      imza="formul_farkli", kritik=True)
    return _sonuc("wrd_formul", "ok", "WRD = whale − retail (sinyal-bot ile uyumlu)",
                  imza="duz_fark")


KONTROLLER_ASYNC = [k_binance_ls, k_binance_taker]
KONTROLLER_SYNC = [k_kiyotaka, k_fred, k_sampiyon_portfoy, k_serap_kapisi, k_wrd_formul]


async def denetle() -> list:
    """Tüm sözleşmeleri kontrol et → sonuç listesi."""
    out = []
    for f in KONTROLLER_SYNC:
        try:
            out.append(f())
        except Exception as e:
            out.append(_sonuc(getattr(f, "__name__", "?"), "erisilemedi", str(e)[:60]))
    try:
        r = await asyncio.gather(*[f() for f in KONTROLLER_ASYNC], return_exceptions=True)
        out.extend([x for x in r if isinstance(x, dict)])
    except Exception:
        pass
    return out


def _onceki():
    try:
        return json.loads(DURUM_FILE.read_text())
    except Exception:
        return {}


def _yaz(d):
    try:
        DURUM_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2))
    except Exception:
        pass


def _degisim(sonuclar: list) -> tuple:
    """Önceki imzalarla karşılaştır → (yeni_bozulanlar, duzelen, ilk_kez)."""
    onceki = _onceki().get("imzalar", {})
    yeni_imza = {s["ad"]: s["imza"] for s in sonuclar}
    bozulan, duzelen = [], []
    for s in sonuclar:
        eski = onceki.get(s["ad"])
        if eski is None:
            continue                          # ilk tur → taban kur, alarm yok
        if s["imza"] != eski:
            if s["durum"] in ("kirik", "yedek"):
                bozulan.append(s)
            elif s["durum"] == "ok":
                duzelen.append(s)
    _yaz({"imzalar": yeni_imza, "ts": _now().isoformat(),
          "sonuclar": sonuclar})
    return bozulan, duzelen, (not onceki)


async def bekci_turu() -> dict:
    """Bir denetim turu: kontrol → değişimi sapta → gerekiyorsa Telegram."""
    sonuclar = await denetle()
    bozulan, duzelen, ilk = _degisim(sonuclar)
    if ilk:
        print("[sozlesme_bekci] taban kuruldu (ilk tur, alarm yok)", flush=True)
        return {"ilk": True, "sonuclar": sonuclar}
    if not (bozulan or duzelen):
        return {"degisim": False, "sonuclar": sonuclar}

    satir = ["🔧 *SÖZLEŞME BEKÇİSİ — BAĞIMLILIK DEĞİŞİMİ*"]
    for s in bozulan:
        ikon = "⛔" if s["kritik"] else ("🟡" if s["durum"] == "yedek" else "⚠️")
        satir.append(f"{ikon} *{s['ad']}* → {s['detay']}")
    for s in duzelen:
        satir.append(f"✅ *{s['ad']}* düzeldi → {s['detay']}")
    satir.append("_Ajanlar otomatik UYUM SAĞLAMAZ (canlı sistemde sessiz değişim yasak, "
                 "ANAYASA #8). Gereken güncelleme yukarıda; onayınla uygulanır._")
    metin = "\n".join(satir)
    try:
        from ajan_merkez import bildir
        await bildir("Sözleşme Bekçisi", "eksik", metin.split("\n")[1][:120], detay=metin)
    except Exception as e:
        print(f"[sozlesme_bekci] telegram hata: {e}", flush=True)
    print(f"[sozlesme_bekci] {len(bozulan)} bozulma / {len(duzelen)} düzelme bildirildi", flush=True)
    return {"degisim": True, "bozulan": len(bozulan), "duzelen": len(duzelen),
            "sonuclar": sonuclar}


async def bekci_loop():
    """6 saatte bir sözleşme denetimi. main.py startup'ta başlar."""
    await asyncio.sleep(300)      # startup yoğunluğu geçsin
    while True:
        try:
            await bekci_turu()
        except Exception as e:
            print(f"[sozlesme_bekci] tur hata: {str(e)[:100]}", flush=True)
        await asyncio.sleep(KONTROL_ARALIK_S)


def baglam_metni() -> str:
    """Lider bağlamı: bozuk/yedekte olan sözleşmeler (hepsi OK ise tek satır)."""
    d = _onceki()
    sonuclar = d.get("sonuclar") or []
    if not sonuclar:
        return "SÖZLEŞME BEKÇİSİ: henüz denetim yapılmadı."
    sorun = [s for s in sonuclar if s.get("durum") in ("kirik", "yedek")]
    if not sorun:
        return f"SÖZLEŞME BEKÇİSİ: {len(sonuclar)}/{len(sonuclar)} bağımlılık sözleşmesi SAĞLAM."
    L = [f"SÖZLEŞME BEKÇİSİ: {len(sorun)}/{len(sonuclar)} bağımlılıkta sorun —"]
    for s in sorun:
        L.append(f"  {'⛔' if s.get('kritik') else '🟡'} {s['ad']}: {s['detay'][:150]}")
    return "\n".join(L)


def durum() -> dict:
    d = _onceki()
    return {"son_denetim": d.get("ts"), "sonuclar": d.get("sonuclar", []),
            "baglam": baglam_metni()}


if __name__ == "__main__":
    async def _t():
        for s in await denetle():
            print(f"[{s['durum']:12}] {s['ad']:20} {s['detay'][:80]}")
    asyncio.run(_t())
