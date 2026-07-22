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


def _sampiyon_bloklar():
    try:
        st = json.loads(PORTFOY.read_text(encoding="utf-8")).get("stiller", [])
        for s in st:
            if s.get("stil") == "ekstrem_donus_fade":
                return s.get("bloklar", [])
    except Exception:
        pass
    return ["poc_taraf", "footprint_absorpsiyon", "footprint_kalicilik"]


def _rejim_etiket(kayitlar):
    """Test penceresi rejim etiketi: adayların ortalama Efficiency Ratio'su."""
    erler = [c.get("rejim_er") for c in kayitlar if c.get("rejim_er") is not None]
    if not erler:
        return "bilinmiyor", None
    ort = sum(erler) / len(erler)
    return ("range(fade-dostu)" if ort < 0.40 else "trend"), round(ort, 3)


def analiz(semboller, bas, bit, train_ay=2, test_ay=1, mod="rediscover", taze=False):
    from oar_kesif import _filtre, kesfet
    havuz = _havuz_yukle(semboller, bas, bit, taze)
    if not havuz:
        print("⚠ havuz boş.", flush=True)
        return None
    for c in havuz:
        c["_ay"] = _ay(c["ts"])
    aylar = sorted({c["_ay"] for c in havuz})
    sampiyon = _sampiyon_bloklar()
    print(f"[WF] {len(aylar)} ay · mod={mod} · train={train_ay} test={test_ay}", flush=True)

    pencereler = []
    tum_test_kayit = []   # (ts, pct) kronolojik OOS eğrisi
    i = train_ay
    while i + test_ay <= len(aylar):
        train_aylar = set(aylar[i - train_ay:i])
        test_aylar = set(aylar[i:i + test_ay])
        train = [c for c in havuz if c["_ay"] in train_aylar]
        test = [c for c in havuz if c["_ay"] in test_aylar]
        i += test_ay
        if len(train) < 30 or not test:
            continue

        if mod == "rediscover":
            try:
                sonuc = kesfet(train, min_k=1, max_k=3, min_trade=15, ust_n=1)
                en_iyiler = sonuc.get("en_iyiler") or []
                bloklar = (en_iyiler[0]["bloklar"] if en_iyiler else sampiyon)
            except Exception as e:
                print(f"[WF] {sorted(test_aylar)} kesfet hata: {str(e)[:50]}", flush=True)
                bloklar = sampiyon
        else:
            bloklar = sampiyon

        test_trades = _filtre(test, bloklar)
        kayitlar = [(c["ts"], c["pct"]) for c in test_trades]
        m = _senaryo_metrik(kayitlar) if kayitlar else None
        rej, er = _rejim_etiket(test_trades)
        tum_test_kayit.extend(kayitlar)
        pencereler.append({
            "test_ay": sorted(test_aylar), "train_ay": sorted(train_aylar),
            "bloklar": bloklar, "rejim": rej, "er": er,
            "n": (m["n"] if m else 0), "pf": (m["pf"] if m else None),
            "beklenti": (m["beklenti"] if m else None),
            "wr": (m["wr"] if m else None),
        })
        print(f"[WF] test {sorted(test_aylar)} → n{m['n'] if m else 0} "
              f"PF {m['pf'] if m else '—'} bek {m['beklenti'] if m else '—'} "
              f"[{rej}] blok={'+'.join(bloklar)}", flush=True)

    # Toplu OOS (tüm test aylarını sıralı işlemiş gibi)
    toplu = _senaryo_metrik(tum_test_kayit) if tum_test_kayit else None
    eq = {str(k): _equity_sim(tum_test_kayit, 1000.0, float(k)) for k in (1, 3, 5)} if tum_test_kayit else {}
    poz = sum(1 for p in pencereler if (p["beklenti"] or 0) > 0)
    return {
        "tarih": datetime.now(timezone.utc).isoformat(), "aralik": f"{bas}..{bit}",
        "semboller": semboller, "mod": mod, "train_ay": train_ay, "test_ay": test_ay,
        "pencere_sayisi": len(pencereler), "pozitif_pencere": poz,
        "toplu_oos": ({"n": toplu["n"], "pf": toplu["pf"], "beklenti": toplu["beklenti"],
                       "wr": toplu["wr"], "maxdd": toplu["maxdd"]} if toplu else None),
        "equity_oos": eq, "pencereler": pencereler,
    }


def rapor_metni(s):
    if not s:
        return "sonuç yok"
    t = s.get("toplu_oos") or {}
    L = [f"═══ WALK-FORWARD (geçmişi gelecekmiş gibi) · mod={s['mod']} ═══",
         f"train {s['train_ay']}ay → test {s['test_ay']}ay, yuvarla · {s['pencere_sayisi']} pencere",
         f"POZİTİF pencere: {s['pozitif_pencere']}/{s['pencere_sayisi']} "
         f"(edge kaç ayda tuttu — asıl kenar testi)",
         (f"TOPLU OOS: n{t.get('n')} PF {t.get('pf')} beklenti {t.get('beklenti')}% "
          f"WR%{t.get('wr')} maxDD%{t.get('maxdd')}" if t else "TOPLU OOS: yetersiz")]
    eq = s.get("equity_oos") or {}
    if eq:
        for k in ("1", "3", "5"):
            d = eq.get(k, {})
            L.append(f"  {k}x: " + ("💀 SIFIRLANDI" if d.get("likide")
                     else f"${d.get('son'):,.0f} (maxDD%{d.get('maxdd')})"))
    L.append("\nPENCERE PENCERE (KÖR test):")
    for p in s["pencereler"]:
        L.append(f"  {','.join(p['test_ay'])}: n{p['n']} PF {p['pf']} bek {p['beklenti']} "
                 f"[{p['rejim']}]" + (f" blok={'+'.join(p['bloklar'])}" if s['mod'] == 'rediscover' else ""))
    L.append("\nYORUM: pozitif-pencere oranı yüksek + toplu OOS pozitif + likidasyon yok "
             "→ edge zamanda KALICI (rejim değişse de). Düşükse edge rejime-bağımlı/seraptır.")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT,ETHUSDT")
    ap.add_argument("--from", dest="bas", default="2019-01")
    ap.add_argument("--to", dest="bit", default="2025-06")
    ap.add_argument("--train-ay", type=int, default=2)
    ap.add_argument("--test-ay", type=int, default=1)
    ap.add_argument("--mod", choices=["rediscover", "sabit"], default="rediscover")
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--taze", action="store_true")
    a = ap.parse_args()
    semboller = [s.strip().upper() for s in a.symbol.split(",") if s.strip()]
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
            asyncio.run(bildir("Walk-Forward", "backtest",
                               f"WF {s['mod']}: {s['pozitif_pencere']}/{s['pencere_sayisi']} pozitif pencere",
                               metin[:3500]))
        except Exception as e:
            print(f"⚠ telegram: {e}", flush=True)


if __name__ == "__main__":
    main()
