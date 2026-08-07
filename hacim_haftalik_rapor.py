"""
hacim_haftalik_rapor.py — ANALİZÖR KARNESİ (haftalık kanıt raporu)
════════════════════════════════════════════════════════════════════════════════════
Kullanıcı isteği (Kartal haftalık raporunu örnek göstererek): "Hacimlerde öne çıkan
durumları haftalık rapor edip sisteme/şampiyonlara/agent kararlarına katkı sağlayabilir
miyiz? Yolumuzun DOĞRU OLUP OLMADIĞINI da ölçüp, bu raporlara göre hem agent kodlarında
düzenlemeye hem de GEREKSİZ bakılan verilerde SADELEŞTİRMEYE gidebilir miyiz?"

CEVAP = bu modül. Kartal raporunun disiplinini hacim konseyine uygular:
  • EŞİK TARAMASI  : güç ≥ {0.2,0.3,...} için beklenti/isabet (Kartal'ın RVOL taraması)
  • MONOTONLUK     : güç kovası ↑ iken beklenti ↑ mı? — ŞANS ile EDGE'i ayıran ASIL test.
                     Gerçek edge'de sinyal güçlendikçe sonuç düzenli iyileşir; şansta
                     kovalar rastgele zıplar. (Kartal raporundaki "RVOL kova" testi.)
  • IS/OOS         : ilk %70 / son %30 zaman bölmesi — eşik IS'te seçilir, OOS teyit eder.
  • KARAR          : DEĞERLİ / GÜRÜLTÜ / YETERSİZ VERİ → doğrudan SADELEŞTİRME önerisi.

⚠️ DÜRÜSTLÜK (bu rapor kendi sınırını da söyler):
  1. ETKİN ÖRNEKLEM: snapshot'lar 5 dk arayla ve ufuklar ÇAKIŞIR → ardışık kayıtlar
     bağımsız DEĞİL. Ham n büyük görünür ama etkin n ≈ süre/ufuk kadardır. Rapor bunu
     hesaplayıp yazar; ham n'e aldanma.
  2. ÇOKLU KARŞILAŞTIRMA: 7 analizör × 2 sembol × birkaç eşik → şansla birkaçı "iyi"
     çıkar. Savunma = monotonluk + OOS'un aynı yönde tutması.
  3. Bu rapor CANLI KARARI DEĞİŞTİRMEZ. Çıktısı ADAY'dır; şampiyona bağlanması ancak
     serap testi (DSR≥0.95) + ANAYASA #8 onayı ile olur (§5e/§5p kuralı).

VERİ: hacim_konseyi.SKOR_LOG (kompakt JSONL, haftalık silmeden muaf) + Binance klines
(ileri getiri RAPOR ANINDA hesaplanır → geçmişe dönük, sızıntısız).
"""
import os
import json
import asyncio
from pathlib import Path
from datetime import datetime, timezone, timedelta

_DATA_DIR = Path(os.environ.get("DATA_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
                 or ("/var/data" if Path("/var/data").exists() else "data"))
RAPOR_FILE = _DATA_DIR / "hacim_karne.json"
KARNE_ARSIV = Path(__file__).resolve().parent / "hacim_karne_arsiv.json"   # git-senkron

UFUK_SAAT = 4                 # ileri getiri ufku (saat) — konsey 5dk, şampiyon saatlik
GUC_ESIKLER = [0.0, 0.2, 0.3, 0.4, 0.5, 0.6]
GUC_KOVALAR = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 1.01)]
MIN_N = 30                    # bu ham n altında karar verilmez


def _now():
    return datetime.now(timezone.utc)


def _parse(ts):
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def _kayitlari_oku(gun: int = 14) -> list:
    """Skor log'undan son `gun` günün kayıtları."""
    try:
        from hacim_konseyi import SKOR_LOG
    except Exception:
        return []
    if not Path(SKOR_LOG).exists():
        return []
    sinir = _now() - timedelta(days=gun)
    out = []
    try:
        with open(SKOR_LOG, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    k = json.loads(ln)
                except Exception:
                    continue
                t = _parse(k.get("t", ""))
                if t and t >= sinir:
                    k["_t"] = t
                    out.append(k)
    except Exception:
        return []
    return out


async def _fiyat_serisi(sembol: str, bas: datetime, bit: datetime) -> list:
    """[(epoch_sec, close)] — 5m klines (ufuk hesabı için yeterli çözünürlük)."""
    try:
        from exchange_client import klines
    except Exception:
        return []
    seri = []
    imlec = int(bas.timestamp() * 1000)
    bitis_ms = int(bit.timestamp() * 1000)
    for _ in range(12):                       # ≤12 sayfa (≈ 12×1000×5dk = 41 gün)
        try:
            kl = await klines(sembol, "5m", 1000, futures=False, start_ms=imlec)
        except TypeError:
            try:
                kl = await klines(sembol, "5m", 1000)
            except Exception:
                break
        except Exception:
            break
        if not kl:
            break
        for r in kl:
            seri.append((int(r[0]) // 1000, float(r[4])))
        son = int(kl[-1][0])
        if son >= bitis_ms or len(kl) < 2:
            break
        imlec = son + 1
    seri.sort()
    return seri


def _fiyat_bul(seri: list, ts_sec: int):
    """ts'e en yakın (>=) kapanış — basit ikili arama."""
    if not seri:
        return None
    lo, hi = 0, len(seri) - 1
    if ts_sec <= seri[0][0]:
        return seri[0][1]
    if ts_sec >= seri[-1][0]:
        return seri[-1][1]
    while lo <= hi:
        m = (lo + hi) // 2
        if seri[m][0] < ts_sec:
            lo = m + 1
        else:
            hi = m - 1
    return seri[min(lo, len(seri) - 1)][1]


def _istatistik(getiriler: list) -> dict:
    """n, isabet%, beklenti% (yön-işaretli ortalama getiri), PF."""
    n = len(getiriler)
    if not n:
        return {"n": 0, "isabet": 0.0, "beklenti": 0.0, "pf": 0.0}
    kazanc = sum(g for g in getiriler if g > 0)
    kayip = -sum(g for g in getiriler if g < 0)
    return {
        "n": n,
        "isabet": round(sum(1 for g in getiriler if g > 0) / n * 100, 1),
        "beklenti": round(sum(getiriler) / n, 4),
        "pf": round(kazanc / kayip, 2) if kayip > 0 else (99.0 if kazanc > 0 else 0.0),
    }


def _monotonluk(kovalar: list) -> tuple:
    """
    Kova beklentileri artan mı? (Kartal'ın RVOL-kova testi.)
    Döner: (skor -1..1, metin). Spearman-benzeri basit sıralama uyumu.
    """
    gecerli = [(i, k["beklenti"]) for i, k in enumerate(kovalar) if k["n"] >= 10]
    if len(gecerli) < 3:
        return 0.0, "yetersiz kova"
    uyum = ters = 0
    for a in range(len(gecerli)):
        for b in range(a + 1, len(gecerli)):
            if gecerli[b][1] > gecerli[a][1]:
                uyum += 1
            elif gecerli[b][1] < gecerli[a][1]:
                ters += 1
    top = uyum + ters
    if not top:
        return 0.0, "düz"
    skor = (uyum - ters) / top
    if skor >= 0.6:
        return skor, "MONOTON ARTAN (güç ↑ → beklenti ↑) — edge işareti"
    if skor <= -0.6:
        return skor, "TERS monoton (güç ↑ → beklenti ↓) — sinyal TERS olabilir"
    return skor, "düzensiz (kovalar rastgele) — şans/gürültü işareti"


def _analizor_karne(kayitlar: list, seriler: dict, ad: str) -> dict:
    """Bir analizörün karnesi: eşik taraması + kova monotonluğu + IS/OOS."""
    ufuk = UFUK_SAAT * 3600
    ham = []      # (t, guc, signed_getiri)
    for k in kayitlar:
        u = (k.get("u") or {}).get(ad)
        if not u:
            continue
        yon, guc = u[0], u[1]
        if yon == 0:
            continue                                  # NOTR → yön iddiası yok
        seri = seriler.get(k.get("s"))
        if not seri:
            continue
        t0 = int(k["_t"].timestamp())
        p0 = _fiyat_bul(seri, t0)
        p1 = _fiyat_bul(seri, t0 + ufuk)
        if not p0 or not p1 or p0 <= 0:
            continue
        if seri[-1][0] < t0 + ufuk:                   # ufuk henüz DOLMADI → dahil etme
            continue
        ret = (p1 - p0) / p0 * 100.0
        ham.append((k["_t"], guc, ret * yon))         # yön-işaretli getiri

    if len(ham) < MIN_N:
        return {"ad": ad, "durum": "yetersiz", "n": len(ham),
                "not": f"n={len(ham)} < {MIN_N} → karar yok, veri birikmeli"}

    ham.sort(key=lambda x: x[0])
    kesme = int(len(ham) * 0.7)
    IS, OOS = ham[:kesme], ham[kesme:]

    esikler = []
    for e in GUC_ESIKLER:
        g_tum = [g for _, gc, g in ham if gc >= e]
        g_is = [g for _, gc, g in IS if gc >= e]
        g_oos = [g for _, gc, g in OOS if gc >= e]
        esikler.append({"esik": e, "tum": _istatistik(g_tum),
                        "is": _istatistik(g_is), "oos": _istatistik(g_oos)})

    kovalar = []
    for lo, hi in GUC_KOVALAR:
        g = [x for _, gc, x in ham if lo <= gc < hi]
        st = _istatistik(g)
        st["kova"] = f"[{lo}–{hi if hi <= 1 else 1.0})"
        kovalar.append(st)
    mono_skor, mono_txt = _monotonluk(kovalar)

    # ETKİN ÖRNEKLEM: çakışan ufuklar → bağımsız gözlem ≈ toplam süre / ufuk
    sure_saat = (ham[-1][0] - ham[0][0]).total_seconds() / 3600.0
    etkin_n = max(1, int(sure_saat / UFUK_SAAT))

    tum0 = esikler[0]["tum"]; oos0 = esikler[0]["oos"]
    # KARAR: beklenti pozitif + OOS aynı yönde + monotonluk destekliyor
    if (tum0["beklenti"] > 0.02 and oos0["beklenti"] > 0 and mono_skor >= 0.6
            and etkin_n >= 20):
        karar, oneri = "DEĞERLİ", "koru; şampiyon-confirm ADAYI (serap testi şart)"
    elif tum0["beklenti"] < -0.02 and mono_skor <= -0.6 and etkin_n >= 20:
        karar, oneri = "TERS", "sinyal ters çalışıyor olabilir — yön tanımını gözden geçir"
    elif abs(tum0["beklenti"]) <= 0.02 or abs(mono_skor) < 0.6:
        karar, oneri = "GÜRÜLTÜ", "konsensüse katkısı ölçülemiyor → SADELEŞTİRME adayı"
    else:
        karar, oneri = "BELİRSİZ", "veri birikmeli"
    if etkin_n < 20:
        karar, oneri = "YETERSİZ", f"etkin örneklem {etkin_n} (<20) → karar erken"

    return {"ad": ad, "durum": "ok", "karar": karar, "oneri": oneri,
            "n": len(ham), "etkin_n": etkin_n, "sure_saat": round(sure_saat, 1),
            "esikler": esikler, "kovalar": kovalar,
            "mono_skor": round(mono_skor, 2), "mono": mono_txt}


async def karne_uret(gun: int = 14) -> dict:
    """Haftalık analizör karnesi (+ konsensüsün kendi karnesi)."""
    kayitlar = _kayitlari_oku(gun)
    if not kayitlar:
        return {"durum": "veri_yok",
                "not": "skor log boş — konsey çalıştıkça birikir (5dk'da bir kayıt)."}
    try:
        from hacim_konseyi import SEMBOLLER
    except Exception:
        SEMBOLLER = ("BTCUSDT", "ETHUSDT")
    bas = min(k["_t"] for k in kayitlar)
    bit = _now()
    seriler = {}
    for s in SEMBOLLER:
        seriler[s] = await _fiyat_serisi(s, bas - timedelta(hours=1), bit)

    adlar = sorted({a for k in kayitlar for a in (k.get("u") or {})})
    karneler = [_analizor_karne(kayitlar, seriler, a) for a in adlar]

    # konsensüsün kendi karnesi (üye gibi davran)
    kons_kayit = [dict(k, u={"KONSENSÜS": [k.get("ky", 0), min(abs(k.get("kn", 0.0)), 1.0)]})
                  for k in kayitlar]
    for k, o in zip(kons_kayit, kayitlar):
        k["_t"] = o["_t"]
    kons = _analizor_karne(kons_kayit, seriler, "KONSENSÜS")

    out = {"durum": "ok", "uretim": _now().isoformat(), "gun": gun,
           "kayit_sayisi": len(kayitlar), "ufuk_saat": UFUK_SAAT,
           "konsensus": kons, "analizorler": karneler}
    try:
        RAPOR_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return out


def rapor_metni(k: dict) -> str:
    """Kural-tabanlı TAM rapor metni (Kartal formatı — sayfalı gönderilir)."""
    if k.get("durum") != "ok":
        return ("📋 *HACİM ANALİZÖR KARNESİ*\n"
                + (k.get("not") or "veri yok"))
    L = [f"📋 *HACİM ANALİZÖR KARNESİ* ({k['uretim'][:16]} UTC)",
         f"Son {k['gun']} gün · {k['kayit_sayisi']} kayıt · ileri ufuk {k['ufuk_saat']}s",
         "Ölçüm: yön-işaretli ileri getiri (LONG→+, SHORT→−). Beklenti %/işlem.",
         ""]

    def blok(a):
        if a.get("durum") != "ok":
            return [f"● *{a['ad']}* — {a.get('not','')}"]
        s = [f"● *{a['ad']}* → {a['karar']}",
             f"   n={a['n']} (etkin {a['etkin_n']}, {a['sure_saat']}s) · {a['mono']}"]
        for e in a["esikler"][:4]:
            t, o = e["tum"], e["oos"]
            s.append(f"   güç≥{e['esik']:<4} TÜM n={t['n']:<4} isabet%{t['isabet']:<5} "
                     f"bek {t['beklenti']:+.3f}% PF {t['pf']:<5} | OOS n={o['n']:<4} "
                     f"bek {o['beklenti']:+.3f}%")
        s.append("   güç kovası → beklenti (monotonluk):")
        for kv in a["kovalar"]:
            if kv["n"]:
                s.append(f"     {kv['kova']:<12} n={kv['n']:<4} isabet%{kv['isabet']:<5} "
                         f"bek {kv['beklenti']:+.3f}%")
        s.append(f"   ➜ {a['oneri']}")
        return s

    L += blok(k["konsensus"]) + [""]
    for a in k["analizorler"]:
        L += blok(a) + [""]

    degerli = [a["ad"] for a in k["analizorler"] if a.get("karar") == "DEĞERLİ"]
    gurultu = [a["ad"] for a in k["analizorler"] if a.get("karar") == "GÜRÜLTÜ"]
    L.append("━━━ *KARAR ÖZETİ* ━━━")
    L.append(f"DEĞERLİ: {', '.join(degerli) or '—'}")
    L.append(f"SADELEŞTİRME adayı (gürültü): {', '.join(gurultu) or '—'}")
    L.append("")
    L.append("_Sınır: snapshot'lar 5dk arayla + ufuklar çakışır → ardışık kayıtlar bağımsız "
             "DEĞİL; ETKİN örnekleme bak, ham n'e aldanma. 7 analizör × eşikler = çoklu "
             "karşılaştırma; savunma monotonluk + OOS'un aynı yönde tutması. Bu rapor canlı "
             "kararı DEĞİŞTİRMEZ — çıktısı ADAY; şampiyona bağlanması serap testi (DSR≥0.95) "
             "+ onay ister (ANAYASA #8)._")
    return "\n".join(L)


async def haftalik_yayinla() -> bool:
    """Karneyi üret → Telegram (sayfalı) → git-senkron arşive yaz."""
    k = await karne_uret(14)
    metin = rapor_metni(k)
    try:
        from hacim_konseyi import _uzun_gonder
        await _uzun_gonder(metin)
    except Exception as e:
        print(f"[hacim_karne] telegram hata: {e}", flush=True)
    if k.get("durum") == "ok":
        try:
            arsiv = json.loads(KARNE_ARSIV.read_text()) if KARNE_ARSIV.exists() else {"karneler": []}
        except Exception:
            arsiv = {"karneler": []}
        arsiv.setdefault("karneler", []).append({
            "tarih": _now().isoformat(), "kayit": k["kayit_sayisi"],
            "konsensus_karar": k["konsensus"].get("karar"),
            "analizor": {a["ad"]: a.get("karar") for a in k["analizorler"]},
        })
        arsiv["karneler"] = arsiv["karneler"][-52:]
        try:
            KARNE_ARSIV.write_text(json.dumps(arsiv, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        except Exception:
            pass
    return True


async def karne_loop():
    """Haftalık karne (Pazartesi 04:00 UTC civarı)."""
    await asyncio.sleep(900)
    while True:
        try:
            simdi = _now()
            if simdi.weekday() == 0 and simdi.hour == 4:
                await haftalik_yayinla()
                await asyncio.sleep(3700)
        except Exception as e:
            print(f"[hacim_karne] loop hata: {str(e)[:90]}", flush=True)
        await asyncio.sleep(1800)


def baglam_metni() -> str:
    """Lider bağlamı: son karnenin kararları."""
    try:
        k = json.loads(RAPOR_FILE.read_text())
    except Exception:
        return "ANALİZÖR KARNESİ: henüz üretilmedi (veri birikiyor)."
    if k.get("durum") != "ok":
        return f"ANALİZÖR KARNESİ: {k.get('not','veri yok')}"
    par = [f"ANALİZÖR KARNESİ ({k['uretim'][:10]}, {k['kayit_sayisi']} kayıt, ufuk {k['ufuk_saat']}s):",
           f"  KONSENSÜS → {k['konsensus'].get('karar','?')} "
           f"(bek {k['konsensus'].get('esikler',[{}])[0].get('tum',{}).get('beklenti',0):+.3f}%)"]
    for a in k.get("analizorler", []):
        par.append(f"  {a['ad']}: {a.get('karar','?')} — {a.get('oneri','')[:60]}")
    return "\n".join(par)


def durum() -> dict:
    try:
        return json.loads(RAPOR_FILE.read_text())
    except Exception:
        return {"durum": "yok", "not": "karne henüz üretilmedi"}


if __name__ == "__main__":
    async def _t():
        k = await karne_uret(14)
        print(rapor_metni(k)[:3000])
    asyncio.run(_t())
