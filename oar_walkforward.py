"""
oar_walkforward.py — YUVARLANAN WALK-FORWARD (geçmişi "gelecekmiş gibi" test et)
════════════════════════════════════════════════════════════════════════════════════
Kullanıcı sorusu: "geçmiş başarı ≠ gelecek; beklemeden, geçmiş veriyle gelecekteymişiz
gibi kenar (edge) testi nasıl?" CEVAP = WALK-FORWARD ANALİZİ (Pardo): pencereyi ileri
yuvarla, HER test ayı gerçek-canlı-dağıtım gibi KÖR (o ay eğitimde HİÇ görülmedi).

Kullanıcının örneği (Ocak+Şubat eğit → Mart'ı reel-trade gibi ölç) genelleştirildi:
  train_ay ay eğit → sonraki test_ay ayı KÖR test → pencereyi 1 ay ileri kaydır → tekrar.
Tüm test aylarının işlemleri KRONOLOJİK birleşince = "sıralı canlı işlem" OOS eğrisi.

İKİ MOD:
  • rediscover: HER train penceresinde kesfet YENİDEN blok seçer → test'e KÖR uygula.
    → SÜRECİ doğrular (yöntemimiz kalıcı edge buluyor mu; overfit'e en sert test).
  • sabit     : şampiyon bloklarını her test ayına uygula → ŞAMPİYONUN rejim-sağlamlığı
    (edge her dönemde mi tutuyor, yoksa yalnız bazı rejimlerde mi).

REJİM ETİKETİ (kullanıcı isteği "sezonu algıla"): her test ayının ortalama Efficiency
Ratio'su → range (ER<0.40, fade-dostu) / trend. Edge'in rejime bağımlılığı GÖRÜNÜR.

ŞAMPİYONA DOKUNMAZ. Havuz = serap cache (hızlı; yoksa confirm._analiz). KALICI:
walkforward_sonuc.json. KULLANIM:
  python oar_walkforward.py --symbol BTCUSDT,ETHUSDT --from 2019-01 --to 2025-06 \
      --train-ay 2 --test-ay 1 --mod rediscover|sabit [--telegram]
"""
import os
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone

from oar_sampiyon_confirm import _senaryo_metrik, _equity_sim

SERAP_CACHE = Path(__file__).resolve().parent / ".serap_cache"
PORTFOY = Path(__file__).resolve().parent / "oar_sampiyon_portfoy.json"
SONUC = Path(__file__).resolve().parent / "walkforward_sonuc.json"
GUN_MS = 86_400_000


def _havuz_yukle(semboller, bas, bit, taze=False):
    import pickle
    anahtar = "sampiyon_havuz_" + "_".join(semboller) + f"_{bas}_{bit}"
    yol = SERAP_CACHE / f"{anahtar}.pkl"
    if not taze and yol.exists():
        try:
            with open(yol, "rb") as f:
                havuz = pickle.load(f)
            print(f"[WF] serap cache'ten {len(havuz)} aday (aggTrades yeniden işlenmedi)", flush=True)
            return havuz
        except Exception as e:
            print(f"[WF] cache okunamadı ({e})", flush=True)
    print("[WF] cache yok → confirm._analiz (AĞIR, ay ay)…", flush=True)
    from oar_sampiyon_confirm import _analiz
    havuz = []
    for s in semboller:
        adaylar, _ = _analiz(s, bas, bit)
        havuz.extend(adaylar)
    return havuz


def _ay(ts) -> str:
    return datetime.fromtimestamp(int(ts) / 1000, timezone.utc).strftime("%Y-%m")


def _sampiyonlar():
    """TÜM şampiyonlar (FADE + TREND) — kullanıcı kuralı: her zaman hepsini dahil et."""
    try:
        st = json.loads(PORTFOY.read_text(encoding="utf-8")).get("stiller", [])
        out = [{"stil": s.get("stil"), "bloklar": s.get("bloklar", [])} for s in st if s.get("bloklar")]
        if out:
            return out
    except Exception:
        pass
    return [{"stil": "ekstrem_donus_fade", "bloklar": ["poc_taraf", "footprint_absorpsiyon", "footprint_kalicilik"]},
            {"stil": "kirilim_devam_trend", "bloklar": ["poc_taraf", "footprint_trapped", "gun_bias_uyum"]}]


def _rejim_etiket(kayitlar):
    """Test penceresi rejim etiketi: adayların ortalama Efficiency Ratio'su."""
    erler = [c.get("rejim_er") for c in kayitlar if c.get("rejim_er") is not None]
    if not erler:
        return "bilinmiyor", None
    ort = sum(erler) / len(erler)
    return ("range(fade-dostu)" if ort < 0.40 else "trend"), round(ort, 3)


def _pencere_listesi(aylar, train_ay, test_ay):
    out = []; i = train_ay
    while i + test_ay <= len(aylar):
        out.append((set(aylar[i - train_ay:i]), set(aylar[i:i + test_ay])))
        i += test_ay
    return out


def _topla(tum, pencereler):
    """(ts,pct) serisi + pencere listesi → toplu OOS metrik + equity + pozitif-pencere."""
    m = _senaryo_metrik(tum) if tum else None
    eq = {str(k): _equity_sim(tum, 1000.0, float(k)) for k in (1, 3, 5)} if tum else {}
    poz = sum(1 for p in pencereler if (p.get("beklenti") or 0) > 0)
    return {
        "pencere_sayisi": len(pencereler), "pozitif_pencere": poz,
        "toplu_oos": ({"n": m["n"], "pf": m["pf"], "beklenti": m["beklenti"],
                       "wr": m["wr"], "maxdd": m["maxdd"]} if m else None),
        "equity_oos": eq, "pencereler": pencereler,
    }


def _pool_yukle(semboller, bas, bit, seans):
    """
    Seans havuzu: asya → serap cache (_havuz_yukle); london/ny → oar_yeni_sampiyon'un
    .yeni_sampiyon_cache pickle'ları (kullanıcı yeni-şampiyon koşusunda kaydetti).
    """
    if seans in (None, "asya"):
        return _havuz_yukle(semboller, bas, bit)
    import pickle
    cd = KOK if 'KOK' in globals() else Path(__file__).resolve().parent
    cache = cd / ".yeni_sampiyon_cache"
    havuz = []
    for sym in semboller:
        yol = cache / f"{sym}_{seans}_{bas}_{bit}.pkl"
        if yol.exists():
            with open(yol, "rb") as f:
                havuz.extend(pickle.load(f))
        else:
            print(f"[WF] ⚠ {yol.name} yok — önce oar_yeni_sampiyon koş", flush=True)
    return havuz


def analiz_custom(semboller, bas, bit, bloklar, seans="asya", train_ay=2, test_ay=1):
    """
    ÖZEL ADAY walk-forward: verilen blok kümesini (ör. London adayı) seans havuzunda
    ay-ay KÖR test et. Yeni adayı canlıya almadan önce zaman-sağlamlığını ölçer.
    """
    from oar_kesif import _filtre
    havuz = _pool_yukle(semboller, bas, bit, seans)
    if not havuz:
        print("⚠ havuz boş.", flush=True); return None
    for c in havuz:
        c["_ay"] = _ay(c["ts"])
    aylar = sorted({c["_ay"] for c in havuz})
    pencereler = _pencere_listesi(aylar, train_ay, test_ay)
    print(f"[WF-custom] {seans} · [{'+'.join(bloklar)}] · {len(aylar)} ay · {len(pencereler)} pencere", flush=True)
    pen = []; tum = []
    for _tr, te_a in pencereler:
        test = [c for c in havuz if c["_ay"] in te_a]
        if not test:
            continue
        tt = _filtre(test, bloklar)
        kay = [(c["ts"], c["pct"]) for c in tt]
        m = _senaryo_metrik(kay) if kay else None
        rej, _ = _rejim_etiket(tt)
        tum.extend(kay)
        pen.append({"test_ay": sorted(te_a), "rejim": rej, "n": m["n"] if m else 0,
                    "pf": m["pf"] if m else None, "beklenti": m["beklenti"] if m else None})
        print(f"[WF-custom] {sorted(te_a)} → n{m['n'] if m else 0} PF {m['pf'] if m else '—'} "
              f"bek {m['beklenti'] if m else '—'} [{rej}]", flush=True)
    return {"tarih": datetime.now(timezone.utc).isoformat(), "aralik": f"{bas}..{bit}",
            "semboller": semboller, "mod": "custom", "seans": seans, "bloklar": bloklar,
            "train_ay": train_ay, "test_ay": test_ay, "pencere_sayisi": len(pencereler),
            "custom": {"bloklar": bloklar, "seans": seans, **_topla(tum, pen)}}


def analiz(semboller, bas, bit, train_ay=2, test_ay=1, mod="rediscover", taze=False):
    from oar_kesif import _filtre, kesfet
    havuz = _havuz_yukle(semboller, bas, bit, taze)
    if not havuz:
        print("⚠ havuz boş.", flush=True)
        return None
    for c in havuz:
        c["_ay"] = _ay(c["ts"])
    aylar = sorted({c["_ay"] for c in havuz})
    sampiyonlar = _sampiyonlar()   # TÜM şampiyonlar (FADE + TREND)
    pencereler = _pencere_listesi(aylar, train_ay, test_ay)
    print(f"[WF] {len(aylar)} ay · mod={mod} · {len(pencereler)} pencere · "
          f"{len(sampiyonlar)} şampiyon", flush=True)
    sonuc = {"tarih": datetime.now(timezone.utc).isoformat(), "aralik": f"{bas}..{bit}",
             "semboller": semboller, "mod": mod, "train_ay": train_ay, "test_ay": test_ay,
             "pencere_sayisi": len(pencereler)}

    if mod == "rediscover":
        # SÜREÇ doğrulama (şampiyon-bağımsız): her train'de kesfet YENİDEN blok seçer
        pen = []; tum = []
        for tr_a, te_a in pencereler:
            train = [c for c in havuz if c["_ay"] in tr_a]
            test = [c for c in havuz if c["_ay"] in te_a]
            if len(train) < 30 or not test:
                continue
            try:
                r = kesfet(train, min_k=1, max_k=3, min_trade=15, ust_n=1)
                ei = r.get("en_iyiler") or []
                bloklar = ei[0]["bloklar"] if ei else sampiyonlar[0]["bloklar"]
            except Exception as e:
                print(f"[WF] {sorted(te_a)} kesfet hata: {str(e)[:50]}", flush=True)
                bloklar = sampiyonlar[0]["bloklar"]
            tt = _filtre(test, bloklar)
            kay = [(c["ts"], c["pct"]) for c in tt]
            m = _senaryo_metrik(kay) if kay else None
            rej, _ = _rejim_etiket(tt)
            tum.extend(kay)
            pen.append({"test_ay": sorted(te_a), "bloklar": bloklar, "rejim": rej,
                        "n": m["n"] if m else 0, "pf": m["pf"] if m else None,
                        "beklenti": m["beklenti"] if m else None})
            print(f"[WF] {sorted(te_a)} → n{m['n'] if m else 0} PF {m['pf'] if m else '—'} "
                  f"[{rej}] blok={'+'.join(bloklar)}", flush=True)
        sonuc["rediscover"] = _topla(tum, pen)
        return sonuc

    # SABİT: HER şampiyonu (FADE + TREND) AYRI test et + PORTFÖY (birleşik)
    per = {}; portfoy_tum = []
    for sp in sampiyonlar:
        stil, bloklar = sp["stil"], sp["bloklar"]
        pen = []; tum = []
        for tr_a, te_a in pencereler:
            test = [c for c in havuz if c["_ay"] in te_a]
            if not test:
                continue
            tt = _filtre(test, bloklar)
            kay = [(c["ts"], c["pct"]) for c in tt]
            m = _senaryo_metrik(kay) if kay else None
            rej, _ = _rejim_etiket(tt)
            tum.extend(kay)
            pen.append({"test_ay": sorted(te_a), "rejim": rej,
                        "n": m["n"] if m else 0, "pf": m["pf"] if m else None,
                        "beklenti": m["beklenti"] if m else None})
            print(f"[WF][{stil}] {sorted(te_a)} → n{m['n'] if m else 0} "
                  f"PF {m['pf'] if m else '—'} bek {m['beklenti'] if m else '—'} [{rej}]", flush=True)
        per[stil] = {"bloklar": bloklar, **_topla(tum, pen)}
        portfoy_tum.extend(tum)
    sonuc["sampiyonlar"] = per
    sonuc["portfoy"] = _topla(portfoy_tum, [])   # 2 şampiyon birleşik (kaba: aynı sermaye, sıralı)
    return sonuc


def _blok_ozet(L, ad, d):
    t = d.get("toplu_oos") or {}
    L.append(f"\n▓ {ad}: POZİTİF {d['pozitif_pencere']}/{d['pencere_sayisi']}"
             + (f" · toplu OOS PF {t.get('pf')} beklenti {t.get('beklenti')}% WR%{t.get('wr')} "
                f"maxDD%{t.get('maxdd')} n{t.get('n')}" if t else " · yetersiz"))
    for k in ("1", "3", "5"):
        e = (d.get("equity_oos") or {}).get(k, {})
        if e:
            L.append(f"   {k}x: " + ("💀 SIFIRLANDI" if e.get("likide")
                     else f"${e.get('son'):,.0f} (maxDD%{e.get('maxdd')})"))


def rapor_metni(s):
    if not s:
        return "sonuç yok"
    L = [f"═══ WALK-FORWARD (geçmişi gelecekmiş gibi) · mod={s['mod']} ═══",
         f"train {s['train_ay']}ay → test {s['test_ay']}ay, yuvarla · {s['pencere_sayisi']} pencere"]
    if s["mod"] == "custom":
        _blok_ozet(L, f"ÖZEL ADAY [{s.get('seans')}: {'+'.join(s.get('bloklar', []))}]", s.get("custom") or {})
        L.append("\nYORUM: pozitif-pencere yüksek + toplu OOS pozitif + likidasyon yok → aday zamanda tutuyor "
                 "(canlıya alınabilir). Düşük/dalgalıysa n azlığından şans → alınmaz.")
        return "\n".join(L)
    if s["mod"] == "rediscover":
        _blok_ozet(L, "REDISCOVER (süreç doğrulama)", s.get("rediscover") or {})
    else:
        for stil, d in (s.get("sampiyonlar") or {}).items():
            _blok_ozet(L, f"ŞAMPİYON {stil} [{'+'.join(d.get('bloklar', []))}]", d)
        if s.get("portfoy"):
            _blok_ozet(L, "PORTFÖY (2 şampiyon birleşik, kaba)", s["portfoy"])
    L.append("\nYORUM: pozitif-pencere yüksek + toplu OOS pozitif + likidasyon yok → edge zamanda KALICI. "
             "Her iki şampiyon ayrı raporlanır (kullanıcı kuralı: tüm şampiyonları dahil et).")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT,ETHUSDT")
    ap.add_argument("--from", dest="bas", default="2019-01")
    ap.add_argument("--to", dest="bit", default="2025-06")
    ap.add_argument("--train-ay", type=int, default=2)
    ap.add_argument("--test-ay", type=int, default=1)
    ap.add_argument("--mod", choices=["rediscover", "sabit"], default="rediscover")
    ap.add_argument("--bloklar", default="", help="ÖZEL ADAY blokları (virgüllü) → custom WF (ör. London adayı)")
    ap.add_argument("--seans", choices=["asya", "london", "ny"], default="asya",
                    help="özel aday havuzu seansı (london/ny → oar_yeni_sampiyon cache'i)")
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--taze", action="store_true")
    a = ap.parse_args()
    semboller = [s.strip().upper() for s in a.symbol.split(",") if s.strip()]
    if a.bloklar.strip():
        bl = [b.strip() for b in a.bloklar.split(",") if b.strip()]
        s = analiz_custom(semboller, a.bas, a.bit, bl, a.seans, a.train_ay, a.test_ay)
    else:
        s = analiz(semboller, a.bas, a.bit, a.train_ay, a.test_ay, a.mod, a.taze)
    if not s:
        return
    metin = rapor_metni(s)
    print("\n" + metin, flush=True)
    SONUC.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✓ {SONUC.name} yazıldı (commit+push → lider+Claude okur)", flush=True)
    if a.telegram:
        try:
            import asyncio
            from ajan_merkez import bildir
            if s["mod"] == "sabit":
                ozet = " · ".join(f"{k}:{v['pozitif_pencere']}/{v['pencere_sayisi']}"
                                  for k, v in (s.get("sampiyonlar") or {}).items())
            elif s["mod"] == "custom":
                c = s.get("custom") or {}
                ozet = f"{c.get('pozitif_pencere')}/{c.get('pencere_sayisi')} [{s.get('seans')}]"
            else:
                r = s.get("rediscover") or {}
                ozet = f"{r.get('pozitif_pencere')}/{r.get('pencere_sayisi')}"
            asyncio.run(bildir("Walk-Forward", "backtest",
                               f"WF {s['mod']} pozitif pencere: {ozet}", metin[:3500]))
        except Exception as e:
            print(f"⚠ telegram: {e}", flush=True)


if __name__ == "__main__":
    main()
