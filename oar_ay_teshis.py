"""
oar_ay_teshis.py — AY TEŞHİSİ: kötü ay hata mı, normal varyans mı? (kullanıcı isteği "a")
════════════════════════════════════════════════════════════════════════════════════
Kullanıcı: "şampiyonlar bu ay zararla kapadı, eksik/hata var mı?" İLKE: düşük-WR/PF-3
sistem zararlı AY yaşar (WF: FADE 8/76, TREND 17/76 ay negatif) → tek aya bakıp
şampiyon bozmak TUZAK. Bu araç ayı OBJEKTİF ölçer: hata mı, normal mi.

NE YAPAR (pür-state, httpx/parquet GEREKMEZ — her yerde koşar):
  • FADE (oar_paper_box) + TREND (oar_trend_paper) canlı işlem kayıtlarını okur.
  • Ay için: n, WR, toplam equity%, sonuç dağılımı (TP/SL/TIME_STOP), sembol dağılımı.
  • TARİHSEL SINIR KIYASI: walkforward_sonuc.json'daki aylık beklenti dağılımından
    bu ayın en-kötü tarihsel ayın İÇİNDE mi yoksa AŞTI mı → "normal/anomali".
  • TEYİT ANALİZİ (FADE): kazananların vs kaybedenlerin teyitleri farklı mı
    (ör. kaybedenlerde HTF-VPFR/footprint confirm eksik mi).
KARAR: ay tarihsel-bantta ise NORMAL (dokunma); bandı aştıysa ANOMALİ (incele).
PROXY-POC sapması (canlı) ayrı: /api/oar-footprint (gerçek POC vs ortanca) — httpx ister.
"""
import json
from pathlib import Path
from datetime import datetime, timezone

KOK = Path(__file__).resolve().parent
WF = KOK / "walkforward_sonuc.json"

# ── CANLI PROXY YAPISI (kanıt: oar_sampiyon_portfoy.json + canlı entry kodu) ──
# Şampiyon blokları vs canlı giriş kodunun UYGULADIĞI bloklar. Kötü ay teşhisinde
# "canlı proxy şampiyonun kaç bloğunu taşıyor" objektif olarak görünsün diye sabit.
CANLI_BLOK = {
    "FADE": {
        "sampiyon": ["poc_taraf", "footprint_absorpsiyon", "footprint_kalicilik"],
        "canli": ["poc_taraf (gerçek POC)", "absorpsiyon (vol_z≥1 proxy)", "reclaim"],
        "not": "sweep+reclaim+vol_z≥1+POC-tarafı ister → NADİR tetiklenir; kırılım "
               "ayında fade tuzağı oluşmaz → 0 işlem BEKLENEN (mean-reversion doğası).",
    },
    "TREND": {
        "sampiyon": ["poc_taraf", "footprint_trapped", "gun_bias_uyum"],
        "canli": ["gun_bias_uyum"],
        "not": "canlı giriş şampiyonun 3 bloğundan YALNIZ gun_bias_uyum'u uygular; "
               "poc_taraf + footprint_trapped tick-veri ister, canlıda YOK → daha "
               "GEVŞEK → düşük-kalite kırılımları yakalar (R:R ters riski).",
    },
}


def _ay_of(t):
    s = t.get("kapanis") or t.get("acilis") or ""
    return s[:7]


def _net(t):
    return float(t.get("net_fiyat_pct", 0.0) or 0.0)


def _equity_pct(t):
    if t.get("equity_pct") is not None:
        return float(t["equity_pct"])
    return _net(t) * float(t.get("kaldirac", 5))   # TREND equity_pct tutmaz → 5x varsay


def _sistem_ay(islemler, ay):
    tr = [t for t in islemler if _ay_of(t) == ay]
    if not tr:
        return None
    net = [_net(t) for t in tr]
    kaz = [t for t in tr if _net(t) > 0]
    son = {}
    for t in tr:
        son[t.get("sonuc", "?")] = son.get(t.get("sonuc", "?"), 0) + 1
    sem = {}
    for t in tr:
        s = t.get("sembol", "?")
        sem[s] = sem.get(s, 0) + 1
    kaz_net = [n for n in net if n > 0]
    kay_net = [n for n in net if n <= 0]
    ort_kaz = round(sum(kaz_net) / len(kaz_net), 4) if kaz_net else None
    ort_kay = round(sum(kay_net) / len(kay_net), 4) if kay_net else None
    # R:R (kazananın ort büyüklüğü / kaybedenin ort büyüklüğü) — düşük-WR/PF~3
    # şampiyonda ~5 beklenir (avgW/avgL = PF·(1-WR)/WR). <1.5 = R:R TERS = canlı
    # proxy şampiyon profilini taşımıyor (küçük kazanç/büyük kayıp).
    rr = round(ort_kaz / abs(ort_kay), 2) if (ort_kaz and ort_kay) else None
    return {
        "n": len(tr), "kazanan": len(kaz), "wr": round(len(kaz) / len(tr) * 100, 1),
        "toplam_equity_pct": round(sum(_equity_pct(t) for t in tr), 2),
        "ort_net_pct": round(sum(net) / len(net), 4),
        "sonuc_dagilim": son, "sembol_dagilim": sem,
        "en_kotu_net": round(min(net), 3), "en_iyi_net": round(max(net), 3),
        "ort_kazanan_net": ort_kaz, "ort_kaybeden_net": ort_kay, "rr": rr,
    }


def _tarihsel_bant(stil):
    """walkforward_sonuc.json'dan bir stilin aylık beklenti dağılımı (min/p10/medyan)."""
    if not WF.exists():
        return None
    try:
        d = json.loads(WF.read_text(encoding="utf-8"))
    except Exception:
        return None
    pen = ((d.get("sampiyonlar") or {}).get(stil) or {}).get("pencereler")
    if not pen and d.get("mod") == "custom":
        pen = (d.get("custom") or {}).get("pencereler")
    if not pen:
        return None
    bekler = sorted(p["beklenti"] for p in pen if p.get("beklenti") is not None)
    if not bekler:
        return None
    n = len(bekler)
    return {"min": bekler[0], "p10": bekler[max(0, n // 10)], "medyan": bekler[n // 2],
            "negatif_ay": sum(1 for b in bekler if b < 0), "toplam_ay": n}


def _teyit_analiz(islemler, ay):
    """FADE: kazananların vs kaybedenlerin teyit sıklığı (confirm eksikliği var mı)."""
    tr = [t for t in islemler if _ay_of(t) == ay and "teyitler" in t]
    if not tr:
        return None
    from collections import Counter
    kaz_c, kay_c = Counter(), Counter()
    kaz_n = kay_n = 0
    for t in tr:
        c = kaz_c if _net(t) > 0 else kay_c
        if _net(t) > 0:
            kaz_n += 1
        else:
            kay_n += 1
        for ty in (t.get("teyitler") or []):
            anahtar = ty.split(" ")[0].split("→")[0].strip()[:22]
            c[anahtar] += 1
    return {"kazanan_teyit": dict(kaz_c.most_common(6)), "kaybeden_teyit": dict(kay_c.most_common(6)),
            "kazanan_n": kaz_n, "kaybeden_n": kay_n}


def teshis(ay=None):
    if ay is None:
        ay = datetime.now(timezone.utc).strftime("%Y-%m")
    out = {"ay": ay, "sistemler": {}, "uyari": []}
    yuk = []
    try:
        from oar_paper_box import _yukle as fade_yukle
        yuk.append(("FADE", "ekstrem_donus_fade", fade_yukle()))
    except Exception as e:
        out["uyari"].append(f"FADE state okunamadı: {str(e)[:60]}")
    try:
        from oar_trend_paper import _yukle as trend_yukle
        yuk.append(("TREND", "kirilim_devam_trend", trend_yukle()))
    except Exception as e:
        out["uyari"].append(f"TREND state okunamadı: {str(e)[:60]}")

    toplam_eq = 0.0
    for ad, stil, durum in yuk:
        islemler = (durum or {}).get("islemler", [])
        ozet = _sistem_ay(islemler, ay)
        bant = _tarihsel_bant(stil)
        kayit = {"ozet": ozet, "tarihsel_bant": bant}
        if ozet and bant:
            # bu ayın ort beklentisi tarihsel min'in altında mı → anomali
            if ozet["ort_net_pct"] < bant["min"]:
                kayit["karar"] = "🔴 ANOMALİ — bu ay tarihsel EN KÖTÜ aydan da kötü (incele)"
                out["uyari"].append(f"{ad}: ort beklenti {ozet['ort_net_pct']} < tarihsel min {bant['min']}")
            elif ozet["ort_net_pct"] < bant["p10"]:
                kayit["karar"] = "🟠 ZAYIF ama tarihsel bant içinde (kötü %10 dilim) — normal varyans sınırı"
            else:
                kayit["karar"] = "🟢 NORMAL — tarihsel dağılım içinde (dokunma)"
        elif ozet:
            kayit["karar"] = "⚠ tarihsel bant yok (walkforward_sonuc.json gerek) — yalnız ay özeti"
        else:
            kayit["karar"] = "bu ay bu sistemde işlem yok"
        if ad == "FADE":
            kayit["teyit_analiz"] = _teyit_analiz(islemler, ay)
        # CANLI PROXY YAPISI + R:R TERS tespiti (canlı proxy şampiyon profilini taşıyor mu)
        kayit["canli_yapi"] = CANLI_BLOK.get(ad)
        if ozet and ozet.get("n", 0) >= 5 and ozet.get("rr") is not None and ozet["rr"] < 1.5:
            kayit["rr_uyari"] = (f"🟠 R:R TERS ({ozet['rr']}) — kazanan ort %{ozet['ort_kazanan_net']} "
                                 f"< kaybeden ort %{abs(ozet['ort_kaybeden_net'])}. Düşük-WR/PF~3 "
                                 f"şampiyonda ~5 beklenir. Canlı proxy şampiyon profilini TAŞIMIYOR "
                                 f"(bloklar eksik) → şampiyon edge'i değil, canlı-proxy zayıflığı.")
            out["uyari"].append(f"{ad}: R:R ters ({ozet['rr']}) — canlı proxy zayıflığı")
        if ozet:
            toplam_eq += ozet["toplam_equity_pct"]
        out["sistemler"][ad] = kayit

    # ── CROSS-SYSTEM: FADE=0 ama TREND aktif → asimetriyi açıkla (bug değil, rejim) ──
    fade_o = (out["sistemler"].get("FADE") or {}).get("ozet")
    trend_o = (out["sistemler"].get("TREND") or {}).get("ozet")
    if (not fade_o) and trend_o and trend_o.get("n", 0) > 0:
        out["asimetri"] = (
            f"FADE 0 işlem ama TREND {trend_o['n']} işlem (AYNI per-sembol kapı). Bug DEĞİL: "
            f"FADE canlı girişi sweep+reclaim+vol_z≥1+POC-tarafı ister (nadir trap); TREND "
            f"canlı girişi sadece 'kırılım + gün-bias' ister (gevşek). Bu ay fiyat Asia "
            f"ekstremlerini KIRIP geri dönmedi (fade tuzağı yok) → mean-reversion FADE doğru "
            f"şekilde bekledi; kırılımlar devam etmeyince (5 TIME_STOP) gevşek TREND zarar etti.")
        out["uyari"].append("FADE 0 / TREND aktif asimetrisi: rejim (kırılım ayı), bug değil")

    out["toplam_equity_pct"] = round(toplam_eq, 2)
    islem_var = any(v.get("ozet") for v in out["sistemler"].values())
    rr_ters = any(v.get("rr_uyari") for v in out["sistemler"].values())
    if not islem_var:
        out["genel"] = (f"{ay} ayında HİÇ işlem yok. Muhtemelen YEREL state boş — canlı "
                        f"paper Railway'de çalışır. Railway endpoint kullan: /api/oar-ay-teshis?ay=... "
                        f"ya da doğru ayı gir. (0 işlem ≠ zararlı ay.)")
    elif any("ANOMALİ" in (v.get("karar") or "") for v in out["sistemler"].values()):
        out["genel"] = f"İki sistemin ay toplamı %{toplam_eq:.1f}. Bir sistem ANOMALİ → gerçek inceleme gerekir."
    elif rr_ters:
        out["genel"] = (f"İki sistemin ay toplamı %{toplam_eq:.1f}. Tarihsel bant aşılmadı AMA R:R TERS "
                        f"→ zarar 'şampiyon edge'i bozuldu' değil, CANLI PROXY şampiyonun seçici "
                        f"bloklarını (poc_taraf/trapped/absorpsiyon) taşımadığı için. Şampiyona DOKUNMA; "
                        f"canlı proxy'yi şampiyona yaklaştırmak = ANAYASA #8 (ayrı onay).")
    else:
        out["genel"] = f"İki sistemin ay toplamı %{toplam_eq:.1f}. Anomali yok → düşük-WR sistemin normal zararlı ayı; şampiyona dokunma."
    return out


def rapor_metni(d):
    L = [f"═══ AY TEŞHİSİ · {d['ay']} ═══"]
    for ad, k in d["sistemler"].items():
        o = k.get("ozet")
        if not o:
            L.append(f"\n▸ {ad}: işlem yok"); continue
        L.append(f"\n▸ {ad}: n{o['n']} · WR%{o['wr']} ({o['kazanan']} kazanan) · "
                 f"ay equity %{o['toplam_equity_pct']} · sonuç {o['sonuc_dagilim']}")
        b = k.get("tarihsel_bant")
        if b:
            L.append(f"   tarihsel aylık beklenti: min {b['min']} · p10 {b['p10']} · medyan {b['medyan']} "
                     f"({b['negatif_ay']}/{b['toplam_ay']} ay negatif) · bu ay ort {o['ort_net_pct']}")
        if o.get("rr") is not None:
            L.append(f"   R:R {o['rr']} (kazanan ort %{o.get('ort_kazanan_net')} / "
                     f"kaybeden ort %{o.get('ort_kaybeden_net')})")
        L.append(f"   → {k.get('karar')}")
        if k.get("rr_uyari"):
            L.append(f"   {k['rr_uyari']}")
        cy = k.get("canli_yapi")
        if cy:
            L.append(f"   canlı proxy: şampiyon {cy['sampiyon']} → canlı {cy['canli']}")
        t = k.get("teyit_analiz")
        if t:
            L.append(f"   teyit — kazanan({t['kazanan_n']}): {t['kazanan_teyit']} | "
                     f"kaybeden({t['kaybeden_n']}): {t['kaybeden_teyit']}")
    if d.get("asimetri"):
        L.append(f"\n⚖ ASİMETRİ: {d['asimetri']}")
    L.append(f"\nGENEL: {d['genel']}")
    for u in d.get("uyari", []):
        L.append(f"  ⚠ {u}")
    return "\n".join(L)


if __name__ == "__main__":
    import sys
    ay = sys.argv[1] if len(sys.argv) > 1 else None
    print(rapor_metni(teshis(ay)))
