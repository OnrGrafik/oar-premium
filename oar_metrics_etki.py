"""
oar_metrics_etki.py — METRICS ZAMAN-DAMGASI HATASININ ŞAMPİYONLARA ETKİSİ (LOCAL)
═══════════════════════════════════════════════════════════════════════════════════════
BULGU (oar_wsd_backtest ile ortaya çıktı, ampirik doğrulandı):
  `oar_local_backtest._metrics_oku` şunu yapıyor →
      pd.to_datetime(create_time).astype("int64") // 1_000_000
  pandas 1.x'te to_datetime HEP datetime64[ns] verirdi → bölme ms üretirdi (DOĞRU).
  pandas 2.0+ çözünürlüğü KORUYOR ("2023-01-01 00:00:00" → [s]/[us]) → aynı bölme
  SANİYE üretiyor (1000× küçük). `_ms_olcekle` yalnız AŞAĞI ölçekler (ns/µs→ms),
  saniyeyi düzeltmez → `_gun_hazirla`'da metrics gün indeksi (≈19) klines gün
  indeksiyle (≈19700) EŞLEŞMİYOR → `gunler.get(gun)` None → `continue` →
  **oi_map / whale_ls_map / retail_ls_map HİÇ KURULMUYOR.**

SONUÇ: `oi_yuksek`, `whale_retail_zit`, `oi_tuzak` blokları BUGÜNE KADARKİ TÜM
keşif koşularında sessizce None döndü → kesfet onları PAS GEÇTİ. Yani "31/34 blok
tarandı" dediğimiz koşular fiilen 3 blok EKSİK taradı. Bu bloklar kaybetmedi —
sahaya hiç çıkmadı.

BU MODÜL NE YAPAR: düzeltmeyi ŞAMPİYON DOSYASINA YAZMADAN, çalışma anında enjekte
edip (monkey-patch) etkiyi ÖLÇER. Kullanıcı sayıyı görüp onaylarsa kalıcı düzeltme
ayrı adımda yapılır (ANAYASA #8).

ÖLÇÜLEN 4 ŞEY:
  ① KAPSAM   — kaç aday sinyal artık metrics alanlarını taşıyor (öncesi: 0).
  ② GÜVENLİK — İKİ İNCUMBENT ŞAMPİYON düzeltilmiş havuzda AYNI mı? Blokları
               metrics'e dokunmuyor → n/PF/beklenti DEĞİŞMEMELİ. Değişirse
               düzeltme yan etki yapıyor demektir (kırmızı bayrak).
  ③ KEŞİF    — tam blok havuzuyla kesfet: metrics blokları artık yarışıyor.
               İncumbent'i geçen YENİ kombinasyon çıkıyor mu?
  ④ SERAP    — metrics bloğu içeren her yeni aday DSR≥0.95 bateresinden geçirilir.
               Geçmeyen ADAY bile sayılmaz (§5p dersi: çoğu yeni combo seraptır).

⚠️ ŞAMPİYON KODUNA/PORTFÖYÜNE YAZMAZ. `oar_sampiyon_portfoy.json` DOKUNULMAZ.
   Çıktı yalnız `oar_metrics_etki_sonuc.json` + rapor.

⚠️ CACHE: `.serap_cache` KULLANILMAZ — oradaki adaylar BOZUK metrics ile üretildi,
   metrics alanlarını taşımıyorlar. Bu modül kendi `.metrics_etki_cache`'ini kurar
   (aggTrades yeniden işlenir → AĞIR, ay ay ilerleme yazar; re-run cache'ten hızlı).

Çalıştırma:
  python oar_metrics_etki.py --symbol BTCUSDT,ETHUSDT --from 2019-01 --to 2025-06 --telegram
  python oar_metrics_etki.py --kendi-test     # parquet GEREKMEZ — yamanın doğruluğu
"""
import argparse
import json
import pickle
from datetime import datetime, timezone
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent / ".metrics_etki_cache"
CIKTI = "oar_metrics_etki_sonuc.json"
PORTFOY = "oar_sampiyon_portfoy.json"
METRICS_BLOKLARI = ("oi_yuksek", "whale_retail_zit", "oi_tuzak")
N_DENEME = 300          # şampiyon keşfiyle aynı konservatif çoklu-deneme cezası


# ═══════════════════════════════════════════════════════════════════════════════
#  YAMA — düzeltilmiş _metrics_oku (şampiyon dosyasına YAZMADAN, çalışma anında)
# ═══════════════════════════════════════════════════════════════════════════════
def _metrics_oku_duzeltilmis(sembol, bas, bit, borsa="binance"):
    """`_metrics_oku` ile AYNI, tek fark: ts_ms çözünürlükten BAĞIMSIZ kurulur."""
    import pandas as pd
    from data_ingest import _aylar
    from oar_local_backtest import _hist_dir
    from oar_wsd_backtest import _ts_ms, _olcek_dogrula

    kok = _hist_dir() / borsa / sembol / "metrics"
    parcalar = []
    for yil, ay in _aylar(bas, bit):
        yol = kok / f"{yil:04d}" / f"{sembol}-metrics-{yil:04d}-{ay:02d}.parquet"
        if yol.exists():
            parcalar.append(pd.read_parquet(yol))
    if not parcalar:
        return None
    df = pd.concat(parcalar, ignore_index=True)
    kolon = "create_time" if "create_time" in df.columns else "ts_ms"
    df["ts_ms"] = _ts_ms(df[kolon])
    _olcek_dogrula(f"{sembol} metrics ({kolon})", df["ts_ms"].to_numpy())
    return df


def yama_uygula():
    """
    `_metrics_oku`yu HAFIZADA düzeltilmiş sürümle değiştirir. Dosyaya YAZMAZ.
    `oar_sampiyon_confirm` bu adı kendi ad-alanına import ettiği için ORAYA DA
    yamalanır (yoksa havuz yine bozuk metrics ile kurulur — sessiz başarısızlık).
    Döner: geri alma fonksiyonu.
    """
    import oar_local_backtest as olb
    import oar_sampiyon_confirm as osc

    eski_olb = olb._metrics_oku
    eski_osc = getattr(osc, "_metrics_oku", None)
    olb._metrics_oku = _metrics_oku_duzeltilmis
    if eski_osc is not None:
        osc._metrics_oku = _metrics_oku_duzeltilmis

    def geri_al():
        olb._metrics_oku = eski_olb
        if eski_osc is not None:
            osc._metrics_oku = eski_osc
    return geri_al


# ═══════════════════════════════════════════════════════════════════════════════
#  HAVUZ (düzeltilmiş metrics ile) — kendi cache'i
# ═══════════════════════════════════════════════════════════════════════════════
def _cache_oku(anahtar):
    yol = CACHE_DIR / f"{anahtar}.pkl"
    if yol.exists():
        try:
            with open(yol, "rb") as f:
                return pickle.load(f)
        except Exception:
            return None
    return None


def _cache_yaz(anahtar, veri):
    CACHE_DIR.mkdir(exist_ok=True)
    with open(CACHE_DIR / f"{anahtar}.pkl", "wb") as f:
        pickle.dump(veri, f)


def havuz_kur(semboller, bas, bit, taze=False):
    """Düzeltilmiş metrics ile aday havuzu (şampiyonun KENDİ üreticisiyle)."""
    from oar_sampiyon_confirm import _analiz

    anahtar = f"havuz_{'_'.join(semboller)}_{bas}_{bit}".replace("-", "")
    havuz = None if taze else _cache_oku(anahtar)
    if havuz:
        print(f"   ✓ cache'ten {len(havuz)} aday (yeniden işlenmedi)", flush=True)
        return havuz

    havuz = []
    for sym in semboller:
        print(f"   [{sym}] havuz kuruluyor (aggTrades — AĞIR, ay ay)…", flush=True)
        adaylar, _ = _analiz(sym, bas, bit)
        havuz += adaylar
    havuz.sort(key=lambda c: c["ts"])
    _cache_yaz(anahtar, havuz)
    return havuz


def kapsam_olc(havuz):
    """Kaç aday metrics alanlarını taşıyor (bozuk halde bu sayı 0 olurdu)."""
    say = {b: 0 for b in ("oi_yuksek", "whale_retail_zit")}
    dolu = 0
    for c in havuz:
        var = False
        for b in say:
            if c.get(b) is not None:
                say[b] += 1
                var = True
        dolu += 1 if var else 0
    return {"toplam_aday": len(havuz), "metrics_tasiyan": dolu,
            "oran_pct": round(100.0 * dolu / len(havuz), 1) if havuz else 0.0,
            "blok_dolu": say}


# ═══════════════════════════════════════════════════════════════════════════════
#  ÖLÇÜMLER
# ═══════════════════════════════════════════════════════════════════════════════
def _sistem_metrik(ad, trades, n_deneme=N_DENEME):
    """Bir işlem kümesi → temel metrikler + serap karnesi."""
    import numpy as np
    from oar_serap_testi import _temel, serap_karnesi

    pcts = [c["pct"] for c in trades]
    tsler = [c["ts"] for c in trades]
    kart = {"ad": ad, "n": len(pcts)}
    if len(pcts) < 30:
        kart["not"] = "n<30 — ölçülmedi"
        return kart
    kart.update(_temel(np.asarray(pcts, dtype=float)))
    kart["serap"] = serap_karnesi(ad, pcts, tsler, n_deneme)
    return kart


METRICS_ALANLARI = ("oi_yuksek", "whale_retail_zit")


def incumbent_kontrol(havuz):
    """
    ② GÜVENLİK — KANIT DÜZEYİNDE: düzeltme yalnız YENİ ALAN EKLER. O hâlde metrics
    alanlarını okumayan şampiyon blokları için işlem kümesi DEĞİŞMEMELİ.

    Test: aynı havuzda (a) metrics alanları VARKEN (b) alanlar SİLİNMİŞKEN filtrele.
    İki küme birebir aynıysa düzeltmenin şampiyona etkisi olamayacağı KANITLANIR —
    portföy json'daki PF ile kıyaslamak yanıltıcı olurdu (o kayıt farklı tarih
    aralığı/çıkış ile üretildi, meşru sebeplerle sapar → sahte alarm).
    """
    from oar_kesif import _filtre

    havuz_metricssiz = [{k: v for k, v in c.items() if k not in METRICS_ALANLARI}
                        for c in havuz]
    portfoy = json.loads(Path(PORTFOY).read_text(encoding="utf-8"))
    out = []
    for stil in portfoy.get("stiller", []):
        bloklar = stil["bloklar"]
        trades = _filtre(havuz, bloklar)
        trades_ref = _filtre(havuz_metricssiz, bloklar)
        k = _sistem_metrik(f"INCUMBENT · {stil['stil']}", trades)
        k["bloklar"] = bloklar
        k["n_metrics_alansiz"] = len(trades_ref)
        k["islem_kumesi_ayni"] = ([c["ts"] for c in trades] == [c["ts"] for c in trades_ref])
        k["sapma_uyarisi"] = not k["islem_kumesi_ayni"]
        k["portfoy_kayitli_bilgi"] = {"pf": stil.get("pf"), "beklenti": stil.get("beklenti")}
        out.append(k)
    return out


def kesif_calistir(havuz):
    """③ KEŞİF: tam blok havuzuyla kesfet — metrics blokları artık yarışıyor."""
    from oar_kesif import kesfet
    from oar_sinyaller import AKTIF_BLOKLAR

    print(f"   kesfet çalışıyor ({len(AKTIF_BLOKLAR)} blok, {len(havuz)} aday)…", flush=True)
    sonuc = kesfet(havuz, blok_havuzu=list(AKTIF_BLOKLAR), ust_n=8)
    return sonuc


def yeni_adaylar(kesif, havuz, incumbent_bloklar):
    """
    ④ SERAP: metrics bloğu İÇEREN adayları ayır → serap bateri.
    Metrics bloğu içermeyen aday bu düzeltmenin sonucu DEĞİLDİR (zaten yarışıyordu).
    """
    from oar_kesif import _filtre

    out = []
    for a in kesif.get("en_iyiler", []):
        bloklar = a["bloklar"]
        if not any(b in METRICS_BLOKLARI for b in bloklar):
            continue
        if sorted(bloklar) in [sorted(x) for x in incumbent_bloklar]:
            continue
        k = _sistem_metrik("YENİ · " + "+".join(bloklar), _filtre(havuz, bloklar))
        k["bloklar"] = bloklar
        k["oos_puan"] = a.get("oos_puan")
        k["holdout_puan"] = a.get("holdout_puan")
        k["saglam"] = a.get("saglam")
        out.append(k)
    return out


def _karar_ver(kartlar):
    """BH-FDR + serap kararı (oar_serap_testi ile AYNI eşikler)."""
    from oar_serap_testi import _bh_fdr, _karar

    ad_p = [(k["ad"], k["serap"]["permutasyon_p"]) for k in kartlar if "serap" in k]
    fdr = _bh_fdr(ad_p, q=0.05) if ad_p else {}
    for k in kartlar:
        if "serap" in k:
            k["fdr_gecti"] = bool(fdr.get(k["ad"], False))
            k["karar"] = _karar(k["serap"], k["fdr_gecti"])
    return kartlar


# ═══════════════════════════════════════════════════════════════════════════════
#  RAPOR
# ═══════════════════════════════════════════════════════════════════════════════
def _sat(k):
    if "serap" not in k:
        return f"  {k['ad']:<48} n{k.get('n', 0):<6} {k.get('not', '—')}"
    s = k["serap"]
    dsr = (s.get("deflated_sharpe") or {}).get("dsr")
    ci = (s.get("bootstrap_beklenti_ci") or {}).get("alt")
    lik = ((s.get("mc_equity") or {}).get("5x") or {}).get("likidasyon_orani")
    g = lambda v, w: f"{('—' if v is None else v)!s:<{w}}"
    return (f"  {k['ad']:<48} n{g(k.get('n'), 6)} WR%{g(k.get('wr'), 5)} PF {g(k.get('pf'), 6)}"
            f" bek {g(k.get('beklenti'), 8)} DD%{g(k.get('maxdd_1x_pct'), 5)}"
            f" DSR {g(dsr, 6)} CI-alt {g(ci, 8)} 5xlik {g(lik, 6)} {k.get('karar', '')}")


def rapor_metni(sonuc):
    kap = sonuc["kapsam"]
    inc = sonuc["incumbent"]
    yeni = sonuc["yeni_adaylar"]

    sapma = [k for k in inc if k.get("sapma_uyarisi")]
    gecen = [k for k in yeni if "GERÇEK EDGE" in k.get("karar", "")]

    if gecen:
        bas = f"Metrics düzeltmesi {len(gecen)} SERAP-GEÇER yeni aday çıkardı: " + \
              ", ".join("+".join(k["bloklar"]) for k in gecen)
    elif yeni:
        bas = f"Metrics blokları artık yarışıyor ({len(yeni)} aday) ama HİÇBİRİ serap-geçmedi → şampiyon DEĞİŞMEZ"
    else:
        bas = "Metrics blokları yarıştı, kesfet ilk sıralara HİÇ metrics kombinasyonu koymadı → şampiyon DEĞİŞMEZ"

    sat = [
        "═══ METRICS ZAMAN-DAMGASI DÜZELTMESİNİN ETKİSİ ═══",
        bas,
        f"Aralık {sonuc['aralik']} · {', '.join(sonuc['semboller'])}",
        "",
        "① KAPSAM (düzeltme ne kadar veri açtı)",
        f"  toplam aday {kap['toplam_aday']} · metrics alanı taşıyan {kap['metrics_tasiyan']}"
        f" (%{kap['oran_pct']}) · ÖNCESİ: 0 (blok None → kesfet pas geçiyordu)",
        f"  oi_yuksek dolu {kap['blok_dolu']['oi_yuksek']} · whale_retail_zit dolu"
        f" {kap['blok_dolu']['whale_retail_zit']}",
        "",
        "② GÜVENLİK — incumbent şampiyonlar düzeltilmiş havuzda (DEĞİŞMEMELİ)",
    ]
    sat += [_sat(k) for k in inc]
    sat += ["", "  KANIT — metrics alanları silinince şampiyon işlem kümesi değişiyor mu:"]
    for k in inc:
        isaret = "✓ AYNI" if k.get("islem_kumesi_ayni") else "⛔ FARKLI"
        sat.append(f"    {k['bloklar']} → n {k.get('n')} vs {k.get('n_metrics_alansiz')}  {isaret}")
    if sapma:
        sat += ["", "  ⛔ DUR: şampiyon işlem kümesi değişti — düzeltmenin yan etkisi VAR.",
                "     Kalıcı düzeltmeyi UYGULAMA, önce bunu incele."]
    else:
        sat += ["  ✓ İki küme birebir aynı → düzeltme şampiyonları DEĞİŞTİREMEZ (kanıtlandı).",
                "    (şampiyon blokları metrics alanlarını okumuyor; düzeltme yalnız alan EKLİYOR)"]
    sat += ["", "③④ METRICS BLOĞU İÇEREN YENİ ADAYLAR (serap bateriyle)"]
    sat += [_sat(k) for k in yeni] if yeni else ["  (kesfet ilk sıralarına metrics kombinasyonu koymadı)"]
    sat += [
        "",
        "KARAR KURALI: yeni aday ancak DSR≥0.95 ∧ CI-alt>0 ∧ p<0.05 ∧ FDR ∧ 5x-likid=0",
        "ise ADAY olur; portföye alınması ayrıca walk-forward (oar_walkforward) + onay ister.",
        "",
        "⚠️ Bu koşu ŞAMPİYON PORTFÖYÜNE YAZMADI. oar_sampiyon_portfoy.json dokunulmadı.",
    ]
    return "\n".join(sat)


# ═══════════════════════════════════════════════════════════════════════════════
#  SELF-TEST (parquet GEREKMEZ)
# ═══════════════════════════════════════════════════════════════════════════════
def kendi_test():
    """Yamanın gerçekten metrics alanlarını açtığını sentetik parquet ile kanıtlar."""
    import os
    import tempfile
    import numpy as np
    import pandas as pd

    kok = Path(tempfile.mkdtemp(prefix="metrics_etki_"))
    os.environ["OAR_HIST_DIR"] = str(kok)
    rng = np.random.default_rng(11)

    for ay in (1, 2, 3):
        n = pd.Period(f"2023-{ay:02d}").days_in_month * 1440
        t0 = int(pd.Timestamp(f"2023-{ay:02d}-01", tz="UTC").timestamp() * 1000)
        ot = t0 + np.arange(n) * 60_000
        px = 30000 * np.exp(np.cumsum(rng.normal(0, 0.0006, n)))
        d = kok / "binance" / "BTCUSDT" / "klines" / "2023"
        d.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"open_time": ot, "open": px, "high": px * 1.0004,
                      "low": px * 0.9996, "close": px, "volume": np.ones(n)}) \
            .to_parquet(d / f"BTCUSDT-1m-2023-{ay:02d}.parquet", index=False)

        m5 = pd.date_range(f"2023-{ay:02d}-01", periods=n // 5, freq="5min")
        d = kok / "binance" / "BTCUSDT" / "metrics" / "2023"
        d.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({
            "create_time": [t.strftime("%Y-%m-%d %H:%M:%S") for t in m5],
            "symbol": "BTCUSDT", "sum_open_interest": 1e6 + rng.normal(0, 5e4, len(m5)),
            "sum_open_interest_value": 1e10,
            "count_toptrader_long_short_ratio": 1.2,
            "sum_toptrader_long_short_ratio": 1.2 + rng.normal(0, .05, len(m5)),
            "count_long_short_ratio": 1.5,
            "sum_taker_long_short_vol_ratio": 1.0,
        }).to_parquet(d / f"BTCUSDT-metrics-2023-{ay:02d}.parquet", index=False)

    import importlib
    import oar_local_backtest as olb
    importlib.reload(olb)

    k = olb._klines_oku("BTCUSDT", "2023-01", "2023-03")
    bozuk = olb._gun_hazirla(k, [], olb._metrics_oku("BTCUSDT", "2023-01", "2023-03"))
    duz = olb._gun_hazirla(k, [], _metrics_oku_duzeltilmis("BTCUSDT", "2023-01", "2023-03"))

    alan = lambda g: sorted(x for x in g[sorted(g)[len(g) // 2]] if "oi_" in x or "ls_map" in x)
    print(f"[SELF-TEST] MEVCUT  _metrics_oku → {alan(bozuk) or 'HİÇ METRİK ALANI YOK'}")
    print(f"[SELF-TEST] YAMALI  sürüm       → {alan(duz)}")
    assert not alan(bozuk), "mevcut sürüm beklenmedik şekilde metrik üretti"
    assert "oi_map" in alan(duz), "yama metrics alanlarını açamadı"

    geri = yama_uygula()
    try:
        assert olb._metrics_oku is _metrics_oku_duzeltilmis, "yama uygulanmadı"
        import oar_sampiyon_confirm as osc
        assert osc._metrics_oku is _metrics_oku_duzeltilmis, "confirm ad-alanı yamalanmadı"
        print("[SELF-TEST] ✓ yama iki ad-alanına da uygulandı")
    finally:
        geri()
    assert olb._metrics_oku is not _metrics_oku_duzeltilmis, "yama geri alınamadı"
    print("[SELF-TEST] ✓ yama geri alındı (kalıcı değişiklik YOK)")
    print("[SELF-TEST] ✓ hazır — gerçek koşu şampiyon dosyasına dokunmadan ölçer")
    return True


# ═══════════════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT,ETHUSDT")
    ap.add_argument("--from", dest="bas", default="2019-01")
    ap.add_argument("--to", dest="bit", default="2025-06")
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--taze", action="store_true", help="cache'i yok say, sıfırdan kur")
    ap.add_argument("--kendi-test", action="store_true")
    args = ap.parse_args()

    if args.kendi_test:
        kendi_test()
        return

    semboller = [s.strip().upper() for s in args.symbol.split(",") if s.strip()]
    print("═" * 70)
    print("METRICS ZAMAN-DAMGASI ETKİ ÖLÇÜMÜ — şampiyon dosyasına YAZMAZ")
    print("═" * 70, flush=True)

    geri = yama_uygula()
    try:
        havuz = havuz_kur(semboller, args.bas, args.bit, taze=args.taze)
        if not havuz:
            print("❌ havuz boş — klines/aggTrades/metrics parquet indirilmiş mi?")
            return

        kapsam = kapsam_olc(havuz)
        print(f"   ① kapsam: {kapsam['metrics_tasiyan']}/{kapsam['toplam_aday']} aday"
              f" metrics taşıyor (%{kapsam['oran_pct']})", flush=True)
        if kapsam["metrics_tasiyan"] == 0:
            print("   ⛔ düzeltmeye rağmen 0 — metrics parquet indirilmemiş olabilir.")

        print("   ② incumbent şampiyonlar kontrol ediliyor (işlem kümesi kanıtı)…", flush=True)
        inc = incumbent_kontrol(havuz)
        if any(k.get("sapma_uyarisi") for k in inc):
            print("   ⛔ şampiyon işlem kümesi değişti — kalıcı düzeltmeyi UYGULAMA", flush=True)

        kesif = kesif_calistir(havuz)
        yeni = yeni_adaylar(kesif, havuz, [s["bloklar"] for s in inc])
        _karar_ver(inc + yeni)

        sonuc = {"tarih": datetime.now(timezone.utc).isoformat(),
                 "aralik": f"{args.bas}..{args.bit}", "semboller": semboller,
                 "kapsam": kapsam, "incumbent": inc, "yeni_adaylar": yeni,
                 "kesif_ilk_siralar": kesif.get("en_iyiler", []),
                 "not": "oar_sampiyon_portfoy.json DOKUNULMADI"}

        rapor = rapor_metni(sonuc)
        print("\n" + rapor)
        Path(CIKTI).write_text(json.dumps(sonuc, ensure_ascii=False, indent=2, default=str),
                               encoding="utf-8")
        print(f"\n💾 {CIKTI} yazıldı (git-senkron)")

        if args.telegram:
            try:
                import asyncio
                from ajan_merkez import bildir
                asyncio.run(bildir("Metrics Etki Ölçümü", "backtest",
                                   rapor.split("\n", 2)[1], detay=rapor))
                print("[Telegram] thread 4129'a gönderildi ✓", flush=True)
            except Exception as e:
                print(f"[Telegram] gönderilemedi: {str(e)[:80]}", flush=True)
    finally:
        geri()
        print("\n(yama geri alındı — kalıcı kod değişikliği YAPILMADI)")


if __name__ == "__main__":
    main()
