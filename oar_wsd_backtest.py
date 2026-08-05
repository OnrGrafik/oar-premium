"""
oar_wsd_backtest.py — WSD (Whale Size Delta) TARİHSEL DOĞRULAMA (LOCAL, parquet)
═══════════════════════════════════════════════════════════════════════════════════════
NEDEN: sinyal-bot (§6f) canlıda WSD'yi KARAR metriği olarak kullanıyor ama bu metrik
HİÇ backtest edilmedi. "Mantıklı görünüyor" ≠ edge (§5e güven ilkesi). Bu modül WSD'yi
2019→bugün metrics parquet'inden ölçer ve serap bateriyle (DSR≥0.95) yargılar.

METRİK TANIMLARI (metrics parquet, 5m satır — data_ingest.METRICS_COLS):
  sum_toptrader_long_short_ratio    → top trader POZİSYON ağırlıklı L/S oranı
  count_toptrader_long_short_ratio  → top trader HESAP    ağırlıklı L/S oranı
  count_long_short_ratio            → TÜM hesaplar (retail) L/S oranı
  sum_taker_long_short_vol_ratio    → taker alış/satış HACİM oranı (notional akış)

  oran → long% dönüşümü:  long% = r/(1+r)·100     (r = long/short)

  ⭐ WSD  = long%(pozisyon) − long%(hesap)     ← AYNI KOHORT, tek fark size ağırlığı
       Yapısal offset YOK → İŞARETİ gerçek sinyal. + = büyük pozisyonlar hesap
       sayısından daha LONG (para long tarafta).
     KOHORT = long%(top hesap) − long%(retail hesap)   (ikisi de hesap-ağırlıklı)
     TAKER  = long%(taker hacim)                       (§6f'de "sıradaki aday" denen ölçüm)
     WRD    = long%(pozisyon) − long%(retail hesap)    (LEGACY türev — yapısal offset
              taşır, işareti sinyal DEĞİL; yalnız kıyas için test edilir)

NO-LOOKAHEAD KURALLARI (hepsi zorunlu):
  • Karar anı = 04:00 UTC (Asya seansı bitişi — OAR ile tutarlı). Metrik satırı bu anın
    EN FAZLA 30 dk öncesinden olmalı (bayat satır işleme sokulmaz).
  • Percentile penceresi = ÖNCEKİ 30 gün (o günün kendi değeri pencereye GİRMEZ).
  • SL genişliği = ÖNCEKİ 20 günün ortalama günlük range%'i (o gün dahil DEĞİL).

İŞLEM MODELİ (ev standardı):
  Giriş = 04:00 UTC bar kapanışı · SL = 1.0·σ_gün · TP = 3R (§3 TP_3R) · time-stop 24s.
  Aynı barda hem SL hem TP mümkünse SL ÖNCE (KÖTÜMSER). Fee+slippage düşülür.
  İşlem sonucu YALNIZ (gün, yön)'e bağlıdır → gün başına bir kez simüle edilir, tüm
  varyantlar aynı havuzdan seçer (varyantlar arası kıyas birebir adil olur).

YARGI: her varyant → n/WR/PF/beklenti/maxDD/OOS + serap karnesi (DSR, permütasyon p,
bootstrap CI, MC likidasyon) + BH-FDR (çoklu-karşılaştırma). ✅ GERÇEK EDGE ancak
DSR≥0.95 ∧ CI-alt>0 ∧ p<0.05 ∧ FDR ∧ 5x-likidasyon=0 ise.

⚠️ ŞAMPİYONLARA DOKUNMAZ (ANAYASA #8). Bu bir ÖLÇÜM modülüdür; çıkan sonuç serap
   testinden geçse bile canlıya bağlama AYRI onay ister (§5p dersi).

Çalıştırma:
  python oar_wsd_backtest.py --symbol BTCUSDT,ETHUSDT --from 2019-01 --to 2025-06 --telegram
  python oar_wsd_backtest.py --kendi-test        # parquet GEREKMEZ — mekanik doğrulama
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from oar_local_backtest import (_klines_oku, _metrics_oku, _ms_olcekle,
                                FEE_PCT, SLIP_PCT, GUN_MS, SAAT_MS)

KARAR_SAAT_UTC = 4.0     # Asya bitişi (OAR seans tanımıyla aynı)
TAZELIK_DK     = 30      # metrik satırı karar anından en fazla bu kadar eski olabilir
PCT_PENCERE    = 30      # percentile penceresi (gün) — STRICT geçmiş
SIGMA_PENCERE  = 20      # σ_gün penceresi (gün) — STRICT geçmiş
TUT_SAAT       = 24      # time-stop
TP_R           = 3.0     # TP = 3R (§3 ev standardı)
EKSTREM_UST    = 80.0    # percentile üst eşiği
EKSTREM_ALT    = 20.0    # percentile alt eşiği
MIN_N          = 40
OOS_ORAN       = 0.20
N_DENEME       = 40      # DSR çoklu-karşılaştırma cezası (14 varyant × konfig seçimleri)
CIKTI          = "wsd_backtest_sonuc.json"

OT_MIN, OT_MAX = 1_400_000_000_000, 2_000_000_000_000


# ═══════════════════════════════════════════════════════════════════════════════
#  METRİK DÖNÜŞÜMLERİ
# ═══════════════════════════════════════════════════════════════════════════════
def _ts_ms(seri):
    """
    Zaman kolonu → epoch MİLİSANİYE. pandas ÇÖZÜNÜRLÜĞÜNDEN BAĞIMSIZ.

    ⚠️ NEDEN AYRI: `_metrics_oku` şunu yapıyor →
        pd.to_datetime(create_time).astype("int64") // 1_000_000
    pandas 1.x'te to_datetime HEP datetime64[ns] veriyordu → //1e6 = ms (doğru).
    pandas 2.0+ çözünürlüğü KORUYOR ("2023-01-01 00:00:00" → [s] veya [us]) →
    aynı bölme SANİYE üretiyor, yani 1000× küçük. `_ms_olcekle` yalnız AŞAĞI
    ölçekler (ns/µs→ms), saniyeyi düzeltmez → metrik satırları 40 yıl bayat
    görünür ve sessizce elenir. Bu fonksiyon her iki dünyada da doğru sonucu verir.
    """
    import numpy as np
    import pandas as pd

    if pd.api.types.is_numeric_dtype(seri):
        arr = np.asarray(seri, dtype="float64")
        out = arr.copy()
        out[arr < 1e11] = arr[arr < 1e11] * 1_000.0            # s  → ms
        out[(arr >= 1e14) & (arr < 1e17)] = arr[(arr >= 1e14) & (arr < 1e17)] / 1_000.0   # µs → ms
        out[arr >= 1e17] = arr[arr >= 1e17] / 1_000_000.0      # ns → ms
        return out.astype("int64")

    dt = pd.to_datetime(seri, errors="coerce", utc=True)
    epoch = pd.Timestamp("1970-01-01", tz="UTC")
    return ((dt - epoch) // pd.Timedelta("1ms")).to_numpy(dtype="int64")


def _olcek_dogrula(ad, arr):
    """Zaman dizisi makul epoch-ms aralığında mı (2014–2033). Değilse HATA — sessiz düşme YOK."""
    import numpy as np
    if not len(arr):
        return
    orta = float(np.median(np.asarray(arr, dtype="float64")))
    if not (OT_MIN <= orta < OT_MAX):
        raise ValueError(
            f"{ad}: zaman ölçeği bozuk (medyan {orta:.0f} → "
            f"{_utc(orta).year}). Beklenen epoch-ms.")


def _utc(ms):
    """epoch-ms → UTC datetime (tz-aware; utcfromtimestamp deprecation'ı yok)."""
    return datetime.fromtimestamp(max(float(ms), 0) / 1000, tz=timezone.utc)


def long_pct(oran):
    """L/S oranı → long yüzdesi. r=1 → %50. Geçersiz/negatif → None."""
    try:
        r = float(oran)
    except (TypeError, ValueError):
        return None
    if r <= 0 or r != r:          # negatif veya NaN
        return None
    return r / (1.0 + r) * 100.0


def _metrikler(satir):
    """Bir metrics satırından WSD/KOHORT/TAKER/WRD üret. Eksikse None döner."""
    poz   = long_pct(satir.get("sum_toptrader_long_short_ratio"))
    hesap = long_pct(satir.get("count_toptrader_long_short_ratio"))
    retail = long_pct(satir.get("count_long_short_ratio"))
    taker = long_pct(satir.get("sum_taker_long_short_vol_ratio"))
    return {
        "wsd":    (poz - hesap) if (poz is not None and hesap is not None) else None,
        "kohort": (hesap - retail) if (hesap is not None and retail is not None) else None,
        "taker":  taker,
        "wrd":    (poz - retail) if (poz is not None and retail is not None) else None,
        "poz_long": poz, "hesap_long": hesap, "retail_long": retail,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  GÜNLÜK TABLO (no-lookahead)
# ═══════════════════════════════════════════════════════════════════════════════
def gun_tablosu(sembol, bas, bit, k_df=None, m_df=None):
    """
    Döner: (satirlar, hi, lo, cl)
      satirlar[] = {gun, ts, giris, sigma, wsd, kohort, taker, wrd, i0, i1}
      hi/lo/cl   = numpy dizileri (klines) — satır i0..i1 bu dizilere indeksler
    k_df/m_df verilirse parquet OKUNMAZ (self-test seam'i).
    """
    import numpy as np
    import pandas as pd

    k = k_df if k_df is not None else _klines_oku(sembol, bas, bit)
    if k is None or not len(k):
        print(f"      ⚠ {sembol}: klines yok", flush=True)
        return [], None, None, None
    m = m_df if m_df is not None else _metrics_oku(sembol, bas, bit)
    if m is None or not len(m):
        print(f"      ⚠ {sembol}: metrics parquet yok (data_ingest ile 'metrics' indir)", flush=True)
        return [], None, None, None

    k = k.copy()
    k["open_time"] = _ms_olcekle(k["open_time"])
    gecerli = (k["open_time"] >= OT_MIN) & (k["open_time"] < OT_MAX)
    if not bool(gecerli.all()):
        print(f"      ⚠ {int((~gecerli).sum())} bozuk klines satırı atlandı", flush=True)
        k = k[gecerli]
    k = k.sort_values("open_time")

    ot = k["open_time"].to_numpy(dtype="int64")
    hi = k["high"].to_numpy(dtype="float64")
    lo = k["low"].to_numpy(dtype="float64")
    cl = k["close"].to_numpy(dtype="float64")
    op = k["open"].to_numpy(dtype="float64")

    # ⚠️ ts_ms'i _metrics_oku'dan ALMIYORUZ (pandas 2.0+ çözünürlük tuzağı — _ts_ms
    #    docstring'ine bak). Ham create_time'dan yeniden, çözünürlükten bağımsız kurulur.
    m = m.copy()
    zaman_kolon = "create_time" if "create_time" in m.columns else "ts_ms"
    m["ts_ms"] = _ts_ms(m[zaman_kolon])
    m = m.sort_values("ts_ms")
    mts = m["ts_ms"].to_numpy(dtype="int64")
    _olcek_dogrula(f"{sembol} metrics ({zaman_kolon})", mts)
    _olcek_dogrula(f"{sembol} klines", ot)
    m_kayit = m.to_dict("records")

    # σ_gün: önceki SIGMA_PENCERE gününün ortalama günlük range%'i (STRICT geçmiş)
    gun_arr = ot // GUN_MS
    gd = pd.DataFrame({"gun": gun_arr, "high": hi, "low": lo, "open": op}) \
           .groupby("gun").agg(h=("high", "max"), l=("low", "min"), o=("open", "first"))
    rngs = ((gd["h"] - gd["l"]) / gd["o"] * 100.0).to_numpy(dtype="float64")
    gunler = gd.index.to_numpy()
    sigma_map = {}
    for i in range(SIGMA_PENCERE, len(gunler)):
        pencere = rngs[i - SIGMA_PENCERE:i]
        if np.isfinite(pencere).all() and pencere.mean() > 0:
            sigma_map[int(gunler[i])] = float(pencere.mean())

    satirlar = []
    son_ay = ""
    red = {"sigma_penceresi": 0, "giris_bari": 0, "ileri_pencere": 0, "metrik_bayat": 0}
    for g in gunler:
        g = int(g)
        sigma = sigma_map.get(g)
        if sigma is None:
            red["sigma_penceresi"] += 1
            continue
        karar_ts = g * GUN_MS + int(KARAR_SAAT_UTC * SAAT_MS)

        ay = datetime.fromtimestamp(karar_ts / 1000, tz=timezone.utc).strftime("%Y-%m")
        if ay != son_ay:
            print(f"      · {ay} analiz ediliyor…", flush=True)
            son_ay = ay

        # giriş barı: karar anına ait/önceki 1m bar (en fazla 5 dk boşluk)
        gi = int(np.searchsorted(ot, karar_ts, side="right")) - 1
        if gi < 0 or karar_ts - int(ot[gi]) > 5 * 60_000:
            red["giris_bari"] += 1
            continue
        # ileri yol: giriş barından sonra TUT_SAAT boyunca
        i0 = gi + 1
        i1 = int(np.searchsorted(ot, karar_ts + TUT_SAAT * SAAT_MS, side="right"))
        if i1 - i0 < 60:
            red["ileri_pencere"] += 1
            continue
        # metrik satırı: karar anından en fazla TAZELIK_DK eski
        mi = int(np.searchsorted(mts, karar_ts, side="right")) - 1
        if mi < 0 or karar_ts - int(mts[mi]) > TAZELIK_DK * 60_000:
            red["metrik_bayat"] += 1
            continue

        mv = _metrikler(m_kayit[mi])
        satirlar.append({"gun": g, "ts": karar_ts, "giris": float(cl[gi]),
                         "sigma": sigma, "i0": i0, "i1": i1, **mv})

    if red["metrik_bayat"] or not satirlar:
        ara = lambda a: (f"{_utc(a[0]):%Y-%m-%d} → {_utc(a[-1]):%Y-%m-%d}") if len(a) else "boş"
        print(f"      · elenen gün: {red} | klines {ara(ot)} | metrics {ara(mts)}", flush=True)
    _pct_ekle(satirlar, ["wsd", "kohort", "taker", "wrd"])
    return satirlar, hi, lo, cl


def _pct_ekle(satirlar, alanlar):
    """Her alan için ÖNCEKİ PCT_PENCERE günün içindeki percentile (o gün hariç)."""
    for alan in alanlar:
        gecmis = []
        for r in satirlar:
            v = r.get(alan)
            pencere = [x for x in gecmis[-PCT_PENCERE:] if x is not None]
            if v is not None and len(pencere) >= PCT_PENCERE // 2:
                r[alan + "_pct"] = round(100.0 * sum(1 for x in pencere if x < v) / len(pencere), 1)
            else:
                r[alan + "_pct"] = None
            gecmis.append(v)


# ═══════════════════════════════════════════════════════════════════════════════
#  İŞLEM SİMÜLASYONU (gün × yön — varyantlardan bağımsız, bir kez)
# ═══════════════════════════════════════════════════════════════════════════════
def _net(yon, giris, cikis):
    ham = (cikis - giris) / giris * 100.0
    if yon == "SHORT":
        ham = -ham
    return ham - FEE_PCT - SLIP_PCT


def _simule(yon, r, hi, lo, cl):
    """Tek işlem: SL=1.0·σ, TP=3R, time-stop. Aynı barda SL ÖNCE (kötümser)."""
    import numpy as np
    giris, sigma = r["giris"], r["sigma"]
    i0, i1 = r["i0"], r["i1"]
    if yon == "LONG":
        sl = giris * (1.0 - sigma / 100.0)
        tp = giris + TP_R * (giris - sl)
        sl_vur = lo[i0:i1] <= sl
        tp_vur = hi[i0:i1] >= tp
    else:
        sl = giris * (1.0 + sigma / 100.0)
        tp = giris - TP_R * (sl - giris)
        sl_vur = hi[i0:i1] >= sl
        tp_vur = lo[i0:i1] <= tp
    si = int(np.argmax(sl_vur)) if bool(sl_vur.any()) else -1
    ti = int(np.argmax(tp_vur)) if bool(tp_vur.any()) else -1
    if si == -1 and ti == -1:
        return _net(yon, giris, float(cl[i1 - 1])), "TIME"
    if ti == -1 or (si != -1 and si <= ti):     # eşitlikte SL (kötümser)
        return _net(yon, giris, sl), "SL"
    return _net(yon, giris, tp), "TP"


def islem_havuzu(satirlar, hi, lo, cl):
    """{gun: {"LONG": (net,tur), "SHORT": (net,tur)}} — gün başına iki simülasyon."""
    havuz = {}
    for r in satirlar:
        havuz[r["gun"]] = {"LONG": _simule("LONG", r, hi, lo, cl),
                           "SHORT": _simule("SHORT", r, hi, lo, cl)}
    return havuz


# ═══════════════════════════════════════════════════════════════════════════════
#  VARYANTLAR
# ═══════════════════════════════════════════════════════════════════════════════
def _varyantlar():
    """ad → (yön, seçici(satır)->bool). Taban = her gün (drift kıyası)."""
    def ust(alan):
        return lambda r: r.get(alan + "_pct") is not None and r[alan + "_pct"] >= EKSTREM_UST

    def alt(alan):
        return lambda r: r.get(alan + "_pct") is not None and r[alan + "_pct"] <= EKSTREM_ALT

    def isaret(alan, poz):
        if poz:
            return lambda r: r.get(alan) is not None and r[alan] > 0
        return lambda r: r.get(alan) is not None and r[alan] < 0

    return [
        ("TABAN_LONG",           "LONG",  lambda r: True),
        ("TABAN_SHORT",          "SHORT", lambda r: True),
        ("WSD_ISARET_LONG",      "LONG",  isaret("wsd", True)),
        ("WSD_ISARET_SHORT",     "SHORT", isaret("wsd", False)),
        ("WSD_EKSTREM_LONG",     "LONG",  ust("wsd")),
        ("WSD_EKSTREM_SHORT",    "SHORT", alt("wsd")),
        ("KOHORT_EKSTREM_LONG",  "LONG",  ust("kohort")),
        ("KOHORT_EKSTREM_SHORT", "SHORT", alt("kohort")),
        ("TAKER_EKSTREM_LONG",   "LONG",  ust("taker")),
        ("TAKER_EKSTREM_SHORT",  "SHORT", alt("taker")),
        ("WRD_EKSTREM_LONG",     "LONG",  ust("wrd")),
        ("WRD_EKSTREM_SHORT",    "SHORT", alt("wrd")),
    ]


def _varyant_serisi(satirlar, havuz, yon, sec):
    """Seçilen günlerin (net, ts, tur) serisi — kronolojik."""
    pcts, tsler, turler = [], [], []
    for r in satirlar:
        if not sec(r):
            continue
        net, tur = havuz[r["gun"]][yon]
        pcts.append(net); tsler.append(r["ts"]); turler.append(tur)
    return pcts, tsler, turler


# ═══════════════════════════════════════════════════════════════════════════════
#  DEĞERLENDİRME (temel + OOS + serap bateri + FDR)
# ═══════════════════════════════════════════════════════════════════════════════
def seriler_uret(satirlar, hi, lo, cl):
    """{varyant_adı: (pcts, tsler, turler)} — bir sembolün tüm varyant serileri."""
    havuz = islem_havuzu(satirlar, hi, lo, cl)
    return {ad: _varyant_serisi(satirlar, havuz, yon, sec)
            for ad, yon, sec in _varyantlar()}


def seriler_birlestir(seri_listesi):
    """Birden çok sembolün serilerini varyant bazında birleştirir (ts'e göre kronolojik)."""
    birlesik = {}
    for ad, yon, _ in _varyantlar():
        p, t, tu = [], [], []
        for seriler in seri_listesi:
            sp, st, stu = seriler.get(ad, ([], [], []))
            p += sp; t += st; tu += stu
        sira = sorted(range(len(t)), key=lambda i: t[i])
        birlesik[ad] = ([p[i] for i in sira], [t[i] for i in sira], [tu[i] for i in sira])
    return birlesik


def degerlendir(seriler):
    """Varyant serilerini yargıla: temel + OOS + serap bateri + BH-FDR."""
    import numpy as np
    from oar_serap_testi import _temel, _bh_fdr, _karar, serap_karnesi

    yon_map = {ad: yon for ad, yon, _ in _varyantlar()}
    sonuc = {}
    for ad, (pcts, tsler, turler) in seriler.items():
        yon = yon_map.get(ad, "?")
        n = len(pcts)
        kart = {"ad": ad, "yon": yon, "n": n}
        if n < MIN_N:
            kart["karar"] = f"❓ YETERSİZ N (n={n} < {MIN_N})"
            sonuc[ad] = kart
            continue
        arr = np.asarray(pcts, dtype=float)
        kart.update(_temel(arr))
        kes = int(n * (1 - OOS_ORAN))
        kart["oos_beklenti"] = round(float(arr[kes:].mean()), 4) if n - kes >= 10 else None
        kart["is_beklenti"] = round(float(arr[:kes].mean()), 4)
        kart["sonuc_dagilim"] = {t: turler.count(t) for t in ("TP", "SL", "TIME")}
        kaz = arr[arr > 0]; kay = arr[arr <= 0]
        kart["rr"] = round(float(kaz.mean() / abs(kay.mean())), 2) if len(kaz) and len(kay) else None
        kart["serap"] = serap_karnesi(ad, pcts, tsler, N_DENEME)
        sonuc[ad] = kart

    # BH-FDR: yalnız serap karnesi olan varyantlar üzerinden
    ad_p = [(ad, k["serap"]["permutasyon_p"]) for ad, k in sonuc.items() if "serap" in k]
    fdr = _bh_fdr(ad_p, q=0.05) if ad_p else {}
    for ad, k in sonuc.items():
        if "serap" in k:
            k["fdr_gecti"] = bool(fdr.get(ad, False))
            k["karar"] = _karar(k["serap"], k["fdr_gecti"])
    return sonuc


# ═══════════════════════════════════════════════════════════════════════════════
#  RAPOR
# ═══════════════════════════════════════════════════════════════════════════════
def _g(v, w):
    """None-güvenli sabit-genişlik hücre (rapor tabloları için)."""
    return f"{('—' if v is None else v)!s:<{w}}"


def _sat(k):
    if "serap" not in k:
        return f"  {k['ad']:<22} {k['karar']}"
    s = k["serap"]
    dsr = (s.get("deflated_sharpe") or {}).get("dsr")
    ci = (s.get("bootstrap_beklenti_ci") or {}).get("alt")
    lik = ((s.get("mc_equity") or {}).get("5x") or {}).get("likidasyon_orani")
    return (f"  {k['ad']:<22} n{_g(k.get('n'), 5)} WR%{_g(k.get('wr'), 5)} PF {_g(k.get('pf'), 6)}"
            f" bek {_g(k.get('beklenti'), 8)} OOS {_g(k.get('oos_beklenti'), 8)}"
            f" R:R {_g(k.get('rr'), 5)} DD%{_g(k.get('maxdd_1x_pct'), 5)}"
            f" DSR {_g(dsr, 6)} CI-alt {_g(ci, 8)}"
            f" 5xlik {_g(lik, 6)} {k['karar']}")


def rapor_metni(sonuc, sembol_ozet, bas, bit):
    sirali = sorted(sonuc.values(),
                    key=lambda k: (-(k.get("beklenti") or -99), k["ad"]))
    gecen = [k["ad"] for k in sirali if "GERÇEK EDGE" in k.get("karar", "")]
    marjinal = [k["ad"] for k in sirali if "MARJİNAL" in k.get("karar", "")]
    bas_satir = ("WSD serap-geçer varyant: " + ", ".join(gecen)) if gecen else \
                ("WSD'de GERÇEK EDGE yok — " + (f"marjinal: {', '.join(marjinal)}" if marjinal else "tüm varyantlar serap"))
    sat = [
        "═══ WSD (Whale Size Delta) TARİHSEL DOĞRULAMA ═══",
        bas_satir,
        f"Aralık {bas}..{bit} · {sembol_ozet}",
        f"Karar anı 04:00 UTC · SL=1.0σ_gün · TP=3R · time-stop {TUT_SAAT}s · fee+slip düşülü",
        f"Percentile penceresi {PCT_PENCERE}g (STRICT geçmiş) · DSR n_deneme={N_DENEME}",
        "",
    ]
    sat += [_sat(k) for k in sirali]
    sat += [
        "",
        "YORUM ANAHTARI:",
        "  TABAN_*  = her gün aynı yönde işlem (drift kıyası). Bir varyant TABAN'ı",
        "             geçemiyorsa metrik BİLGİ KATMIYOR demektir.",
        "  WSD      = pozisyon% − hesap% (AYNI kohort) → işareti gerçek sinyal.",
        "  WRD      = LEGACY türev (yapısal offset taşır) — WSD ile kıyas için burada.",
        "  ✅ GERÇEK EDGE ancak DSR≥0.95 ∧ CI-alt>0 ∧ p<0.05 ∧ FDR ∧ 5x-likidasyon=0.",
        "",
        "⚠️ Serap-geçen varyant bile CANLIYA OTOMATİK BAĞLANMAZ (ANAYASA #8) — walk-forward",
        "   (oar_walkforward) ay-ay kör testi + kullanıcı onayı gerekir (§5w London dersi).",
    ]
    return "\n".join(sat)


# ═══════════════════════════════════════════════════════════════════════════════
#  SELF-TEST (parquet GEREKMEZ — mekanik doğrulama)
# ═══════════════════════════════════════════════════════════════════════════════
def kendi_test():
    """
    Sentetik veri: WSD>0 günlerinde sonraki 24s YUKARI sürüklenir, WSD<0 günlerinde
    AŞAĞI. Beklenen: WSD_ISARET_* pozitif beklenti; KOHORT (rastgele) ~sıfır.
    Boru hattının (no-lookahead percentile, SL/TP, kötümser aynı-bar) doğruluğunu
    gerçek veri olmadan kanıtlar.
    """
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(7)
    N_GUN = 420
    bas_ts = 1_600_000_000_000 // GUN_MS * GUN_MS      # gün başlangıcına hizala
    dk_sayisi = N_GUN * 1440

    wsd_gun = rng.normal(0, 3, N_GUN)                   # işaretli sinyal
    kohort_gun = rng.normal(0, 3, N_GUN)                # gürültü (fiyatla ilişkisiz)

    fiyat = 30000.0
    ot, o_, h_, l_, c_ = [], [], [], [], []
    for g in range(N_GUN):
        # ZAYIF eğim (24s'te ~%3.5) — TP 3R'den küçük → TP/SL/TIME karışımı çıkar,
        # böylece kötümser aynı-bar SL yolu da testte gerçekten çalışır.
        egim = 0.00003 if wsd_gun[g] > 0 else -0.00003
        for dk in range(1440):
            # sürüklenme yalnız karar anından (04:00 = 240. dk) SONRA etkili
            mu = egim if dk > 240 else 0.0
            adim = fiyat * (mu + rng.normal(0, 0.0006))
            ac = fiyat
            fiyat = max(1.0, fiyat + adim)
            ot.append(bas_ts + (g * 1440 + dk) * 60_000)
            o_.append(ac); c_.append(fiyat)
            h_.append(max(ac, fiyat) * (1 + abs(rng.normal(0, 0.0002))))
            l_.append(min(ac, fiyat) * (1 - abs(rng.normal(0, 0.0002))))
    k = pd.DataFrame({"open_time": ot, "open": o_, "high": h_, "low": l_,
                      "close": c_, "volume": [1.0] * dk_sayisi})

    # metrics: 5 dk'da bir satır; oranları hedef long%'ten geri üret (long% = r/(1+r))
    def _oran(pct):
        pct = min(max(pct, 1.0), 99.0)
        return pct / (100.0 - pct)

    mts, s_poz, s_hesap, s_retail, s_taker, s_oi = [], [], [], [], [], []
    for g in range(N_GUN):
        for adim in range(288):
            ts = bas_ts + g * GUN_MS + adim * 5 * 60_000
            hesap_pct = 55.0
            poz_pct = hesap_pct + wsd_gun[g]
            retail_pct = hesap_pct - kohort_gun[g]
            mts.append(ts)
            s_poz.append(_oran(poz_pct)); s_hesap.append(_oran(hesap_pct))
            s_retail.append(_oran(retail_pct)); s_taker.append(_oran(50.0 + rng.normal(0, 2)))
            s_oi.append(1e6)
    m = pd.DataFrame({"ts_ms": mts, "sum_open_interest": s_oi,
                      "sum_toptrader_long_short_ratio": s_poz,
                      "count_toptrader_long_short_ratio": s_hesap,
                      "count_long_short_ratio": s_retail,
                      "sum_taker_long_short_vol_ratio": s_taker})

    print("[SELF-TEST] sentetik veri kuruldu — WSD işaretine sürüklenme GÖMÜLÜ", flush=True)
    satirlar, hi, lo, cl = gun_tablosu("TESTUSDT", "", "", k_df=k, m_df=m)
    print(f"[SELF-TEST] {len(satirlar)} karar günü üretildi", flush=True)
    assert len(satirlar) > 300, "gün tablosu beklenenden küçük"

    # no-lookahead kontrolü: ilk PCT_PENCERE//2 gün percentile ÜRETMEMELİ
    assert satirlar[0]["wsd_pct"] is None, "percentile geçmiş penceresi sızdırıyor"
    assert satirlar[-1]["wsd_pct"] is not None, "percentile hiç üretilmedi"

    # metrik tanımı kontrolü: oran→long% ve WSD işareti
    assert abs(long_pct(1.0) - 50.0) < 1e-9, "long_pct(1.0) %50 olmalı"
    assert long_pct(0) is None and long_pct(None) is None, "geçersiz oran None dönmeli"
    ornek = _metrikler({"sum_toptrader_long_short_ratio": _oran(58.0),
                        "count_toptrader_long_short_ratio": _oran(55.0),
                        "count_long_short_ratio": _oran(60.0),
                        "sum_taker_long_short_vol_ratio": 1.0})
    assert abs(ornek["wsd"] - 3.0) < 1e-6, "WSD = pozisyon% − hesap% olmalı"
    assert abs(ornek["kohort"] + 5.0) < 1e-6, "KOHORT = hesap% − retail% olmalı"

    seriler = seriler_uret(satirlar, hi, lo, cl)
    sonuc = degerlendir(seriler)
    print("\n" + rapor_metni(sonuc, "TESTUSDT (sentetik)", "sentetik", "sentetik"))

    # portföy birleştirme yolu (iki sembol) — kronolojik sıra korunuyor mu
    prt = seriler_birlestir([seriler, seriler])
    p_all, t_all, _ = prt["TABAN_LONG"]
    assert len(p_all) == 2 * len(seriler["TABAN_LONG"][0]), "portföy serisi eksik birleşti"
    assert all(t_all[i] <= t_all[i + 1] for i in range(len(t_all) - 1)), "portföy kronolojik değil"
    print("[SELF-TEST] ✓ portföy birleştirme kronolojik ve tam")

    gomulu = sonuc["WSD_ISARET_LONG"]["beklenti"]
    gurultu = sonuc.get("KOHORT_EKSTREM_LONG", {}).get("beklenti")
    print(f"\n[SELF-TEST] gömülü sinyal beklentisi {gomulu} · gürültü {gurultu}")
    assert gomulu > 0, "gömülü sinyal yakalanamadı — boru hattı bozuk"
    assert gomulu > (gurultu or -99), "gömülü sinyal gürültüden ayrışmadı"
    print("[SELF-TEST] ✓ boru hattı doğru (sinyal yakalandı, gürültü ayrıştı)")
    return True


# ═══════════════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT,ETHUSDT")
    ap.add_argument("--from", dest="bas", default="2019-01")
    ap.add_argument("--to", dest="bit", default="2025-06")
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--kendi-test", action="store_true",
                    help="parquet gerektirmez — sentetik veriyle mekanik doğrulama")
    args = ap.parse_args()

    if args.kendi_test:
        kendi_test()
        return

    ozetler, per_sembol, seri_listesi = [], {}, []
    for sym in [s.strip().upper() for s in args.symbol.split(",") if s.strip()]:
        print(f"[WSD] {sym} {args.bas}..{args.bit}…", flush=True)
        satirlar, hi, lo, cl = gun_tablosu(sym, args.bas, args.bit)
        if not satirlar:
            continue
        seriler = seriler_uret(satirlar, hi, lo, cl)
        seri_listesi.append(seriler)
        per_sembol[sym] = degerlendir(seriler)
        ozetler.append(f"{sym} n{len(satirlar)}")
        print(f"      ✓ {sym}: {len(satirlar)} karar günü", flush=True)

    if not per_sembol:
        print("❌ Hiç veri yok — metrics parquet indirilmiş mi? (data_ingest 'metrics')")
        return

    # PORTFÖY: her sembol AYRI simüle edilir, seriler kronolojik birleşir (§000)
    portfoy = degerlendir(seriler_birlestir(seri_listesi)) if len(seri_listesi) > 1 else None

    rapor = rapor_metni(portfoy or per_sembol[list(per_sembol)[0]],
                        ("PORTFÖY: " if portfoy else "") + " · ".join(ozetler),
                        args.bas, args.bit)
    print("\n" + rapor)
    for sym, snc in per_sembol.items():
        print(f"\n─── {sym} ───")
        print(rapor_metni(snc, sym, args.bas, args.bit))

    kayit = {"tarih": datetime.now(timezone.utc).isoformat(),
             "aralik": f"{args.bas}..{args.bit}",
             "parametreler": {"karar_saat_utc": KARAR_SAAT_UTC, "tp_r": TP_R,
                              "tut_saat": TUT_SAAT, "pct_pencere": PCT_PENCERE,
                              "sigma_pencere": SIGMA_PENCERE, "n_deneme": N_DENEME},
             "portfoy": portfoy, "semboller": per_sembol}
    Path(CIKTI).write_text(json.dumps(kayit, ensure_ascii=False, indent=2, default=str),
                           encoding="utf-8")
    print(f"\n💾 {CIKTI} yazıldı (git-senkron → commit+push edilirse lider okur)")

    if args.telegram:
        try:
            import asyncio
            from ajan_merkez import bildir
            asyncio.run(bildir("WSD Tarihsel Doğrulama", "backtest",
                               rapor.split("\n", 2)[1], detay=rapor))
            print("[Telegram] thread 4129'a gönderildi ✓", flush=True)
        except Exception as e:
            print(f"[Telegram] gönderilemedi: {str(e)[:80]}", flush=True)


if __name__ == "__main__":
    main()
