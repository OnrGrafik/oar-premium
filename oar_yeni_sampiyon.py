"""
oar_yeni_sampiyon.py — YENİ ŞAMPİYON ARAYICI (3 açı TEK backtest, serap-elemeli)
════════════════════════════════════════════════════════════════════════════════════
Kullanıcı: "tüm backtestleri baz alarak yeni şampiyon çıkar; 3 açıyı bir backtest'te
dene, gereksizi hızlıca ele." DERS (5n/5p/5r): blok ekleyip aynı veriyi tekrar taramak
yeni şampiyon getirmez (hepsi serap). Yeni şampiyon → YENİ ZEMİN gerek.

3 AÇI (her biri AYNI kanıtlı mekanizma/blok uzayıyla, farklı zemin; her aday SERAP
bateriyle ELENİR — DSR≥0.95 ∧ CI-alt>0 ∧ perm-p<0.05 (FDR) ∧ 5x-likid=0):
  ① SEANS GENELLEŞTİRME: şampiyon ekstrem-fade mekanizmasını LONDON (07-11) + NY (13-17)
     range'lerine uygula (şu an yalnız Asya). Edge yapısalsa başka seanslarda da çıkar.
     Tam 34-blok uzayı (footprint+POZİSYON oi/whale+vwap+frvp+delta+tpo) → açı-2 dahil.
  ② POZİSYON-EKSTREMİ: funding verisi YOK (backtest edilemez) → vekil = OI z + L/S ratio
     ekstremi blokları (oi_yuksek/whale_retail_zit) zaten blok uzayında → ① keşfinde denenir.
  ③ REJİM-SWITCH META: range günü FADE + trend günü TREND birleşik (rejim_er eşiği) →
     rejime göre otomatik geçiş iki şampiyonu ayrı ayrıdan iyi mi.

ŞAMPİYONA/portföye DOKUNMAZ: kesfet/_filtre/serap_karnesi okur; öneriler
oar_yeni_sampiyon_aday.json'a yazılır (serap-geçer + onayla portföye alınır — 5p).
AĞIR: seans başına aggTrades bir kez işlenir → .yeni_sampiyon_cache pickle (re-run hızlı).
KULLANIM: python oar_yeni_sampiyon.py --symbol BTCUSDT,ETHUSDT --from 2019-01 --to 2025-06 --telegram
"""
import os
import json
import pickle
import argparse
from pathlib import Path
from datetime import datetime, timezone

from oar_local_backtest import (_klines_oku, _aggt_ay_yollari, _metrics_oku,
                                _gun_hazirla, aday_sinyaller_uret)

KOK = Path(__file__).resolve().parent
CACHE = KOK / ".yeni_sampiyon_cache"
SONUC = KOK / "yeni_sampiyon_sonuc.json"
ADAY = KOK / "oar_yeni_sampiyon_aday.json"
PORTFOY = KOK / "oar_sampiyon_portfoy.json"

# seans_ad: (bas_utc, bit_utc, post_bit_utc) — range penceresi + sonrası (gün sonuna dek)
# london/ny KANITLI koşuldu; london_kisa + us_acilis = YENİ ZEMİN (kullanıcı isteği).
# ⚠ ÇOKLU-KARŞILAŞTIRMA: seans arttıkça FDR cezası büyür → geçmek zorlaşır (savunma).
SEANSLAR = {
    "london": (7.0, 11.0, 24.0),
    "ny": (13.0, 17.0, 24.0),
    "london_kisa": (7.0, 10.0, 24.0),    # daha dar London range (kırılım daha keskin?)
    "us_acilis": (13.5, 15.5, 24.0),     # ABD borsa açılışı (13:30 UTC) 2 saatlik range
}
N_DENEME = 300   # DSR çoklu-karşılaştırma cezası (konservatif — şampiyonla aynı)


def _havuz(sym, bas, bit, seans_ad, seans, taze=False):
    CACHE.mkdir(exist_ok=True)
    yol = CACHE / f"{sym}_{seans_ad}_{bas}_{bit}.pkl"
    if not taze and yol.exists():
        try:
            with open(yol, "rb") as f:
                h = pickle.load(f)
            print(f"   [{sym}/{seans_ad}] cache'ten {len(h)} aday (aggTrades yeniden işlenmedi)", flush=True)
            return h
        except Exception:
            pass
    klines = _klines_oku(sym, bas, bit)
    yollar = _aggt_ay_yollari(sym, bas, bit)
    if klines is None or not yollar:
        print(f"   [{sym}/{seans_ad}] ⚠ parquet yok — atlandı", flush=True)
        return []
    gunler = _gun_hazirla(klines, yollar, _metrics_oku(sym, bas, bit),
                          seans_bas=seans[0], seans_bit=seans[1], post_bit=seans[2])
    adaylar = aday_sinyaller_uret(gunler)
    for a in adaylar:
        a["_sembol"] = sym; a["_seans"] = seans_ad
    with open(yol, "wb") as f:
        pickle.dump(adaylar, f)
    print(f"   [{sym}/{seans_ad}] ✓ {len(adaylar)} aday (cache'lendi)", flush=True)
    return adaylar


def _serap(ad, trades):
    """Bir aday işlem kümesine serap karnesi. n<30 ise yetersiz."""
    from oar_serap_testi import serap_karnesi
    pcts = [c["pct"] for c in trades]; ts = [c["ts"] for c in trades]
    if len(pcts) < 30:
        return {"ad": ad, "n": len(pcts), "yetersiz": True}
    kar = serap_karnesi(ad, pcts, ts, N_DENEME)
    return kar


def analiz(semboller, bas, bit, taze=False):
    from oar_kesif import kesfet, _filtre
    from oar_serap_testi import _karar, _bh_fdr
    karneler = []
    detay = []

    # ── AÇI ①+②: LONDON & NY seans keşfi (tam blok uzayı) ──
    for seans_ad, seans in SEANSLAR.items():
        havuz = []
        for sym in semboller:
            havuz += _havuz(sym, bas, bit, seans_ad, seans, taze)
        if len(havuz) < 100:
            print(f"[Yeni] {seans_ad}: yetersiz aday ({len(havuz)}) — atlandı", flush=True)
            continue
        print(f"[Yeni] {seans_ad} kesfet ({len(havuz)} aday, 34-blok)…", flush=True)
        k = kesfet(havuz, min_k=1, max_k=3, ust_n=5)
        for cand in (k.get("en_iyiler") or [])[:5]:
            bloklar = cand["bloklar"]
            trades = _filtre(havuz, bloklar)
            ad = f"seans:{seans_ad}[{'+'.join(bloklar)}]"
            kar = _serap(ad, trades)
            karneler.append(kar)
            detay.append({"aci": f"seans:{seans_ad}", "bloklar": bloklar,
                          "oos_puan": cand.get("oos_puan"),
                          "n": (kar.get("temel", {}) or {}).get("n", kar.get("n"))})

    # ── AÇI ③: REJİM-SWITCH META (Asya serap cache: FADE range-günü + TREND trend-günü) ──
    try:
        from oar_walkforward import _havuz_yukle
        asia = _havuz_yukle(semboller, bas, bit)
        st = {s["stil"]: s["bloklar"] for s in json.loads(PORTFOY.read_text(encoding="utf-8")).get("stiller", [])}
        fade_b, trend_b = st.get("ekstrem_donus_fade"), st.get("kirilim_devam_trend")
        if asia and fade_b and trend_b:
            fade_tr = _filtre(asia, fade_b); trend_tr = _filtre(asia, trend_b)
            sec = ([c for c in fade_tr if c.get("rejim_er") is not None and c["rejim_er"] < 0.40]
                   + [c for c in trend_tr if c.get("rejim_er") is not None and c["rejim_er"] >= 0.40])
            ad = "rejim_switch:FADE-range+TREND-trend"
            kar = _serap(ad, sec)
            karneler.append(kar)
            detay.append({"aci": "rejim_switch", "n": (kar.get("temel", {}) or {}).get("n", kar.get("n"))})
        else:
            print("[Yeni] rejim-switch: Asya serap cache ya da portföy yok — atlandı", flush=True)
    except Exception as e:
        print(f"[Yeni] rejim-switch atlandı: {str(e)[:70]}", flush=True)

    # ── FDR (çoklu-test) + KARAR ──
    ad_p = [(k["ad"], k["permutasyon_p"]) for k in karneler if "permutasyon_p" in k]
    fdr = _bh_fdr(ad_p)
    for k in karneler:
        if "deflated_sharpe" in k:
            k["karar"] = _karar(k, bool(fdr.get(k["ad"], False)))
        else:
            k["karar"] = "❓ YETERSİZ VERİ (n<30)"

    return {"tarih": datetime.now(timezone.utc).isoformat(), "aralik": f"{bas}..{bit}",
            "semboller": semboller, "seanslar": list(SEANSLAR.keys()),
            "karneler": karneler, "detay": detay}


def rapor_metni(s):
    L = ["═══ YENİ ŞAMPİYON ARAYICI (seans genelleştirme + rejim-switch, serap-elemeli) ═══",
         f"Aralık {s['aralik']} · {','.join(s['semboller'])}",
         "Funding açısı: geçmiş veri YOK → atlandı (pozisyon blokları oi/whale seans keşfinde denendi).",
         f"{'SİSTEM':52} {'n':>5} {'PF':>5} {'DSR':>6} {'CI-alt':>8}  KARAR"]
    gercek = []
    for k in s["karneler"]:
        ad = k["ad"][:50]
        if k.get("yetersiz"):
            L.append(f"{ad:52} {k.get('n',0):>5}     —      —        —  ❓ n<30")
            continue
        t = k.get("temel", {})
        dsr = (k.get("deflated_sharpe") or {}).get("dsr")
        ci = (k.get("bootstrap_beklenti_ci") or {}).get("alt")
        L.append(f"{ad:52} {t.get('n',0):>5} {t.get('pf','—'):>5} {dsr if dsr is not None else '—':>6} "
                 f"{ci if ci is not None else '—':>8}  {k.get('karar','')}")
        if k.get("karar") == "✅ GERÇEK EDGE":
            gercek.append(k["ad"])
    L.append("")
    L.append(f"🏆 YENİ GERÇEK EDGE: {', '.join(gercek) if gercek else 'YOK (hepsi elendi — bu zeminlerde yeni şampiyon çıkmadı)'}")
    L.append("NOT: GERÇEK EDGE çıkan aday oar_yeni_sampiyon_aday.json'a yazıldı; portföye ALINMADI. "
             "Walk-forward + onayla eklenir. Çıkmadıysa: 'bu zeminlerde de edge yok' KESİN öğrenildi.")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT,ETHUSDT")
    ap.add_argument("--from", dest="bas", default="2019-01")
    ap.add_argument("--to", dest="bit", default="2025-06")
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--taze", action="store_true")
    a = ap.parse_args()
    semboller = [s.strip().upper() for s in a.symbol.split(",") if s.strip()]
    s = analiz(semboller, a.bas, a.bit, a.taze)
    metin = rapor_metni(s)
    print("\n" + metin, flush=True)
    ADAY.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    SONUC.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✓ {SONUC.name} + {ADAY.name} yazıldı (KANITLI portföy DEĞİŞMEDİ).", flush=True)
    if a.telegram:
        try:
            import asyncio
            from ajan_merkez import bildir
            g = [k["ad"] for k in s["karneler"] if k.get("karar") == "✅ GERÇEK EDGE"]
            asyncio.run(bildir("Yeni Şampiyon Arayıcı", "backtest",
                               f"Seans+rejim keşfi: {len(g)} yeni GERÇEK EDGE" if g else
                               "Seans+rejim keşfi: yeni şampiyon çıkmadı (hepsi serap-elendi)",
                               metin[:3500]))
        except Exception as e:
            print(f"⚠ telegram: {e}", flush=True)


if __name__ == "__main__":
    main()
