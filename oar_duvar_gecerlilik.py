"""
oar_duvar_gecerlilik.py — DUVAR GEÇERLİLİĞİ: funding rejimi duvarın TUTUP TUTMAYACAĞINI söylüyor mu?
═══════════════════════════════════════════════════════════════════════════════════════
⚠️ BU, ÖLEN SORUNUN TEKRARI DEĞİL. §5aa'da funding'i **yön sinyali** olarak test ettik
   (WSD ❌ · funding ekstremi ❌ · likidasyon kümesi ❌ — üçü de serap). O hat KAPALI.
   BURADAKİ SORU FARKLI: funding YÖN söylemiyor, ama duvarın **kalitesini** söylüyor mu?
   Yani "hangi funding ortalamalarında duvar destek/direnç gibi ÇALIŞMIŞ, hangilerinde
   mıknatıs olup KIRILMIŞ". Duvar zaten var (Komuta Merkezi paneli); soru duvara ne kadar
   güvenileceği. Bu hiç ölçülmedi.

⭐ ÖNCEDEN KAYDEDİLEN HİPOTEZ (pre-registration — veri madenciliği DEĞİL):
   Perp'te funding, KALABALIĞIN hangi tarafta olduğunu söyler. Kalabalık taraf ödeyendir.
     H1: AŞIRI-POZİTİF funding (longlar ödüyor = kalabalık LONG)
         → ALT duvar (long likidasyonları) YÜKLÜ  → mıknatıs, KIRILIR (kaskad)
         → ÜST duvar (short likidasyonları) İNCE → TUTAR (direnç çalışır)
     H2: NEGATİF funding (shortlar ödüyor = kalabalık SHORT) → aynası:
         → ÜST duvar KIRILIR · ALT duvar TUTAR
   Bu tahmin ÖNCE yazıldı, sonra ölçüldü. Ters çıkarsa hipotez ÇÜRÜR — "başka bir kombinasyon
   çalıştı" diye kurtarılmaz (§5m dersi: 97 "kazanan"ın hepsi seraptı).

ÖLÇÜM İKİ KATMANLI (ikincisi ŞART):
  ① OLASILIK KATMANI (tarama): P(duvar tuttu | dokundu, funding rejimi) + LIFT + Wilson CI
     + MONOTONLUK (rejim negatif→nötr→pozitif→aşırı sıralandığında kırılma oranı düzenli
     artıyor mu). Monotonluk = şans ile mekanizmayı ayıran asıl test (§6j yöntemi).
  ② İŞLEM KATMANI (karar): ⚠️ "vuruş oranı ≠ edge" bu depoda defalarca kanıtlandı
     (§5d micro-scalp WR%73 ama PF 0.69 · §5g kırılım-devam WR%70 ama PF 0.93 · §5m).
     Bu yüzden olasılık YETMEZ: duvarı gerçekten trade edip PF/beklenti + SERAP bateri
     (DSR≥0.95 ∧ CI-alt>0 ∧ perm-p<0.05 ∧ FDR ∧ 5x-likidasyon=0) uygulanır.

TERS KOL KONTROLÜ (zorunlu): her duvar için FADE (duvarı reddeder) **ve** KIRILIM-TAKİP
  (duvarı kırınca yönünde gider) kolları AYRI ölçülür. İkisi de aynı rejimde pozitifse
  sinyal duvardan değil DÖNEM EĞİLİMİNDEN geliyordur (§5aa'da bu kontrol funding
  hipotezini çürüttü — LONG kolu da negatifti).

NO-LOOKAHEAD:
  • Duvar haritası 04:00 UTC'de YALNIZ geçmişten kurulur (oar_likidasyon_haritasi ile aynı
    boru hattı: 7 günlük OI penceresi, likidasyona değmiş pozisyon düşer, OI azalış sönümü).
  • Duvar seviyesi karar anında BİLİNİR → limit emir olarak beklemek gerçekçi; temas barında
    duvar fiyatından giriş LOOKAHEAD DEĞİL.
  • funding ortalamaları YALNIZ ödenmiş (settled) periyotlardan; gelecek periyot ASLA.
  • σ_gün = önceki 20 günün ortalama günlük range%'i (o gün dahil değil).
  • Aynı barda hem kırılım hem tepki mümkünse KIRILIM önce sayılır → hipotezin ALEYHİNE
    (kötümser). SL/TP çakışmasında SL önce (ev standardı).

⚠️ VERİ SINIRI: harita OI'ye dayanır, metrics parquet **2021+** (§5aa) → varsayılan --from 2021-01.
⚠️ ŞAMPİYONLARA DOKUNMAZ (ANAYASA #8). Bu bir ÖLÇÜM modülüdür; serap testinden geçse bile
   canlıya bağlamak AYRI onay ister (§5p dersi).

Çalıştırma:
  python oar_duvar_gecerlilik.py --symbol BTCUSDT,ETHUSDT --from 2021-01 --to 2025-06 --telegram
  python oar_duvar_gecerlilik.py --kendi-test      # parquet/ağ GEREKMEZ — mekanik doğrulama
"""
import argparse
import json
from pathlib import Path

from oar_local_backtest import GUN_MS, SAAT_MS, FEE_PCT, SLIP_PCT
from oar_wsd_backtest import (KARAR_SAAT_UTC, SIGMA_PENCERE, TUT_SAAT, TP_R,
                              OT_MIN, OT_MAX,
                              _olcek_dogrula, _ts_ms, _utc, degerlendir, _g)
from oar_likidasyon_haritasi import (KALDIRAC_DAGILIM, PENCERE_GUN,
                                     _harita_kur, _kume_ozet, oi_sonum)
from oar_funding_carry import funding_oku, TABAN_ORAN, ASIRI_CARPAN, PERIYOT_MS

# ── Olay tanımı (VARSAYIM — hepsi ayarlanabilir, rapora yazılır) ──────────────
KIRILIM_SIGMA = 0.5    # duvarı bu kadar σ_gün AŞARSA "kırdı"
TEPKI_SIGMA   = 0.5    # duvardan bu kadar σ_gün UZAKLAŞIRSA "tuttu"
SL_SIGMA      = 1.0    # işlem SL genişliği (ev standardı — WSD/funding ile kıyaslanabilir)
MIN_KOVA_N    = 25     # bir rejim kovasının olasılık raporuna girmesi için asgari temas
CIKTI         = "duvar_gecerlilik_sonuc.json"

# ── Funding ortalama pencereleri (periyot sayısı; 1 periyot = 8 saat) ─────────
PENCERELER = [("f_8s", 1), ("f_1g", 3), ("f_3g", 9), ("f_7g", 21), ("f_30g", 90)]
ANA_PENCERE = "f_3g"   # işlem katmanı için ÖNCEDEN seçilen pencere (orta vade)

# ── Rejim kovaları: MUTLAK ÇIPA (§5aa dersi) ─────────────────────────────────
# ⚠️ Gezici percentile SEVİYEYİ değil DEĞİŞİMİ ölçer — aylarca süren yüksek funding
#    kendi son 30 gününe göre "normal" görünür, tam da görünmesi gereken yerde kaybolur.
REJIM_SIRA = ["negatif", "notr", "pozitif", "asiri_poz"]

# Gerçek tarama genişliği = pencere × rejim × duvar × kol × UFUK. DSR cezası bunu
# görmeli. ⚠️ UFUK boyutu dürüstlük gereği sayılır: ilk koşu 24s ile yapıldı, sonuç
# n-açlığından ölünce 72s'e geçildi → AYNI veriye ikinci bakış. Araştırmacı serbestlik
# derecesi cezasız bırakılırsa DSR seraba yeşil ışık yakar (§5m dersi).
N_UFUK   = 2                                            # denenen ufuk sayısı (24s, 72s)
N_DENEME = len(PENCERELER) * len(REJIM_SIRA) * 2 * 2 * N_UFUK   # = 160


def _rejim(ort):
    """funding ortalaması → rejim etiketi (mutlak çıpa)."""
    if ort is None:
        return None
    if ort < 0:
        return "negatif"
    if ort <= TABAN_ORAN:
        return "notr"
    if ort < ASIRI_CARPAN * TABAN_ORAN:
        return "pozitif"
    return "asiri_poz"


def _wilson(basari, n, z=1.96):
    """Wilson skor aralığı — küçük n'de normal yaklaşımdan dürüst."""
    if n <= 0:
        return (None, None)
    p = basari / n
    d = 1 + z * z / n
    orta = (p + z * z / (2 * n)) / d
    yari = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / d
    return (round(max(0.0, orta - yari), 4), round(min(1.0, orta + yari), 4))


# ═══════════════════════════════════════════════════════════════════════════════
#  OLAY SINIFLANDIRMA (saf fonksiyon — self-test bunu doğrudan sınar)
# ═══════════════════════════════════════════════════════════════════════════════
def duvar_olayi(duvar, sigma_px, ust, hi, lo, i0, i1):
    """
    Duvara ne oldu? Döner: (durum, temas_i)
      durum ∈ {"DOKUNMADI", "KIRDI", "TUTTU", "KARARSIZ"}
    ust=True → üst duvar (fiyat yukarıdan gelir, hi ile temas).
    ⚠️ Aynı barda hem kırılım hem tepki mümkünse KIRILIM önce → hipotez ALEYHİNE.
    """
    import numpy as np
    if duvar is None or not (i1 > i0) or sigma_px <= 0:
        return "DOKUNMADI", None
    h, l = hi[i0:i1], lo[i0:i1]
    if ust:
        temas = h >= duvar
    else:
        temas = l <= duvar
    if not bool(temas.any()):
        return "DOKUNMADI", None
    j = int(np.argmax(temas))                      # ilk temas barı (pencere içi indeks)

    if ust:
        kirilim = h[j:] >= duvar + KIRILIM_SIGMA * sigma_px
        tepki   = l[j:] <= duvar - TEPKI_SIGMA * sigma_px
    else:
        kirilim = l[j:] <= duvar - KIRILIM_SIGMA * sigma_px
        tepki   = h[j:] >= duvar + TEPKI_SIGMA * sigma_px

    ki = int(np.argmax(kirilim)) if bool(kirilim.any()) else -1
    ti = int(np.argmax(tepki)) if bool(tepki.any()) else -1
    if ki == -1 and ti == -1:
        return "KARARSIZ", i0 + j
    if ti == -1 or (ki != -1 and ki <= ti):        # eşitlikte KIRILIM (kötümser)
        return "KIRDI", i0 + j
    return "TUTTU", i0 + j


def _simule_duvar(yon, giris, sigma_px, j, i1, hi, lo, cl):
    """
    Duvar işlemi: giriş duvar fiyatından (limit), SL = SL_SIGMA·σ ötede, TP = 3R,
    time-stop pencere sonu. Temas barı DAHİL taranır (o barda duvarı delip geçen
    hareket bizi stoplar — kötümser). Aynı barda SL/TP → SL önce.
    """
    import numpy as np
    R = SL_SIGMA * sigma_px
    if R <= 0 or j is None or j >= i1:
        return None
    if yon == "LONG":
        sl, tp = giris - R, giris + TP_R * R
        sl_vur, tp_vur = lo[j:i1] <= sl, hi[j:i1] >= tp
    else:
        sl, tp = giris + R, giris - TP_R * R
        sl_vur, tp_vur = hi[j:i1] >= sl, lo[j:i1] <= tp
    si = int(np.argmax(sl_vur)) if bool(sl_vur.any()) else -1
    ti = int(np.argmax(tp_vur)) if bool(tp_vur.any()) else -1
    if si == -1 and ti == -1:
        cikis, tur = float(cl[i1 - 1]), "TIME"
    elif ti == -1 or (si != -1 and si <= ti):
        cikis, tur = sl, "SL"
    else:
        cikis, tur = tp, "TP"
    ham = (cikis - giris) / giris * 100.0
    if yon == "SHORT":
        ham = -ham
    return round(ham - FEE_PCT - SLIP_PCT, 6), tur


# ═══════════════════════════════════════════════════════════════════════════════
#  GÜNLÜK TABLO (duvar seviyeleri + funding ortalamaları + olay sonuçları)
# ═══════════════════════════════════════════════════════════════════════════════
def gun_tablosu(sembol, bas, bit, k_df=None, m_df=None, funding=None,
                kaldirac_dagilim=None, ufuk_saat=TUT_SAAT):
    """
    Döner: (satirlar, hi, lo, cl)
    satirlar[] = {gun, ts, spot, sigma, i0, i1,
                  ust_duvar, ust_pay, alt_duvar, alt_pay,
                  f_8s..f_30g, rejim_f_8s..rejim_f_30g,
                  ust_durum, ust_temas_i, alt_durum, alt_temas_i}
    """
    import numpy as np
    import pandas as pd
    from oar_local_backtest import _klines_oku, _metrics_oku, _ms_olcekle

    kaldirac_dagilim = kaldirac_dagilim or KALDIRAC_DAGILIM
    k = k_df if k_df is not None else _klines_oku(sembol, bas, bit)
    if k is None or not len(k):
        print(f"      ⚠ {sembol}: klines yok", flush=True)
        return [], None, None, None
    m = m_df if m_df is not None else _metrics_oku(sembol, bas, bit)
    if m is None or not len(m):
        print(f"      ⚠ {sembol}: metrics parquet yok (OI olmadan duvar kurulamaz)", flush=True)
        return [], None, None, None
    kayitlar = funding if funding is not None else funding_oku(sembol)
    if not kayitlar:
        print(f"      ⚠ {sembol}: funding geçmişi yok → önce "
              f"`python oar_funding_carry.py --indir --symbol {sembol}`", flush=True)
        return [], None, None, None

    k = k.copy()
    k["open_time"] = _ms_olcekle(k["open_time"])
    k = k[(k["open_time"] >= OT_MIN) & (k["open_time"] < OT_MAX)].sort_values("open_time")
    ot = k["open_time"].to_numpy(dtype="int64")
    hi = k["high"].to_numpy(dtype="float64")
    lo = k["low"].to_numpy(dtype="float64")
    cl = k["close"].to_numpy(dtype="float64")
    op = k["open"].to_numpy(dtype="float64")
    _olcek_dogrula(f"{sembol} klines", ot)

    m = m.copy()
    kolon = "create_time" if "create_time" in m.columns else "ts_ms"
    m["ts_ms"] = _ts_ms(m[kolon])
    m = m.sort_values("ts_ms")
    mts = m["ts_ms"].to_numpy(dtype="int64")
    _olcek_dogrula(f"{sembol} metrics", mts)
    oi_val = m["sum_open_interest_value"].astype(float).to_numpy()
    d_oi = np.diff(oi_val, prepend=oi_val[0])
    k_idx = np.searchsorted(ot, mts, side="right") - 1

    fts = np.array([int(r["ts"]) for r in kayitlar], dtype="int64")
    frt = np.array([float(r["rate"]) for r in kayitlar], dtype="float64")
    sira = np.argsort(fts)
    fts, frt = fts[sira], frt[sira]
    _olcek_dogrula(f"{sembol} funding", fts)

    gd = pd.DataFrame({"gun": ot // GUN_MS, "high": hi, "low": lo, "open": op}) \
           .groupby("gun").agg(h=("high", "max"), l=("low", "min"), o=("open", "first"))
    rngs = ((gd["h"] - gd["l"]) / gd["o"] * 100.0).to_numpy(dtype="float64")
    gunler = gd.index.to_numpy()
    sigma_map = {int(gunler[i]): float(rngs[i - SIGMA_PENCERE:i].mean())
                 for i in range(SIGMA_PENCERE, len(gunler))
                 if np.isfinite(rngs[i - SIGMA_PENCERE:i]).all()
                 and rngs[i - SIGMA_PENCERE:i].mean() > 0}

    satirlar, son_ay = [], ""
    red = {"sigma_penceresi": 0, "giris_bari": 0, "ileri_pencere": 0,
           "oi_yok": 0, "funding_yok": 0}
    for g in gunler:
        g = int(g)
        sigma = sigma_map.get(g)
        if sigma is None:
            red["sigma_penceresi"] += 1
            continue
        karar_ts = g * GUN_MS + int(KARAR_SAAT_UTC * SAAT_MS)
        ay = f"{_utc(karar_ts):%Y-%m}"
        if ay != son_ay:
            print(f"      · {ay} analiz ediliyor…", flush=True)
            son_ay = ay

        gi = int(np.searchsorted(ot, karar_ts, side="right")) - 1
        if gi < 0 or karar_ts - int(ot[gi]) > 5 * 60_000:
            red["giris_bari"] += 1
            continue
        i0 = gi + 1
        i1 = int(np.searchsorted(ot, karar_ts + ufuk_saat * SAAT_MS, side="right"))
        if i1 - i0 < 60:
            red["ileri_pencere"] += 1
            continue

        # ── funding ortalamaları: YALNIZ ödenmiş periyotlar ──
        fi = int(np.searchsorted(fts, karar_ts, side="right")) - 1
        if fi < 0 or karar_ts - int(fts[fi]) > 2 * PERIYOT_MS:
            red["funding_yok"] += 1
            continue

        # ── duvar haritası (oar_likidasyon_haritasi ile AYNI boru hattı) ──
        pen_bas = karar_ts - PENCERE_GUN * GUN_MS
        ma = int(np.searchsorted(mts, pen_bas, side="left"))
        mb = int(np.searchsorted(mts, karar_ts, side="right"))
        if mb - ma < 50:
            red["oi_yok"] += 1
            continue
        yeni = d_oi[ma:mb]
        artis = yeni > 0
        if not artis.any():
            red["oi_yok"] += 1
            continue
        ki = k_idx[ma:mb][artis]
        gecerli = (ki >= 0) & (ki <= gi)
        if not gecerli.any():
            red["oi_yok"] += 1
            continue
        ki = ki[gecerli]
        giris_px = cl[ki]
        notional = yeni[artis][gecerli]

        pen_k0 = int(np.searchsorted(ot, pen_bas, side="left"))
        sfx_min = np.minimum.accumulate(lo[pen_k0:gi + 1][::-1])[::-1]
        sfx_max = np.maximum.accumulate(hi[pen_k0:gi + 1][::-1])[::-1]
        yerel = np.clip(ki - pen_k0, 0, len(sfx_min) - 1)
        spot = float(cl[gi])
        hayatta = oi_sonum(oi_val[ma:mb], d_oi[ma:mb])[artis][gecerli]

        alt_s, alt_n, ust_s, ust_n = _harita_kur(
            giris_px, notional, sfx_min[yerel], sfx_max[yerel], spot,
            kaldirac_dagilim, hayatta_pay=hayatta)
        alt_top, alt_kume, alt_mes = _kume_ozet(alt_s, alt_n, spot, yukari=False)
        ust_top, ust_kume, ust_mes = _kume_ozet(ust_s, ust_n, spot, yukari=True)
        toplam = alt_top + ust_top
        if toplam <= 0:
            red["oi_yok"] += 1
            continue

        # küme MESAFESİ (%) → duvar FİYATI
        ust_duvar = spot * (1.0 + ust_mes / 100.0) if ust_mes is not None else None
        alt_duvar = spot * (1.0 - alt_mes / 100.0) if alt_mes is not None else None
        sigma_px = spot * sigma / 100.0

        r = {"gun": g, "ts": karar_ts, "spot": spot, "sigma": sigma,
             "sigma_px": sigma_px, "i0": i0, "i1": i1,
             "ust_duvar": ust_duvar, "ust_pay": (ust_kume / toplam) if toplam else None,
             "ust_mesafe": ust_mes,          # duvarın spot'a % uzaklığı (teşhis için)
             "alt_duvar": alt_duvar, "alt_pay": (alt_kume / toplam) if toplam else None,
             "alt_mesafe": alt_mes,
             "toplam_notional": toplam}

        for ad, np_ in PENCERELER:
            pen = frt[max(0, fi - np_ + 1):fi + 1]
            ort = float(pen.mean()) if len(pen) >= max(1, np_ // 2) else None
            r[ad] = ort
            r["rejim_" + ad] = _rejim(ort)

        r["ust_durum"], r["ust_temas_i"] = duvar_olayi(ust_duvar, sigma_px, True, hi, lo, i0, i1)
        r["alt_durum"], r["alt_temas_i"] = duvar_olayi(alt_duvar, sigma_px, False, hi, lo, i0, i1)
        satirlar.append(r)

    if not satirlar or red["oi_yok"] or red["funding_yok"]:
        ara = lambda a: (f"{_utc(a[0]):%Y-%m-%d} → {_utc(a[-1]):%Y-%m-%d}") if len(a) else "boş"
        print(f"      · elenen gün: {red} | klines {ara(ot)} | metrics {ara(mts)} "
              f"| funding {ara(fts)}", flush=True)
    return satirlar, hi, lo, cl


# ═══════════════════════════════════════════════════════════════════════════════
#  ① OLASILIK KATMANI — hangi ortalamada duvar tuttu / kırıldı
# ═══════════════════════════════════════════════════════════════════════════════
def olasilik_tablosu(satirlar):
    """
    Her (pencere × duvar × rejim) için: temas / tuttu / kırdı + oran + LIFT + Wilson.
    LIFT = koşullu tutma oranı − o duvarın GENEL tutma oranı (taban).
    Ayrıca MONOTONLUK: rejim negatif→aşırı sıralandığında kırılma oranı düzenli mi.
    """
    cikti = {}
    for pen_ad, _ in PENCERELER:
        cikti[pen_ad] = {}
        for duvar in ("ust", "alt"):
            hepsi = [r.get(duvar + "_durum") for r in satirlar]
            n_gun = len(hepsi)
            n_temas = sum(1 for d in hepsi if d in ("TUTTU", "KIRDI", "KARARSIZ"))
            n_kararsiz = sum(1 for d in hepsi if d == "KARARSIZ")
            durumlar = [(r["rejim_" + pen_ad], r[duvar + "_durum"]) for r in satirlar
                        if r.get(duvar + "_durum") in ("TUTTU", "KIRDI")]
            taban_n = len(durumlar)
            if not taban_n:
                continue
            taban_tut = sum(1 for _, d in durumlar if d == "TUTTU") / taban_n

            kovalar, kir_oranlari = {}, []
            for rej in REJIM_SIRA:
                alt = [d for rj, d in durumlar if rj == rej]
                n = len(alt)
                if n < MIN_KOVA_N:
                    kovalar[rej] = {"n": n, "yetersiz": True}
                    kir_oranlari.append(None)
                    continue
                tut = sum(1 for d in alt if d == "TUTTU")
                oran = tut / n
                ci = _wilson(tut, n)
                kovalar[rej] = {
                    "n": n, "tuttu": tut, "kirdi": n - tut,
                    "tutma_orani": round(oran, 4),
                    "kirilma_orani": round(1 - oran, 4),
                    "lift": round((oran - taban_tut) * 100, 2),
                    "wilson_alt": ci[0], "wilson_ust": ci[1],
                    # tabandan AYRIŞTI mı: Wilson aralığı tabanı içermiyorsa evet
                    "tabandan_ayrik": bool(ci[0] is not None and
                                           (ci[0] > taban_tut or ci[1] < taban_tut)),
                }
                kir_oranlari.append(1 - oran)

            gecerli = [x for x in kir_oranlari if x is not None]
            mono = None
            if len(gecerli) >= 3:
                artan = all(gecerli[i] <= gecerli[i + 1] for i in range(len(gecerli) - 1))
                azalan = all(gecerli[i] >= gecerli[i + 1] for i in range(len(gecerli) - 1))
                mono = "artan" if artan else ("azalan" if azalan else "yok")

            cikti[pen_ad][duvar] = {
                "taban_n": taban_n, "taban_tutma": round(taban_tut, 4),
                "kovalar": kovalar, "monotonluk": mono,
                # ⚠️ SEÇİM ETKİSİ: KARARSIZ temaslar (pencere bitmeden çözülmeyenler)
                #    paydadan düşer. Pay yüksekse olasılık katmanı, ERKEN çözülen
                #    temasların yanlı altkümesini ölçüyor demektir — rapora yazılır.
                "gun": n_gun, "temas": n_temas,
                "temas_orani": round(n_temas / n_gun, 4) if n_gun else None,
                "kararsiz": n_kararsiz,
                "kararsiz_payi": round(n_kararsiz / n_temas, 4) if n_temas else None,
            }
    return cikti


def mesafe_tanisi(satirlar, ufuk_saat):
    """
    ⚠️ TEŞHİS (hipotez testi DEĞİL — betimleyici): duvar NE KADAR uzakta ve o mesafede
    fiyat ufuk içinde oraya ULAŞABİLİYOR mu. Temas oranı düşükse ölçüm n-açlığından
    ölür; sebebin mesafe/ufuk uyumsuzluğu olup olmadığını bu tablo gösterir.
    σ_gün cinsinden mesafe asıl ölçüdür: 24 saatte fiyat tipik olarak ~1σ yol alır,
    yani 4σ uzaktaki bir duvara 1 günde ulaşmak yapısal olarak beklenmez.
    """
    import numpy as np
    cikti = {}
    for duvar in ("ust", "alt"):
        mes = [r[duvar + "_mesafe"] for r in satirlar if r.get(duvar + "_mesafe") is not None]
        if not mes:
            continue
        a = np.asarray(mes, dtype=float)
        # mesafeyi σ_gün cinsine çevir (ufukla kıyaslanabilir tek birim)
        sig = np.asarray([r["sigma"] for r in satirlar
                          if r.get(duvar + "_mesafe") is not None], dtype=float)
        sigma_cinsi = np.divide(a, sig, out=np.full_like(a, np.nan), where=sig > 0)
        kova_sinir = [0, 1, 2, 4, 8, 1e9]
        kovalar = []
        for i in range(len(kova_sinir) - 1):
            m = (sigma_cinsi >= kova_sinir[i]) & (sigma_cinsi < kova_sinir[i + 1])
            n = int(m.sum())
            if not n:
                continue
            idx = [j for j, r in enumerate(satirlar)
                   if r.get(duvar + "_mesafe") is not None]
            secili = [satirlar[idx[j]] for j in np.flatnonzero(m)]
            temas = sum(1 for r in secili
                        if r.get(duvar + "_durum") in ("TUTTU", "KIRDI", "KARARSIZ"))
            kovalar.append({
                "aralik": f"{kova_sinir[i]}–{kova_sinir[i+1]:g}σ" if i < len(kova_sinir) - 2
                          else f"≥{kova_sinir[i]}σ",
                "gun": n, "temas": temas,
                "temas_orani": round(temas / n, 4),
            })
        cikti[duvar] = {
            "mesafe_pct_medyan": round(float(np.median(a)), 2),
            "mesafe_sigma_medyan": round(float(np.nanmedian(sigma_cinsi)), 2),
            "mesafe_sigma_p25": round(float(np.nanpercentile(sigma_cinsi, 25)), 2),
            "mesafe_sigma_p75": round(float(np.nanpercentile(sigma_cinsi, 75)), 2),
            "ufuk_saat": ufuk_saat,
            "ufuk_sigma": round(ufuk_saat / 24.0, 2),   # 24s ≈ 1σ_gün yol
            "kovalar": kovalar,
        }
    return cikti


def coklu_karsilastirma(olasilik, alfa=0.05):
    """
    ⚠️⚠️ EN KRİTİK MUHASEBE (§5m dersi: 97 "kazanan"ın hepsi seraptı).
    Olasılık katmanı ONLARCA hücre test eder; her ★ %95 güvenle işaretlenir. Yani
    SAF ŞANSLA bile ~alfa×hücre kadar ★ ÇIKAR. Tek bir ★ görüp "doğrulandı" demek
    tam olarak bu deponun defalarca düştüğü tuzaktır.
    Döner: test edilen hücre sayısı, şansla BEKLENEN ★, GÖZLENEN ★ ve yargı.
    """
    test, yildiz = 0, []
    for pen_ad, blok in olasilik.items():
        for duvar, b in (blok or {}).items():
            for rej, k in (b.get("kovalar") or {}).items():
                if k.get("yetersiz"):
                    continue
                test += 1
                if k.get("tabandan_ayrik"):
                    yildiz.append(f"{pen_ad}·{duvar}·{rej}")
    beklenen = round(test * alfa, 2)
    if not test:
        yargi = "hücre yok"
    elif len(yildiz) <= beklenen:
        yargi = ("❌ ŞANS SEVİYESİNDE — gözlenen ★ sayısı şansla beklenenin ALTINDA/eşit. "
                 "Tek tek ★'lara bakıp 'doğrulandı' DENMEZ.")
    elif len(yildiz) >= 3 * beklenen:
        yargi = ("✅ ŞANSTAN FAZLA — ★ sayısı beklenenin belirgin üstünde; hangi hücrelerin "
                 "tutarlı olduğuna bakmaya değer (yine de işlem katmanı şart).")
    else:
        yargi = ("⚠ BELİRSİZ — ★ sayısı şans beklentisine yakın; tek başına kanıt değil.")
    return {"test_edilen_hucre": test, "alfa": alfa, "sansla_beklenen_yildiz": beklenen,
            "gozlenen_yildiz": len(yildiz), "yildizlar": yildiz, "yargi": yargi}


def hipotez_karnesi(olasilik):
    """
    ÖNCEDEN KAYDEDİLEN H1/H2 doğrulandı mı — her pencere için ayrı.
    H1: asiri_poz → alt KIRILIR (kırılma oranı taban üstü) ∧ üst TUTAR
    H2: negatif   → üst KIRILIR ∧ alt TUTAR
    """
    karne = {}
    for pen_ad, _ in PENCERELER:
        blok = olasilik.get(pen_ad, {})
        satir = {}
        for ad, rej, duvar, beklenen in (("H1_alt_kirilir", "asiri_poz", "alt", "KIRDI"),
                                         ("H1_ust_tutar",  "asiri_poz", "ust", "TUTTU"),
                                         ("H2_ust_kirilir", "negatif",  "ust", "KIRDI"),
                                         ("H2_alt_tutar",   "negatif",  "alt", "TUTTU")):
            k = (blok.get(duvar) or {}).get("kovalar", {}).get(rej)
            if not k or k.get("yetersiz"):
                satir[ad] = {"durum": "ÖLÇÜLEMEDİ", "n": (k or {}).get("n", 0)}
                continue
            # beklenen yönde LIFT + Wilson tabandan ayrık mı
            # ⚠️ LIFT'in İŞARETİ tek başına anlamlı DEĞİL: n≈100'de örneklem gürültüsü
            #    rahatça ±8 puan sallar (self-test bunu gösterdi). Sabit puan eşiği n ile
            #    ölçeklenmediği için YANLIŞ araç — karar YALNIZ Wilson kapısına bağlıdır.
            lift = k["lift"] if beklenen == "TUTTU" else -k["lift"]
            if not k["tabandan_ayrik"]:
                durum = "≈ AYRIŞMADI (şansla açıklanabilir)"
            elif lift > 0:
                durum = "✅ DOĞRULANDI"
            else:
                durum = "❌ TERS (anlamlı, ama hipotezin tersi)"
            satir[ad] = {
                "durum": durum,
                "n": k["n"], "lift": lift,
                "tutma_orani": k["tutma_orani"], "wilson": [k["wilson_alt"], k["wilson_ust"]],
            }
        karne[pen_ad] = satir
    return karne


# ═══════════════════════════════════════════════════════════════════════════════
#  ② İŞLEM KATMANI — ön-kayıtlı hücreler + TERS KOL kontrolü
# ═══════════════════════════════════════════════════════════════════════════════
def _islem_havuzu(satirlar, hi, lo, cl):
    """
    Gün × duvar × kol → (net, tur). Varyantlardan BAĞIMSIZ, bir kez simüle edilir
    (varyant kıyası birebir adil olur — WSD/funding modülleriyle aynı disiplin).
      FADE          : duvara temasta duvarı reddet (üst→SHORT, alt→LONG)
      KIRILIM_TAKIP : duvar kırılınca yönünde git (üst→LONG, alt→SHORT)  ← TERS KOL
    """
    havuz = {}
    for r in satirlar:
        h = {}
        for duvar, ust in (("ust", True), ("alt", False)):
            W, j, durum = r[duvar + "_duvar"], r[duvar + "_temas_i"], r[duvar + "_durum"]
            if W is None or j is None:
                continue
            h[duvar + "_FADE"] = _simule_duvar(
                "SHORT" if ust else "LONG", W, r["sigma_px"], j, r["i1"], hi, lo, cl)
            if durum == "KIRDI":
                kir_px = W + (KIRILIM_SIGMA * r["sigma_px"] * (1 if ust else -1))
                h[duvar + "_KIRILIM"] = _simule_duvar(
                    "LONG" if ust else "SHORT", kir_px, r["sigma_px"], j, r["i1"], hi, lo, cl)
        havuz[r["gun"]] = h
    return havuz


def _varyantlar():
    """
    ad → (yön_etiketi, kol_anahtarı, seçici). ÖN-KAYITLI hücreler + tabanlar + ters kollar.
    Taban = rejim filtresi YOK → dönem eğilimi kıyası (§5aa dersi).
    """
    rej = lambda v: (lambda r: r.get("rejim_" + ANA_PENCERE) == v)
    hep = lambda r: True
    v = [
        ("TABAN_UST_FADE",      "SHORT", "ust_FADE",     hep),
        ("TABAN_ALT_FADE",      "LONG",  "alt_FADE",     hep),
        ("TABAN_UST_KIRILIM",   "LONG",  "ust_KIRILIM",  hep),
        ("TABAN_ALT_KIRILIM",   "SHORT", "alt_KIRILIM",  hep),
        # ── H1: aşırı-pozitif funding (kalabalık LONG) ──
        ("H1_ASIRI_UST_FADE",   "SHORT", "ust_FADE",     rej("asiri_poz")),   # ÜST TUTAR bekleniyor
        ("H1_ASIRI_ALT_FADE",   "LONG",  "alt_FADE",     rej("asiri_poz")),   # ALT KIRILIR → bu kol kaybetmeli
        ("H1_ASIRI_ALT_KIRILIM", "SHORT", "alt_KIRILIM", rej("asiri_poz")),   # kaskad kolu
        # ── H2: negatif funding (kalabalık SHORT) ──
        ("H2_NEG_ALT_FADE",     "LONG",  "alt_FADE",     rej("negatif")),     # ALT TUTAR bekleniyor
        ("H2_NEG_UST_FADE",     "SHORT", "ust_FADE",     rej("negatif")),     # ÜST KIRILIR → kaybetmeli
        ("H2_NEG_UST_KIRILIM",  "LONG",  "ust_KIRILIM",  rej("negatif")),     # kaskad kolu
        # ── nötr rejim: duvar mekanizması funding'den bağımsız mı ──
        ("NOTR_UST_FADE",       "SHORT", "ust_FADE",     rej("notr")),
        ("NOTR_ALT_FADE",       "LONG",  "alt_FADE",     rej("notr")),
    ]
    return v


def seriler_uret(satirlar, havuz):
    """{varyant: (netler, tsler, turler)}"""
    seriler = {}
    for ad, _yon, kol, sec in _varyantlar():
        netler, tsler, turler = [], [], []
        for r in satirlar:
            if not sec(r):
                continue
            s = havuz.get(r["gun"], {}).get(kol)
            if not s:
                continue
            netler.append(s[0]); tsler.append(r["ts"]); turler.append(s[1])
        seriler[ad] = (netler, tsler, turler)
    return seriler


def seriler_birlestir(liste):
    """Sembolleri tek havuzda topla (kronolojik)."""
    birlesik = {}
    for seriler in liste:
        for ad, (n, t, tu) in seriler.items():
            a, b, c = birlesik.setdefault(ad, ([], [], []))
            a.extend(n); b.extend(t); c.extend(tu)
    for ad, (n, t, tu) in birlesik.items():
        if t:
            sira = sorted(range(len(t)), key=lambda i: t[i])
            birlesik[ad] = ([n[i] for i in sira], [t[i] for i in sira], [tu[i] for i in sira])
    return birlesik


def _ters_kontrol(kartlar):
    """
    FADE ve KIRILIM kolları AYNI rejimde birlikte pozitifse → sinyal duvardan değil
    dönem eğiliminden. (§5aa'da bu kontrol funding yön hipotezini çürüttü.)
    """
    ciftler = [("H1_ASIRI_ALT_FADE", "H1_ASIRI_ALT_KIRILIM"),
               ("H2_NEG_UST_FADE", "H2_NEG_UST_KIRILIM"),
               ("TABAN_UST_FADE", "TABAN_UST_KIRILIM"),
               ("TABAN_ALT_FADE", "TABAN_ALT_KIRILIM")]
    uyari = []
    for a, b in ciftler:
        ka, kb = kartlar.get(a), kartlar.get(b)
        if not ka or not kb:
            continue
        ba, bb = ka.get("beklenti"), kb.get("beklenti")
        if ba is None or bb is None:
            continue
        if ba > 0 and bb > 0:
            uyari.append(f"{a} (+{ba:.3f}) ve ters kolu {b} (+{bb:.3f}) İKİSİ DE pozitif "
                         f"→ dönem eğilimi şüphesi, duvar mekanizması DEĞİL")
    return uyari


# ═══════════════════════════════════════════════════════════════════════════════
#  RAPOR
# ═══════════════════════════════════════════════════════════════════════════════
def _o(v):
    return "—" if v is None else f"{v*100:5.1f}%"


def olasilik_metni(olasilik, karne, ck=None):
    s = ["═══ ① OLASILIK KATMANI — duvara dokununca ne oldu ═══", ""]
    s.append(f"Duvar TUTTU = temas sonrası {TEPKI_SIGMA}σ geri çekildi · "
             f"KIRDI = duvarı {KIRILIM_SIGMA}σ aştı (eşitlikte KIRILIM = kötümser)")
    s.append("")
    for pen_ad, per in PENCERELER:
        blok = olasilik.get(pen_ad)
        if not blok:
            continue
        saat = per * 8
        etiket = f"{saat} saat" if saat < 48 else f"{saat // 24} gün"
        s.append(f"── funding ortalaması: {pen_ad} ({etiket}) ──")
        for duvar in ("ust", "alt"):
            b = blok.get(duvar)
            if not b:
                continue
            ad = "ÜST DUVAR (short likidasyonları)" if duvar == "ust" else "ALT DUVAR (long likidasyonları)"
            s.append(f"  {ad} · taban tutma {_o(b['taban_tutma'])} (n={b['taban_n']}) "
                     f"· monotonluk: {b['monotonluk'] or '—'}")
            kp = b.get("kararsiz_payi")
            s.append(f"     temas {b['temas']}/{b['gun']} gün ({_o(b.get('temas_orani'))})"
                     f" · çözülmeyen (KARARSIZ) {b['kararsiz']} = {_o(kp)} of temas"
                     + ("   ⚠ yüksek: olasılıklar ERKEN çözülen temasların yanlı altkümesi"
                        if (kp or 0) > 0.35 else ""))
            s.append(f"     {'rejim':<11}{'n':>5}{'tuttu':>8}{'kırdı':>8}{'LIFT':>8}  Wilson")
            for rej in REJIM_SIRA:
                k = b["kovalar"].get(rej)
                if not k:
                    continue
                if k.get("yetersiz"):
                    s.append(f"     {rej:<11}{k['n']:>5}   — yetersiz (<{MIN_KOVA_N})")
                    continue
                w = f"[{k['wilson_alt']:.2f}, {k['wilson_ust']:.2f}]"
                ayr = " ★" if k["tabandan_ayrik"] else ""
                s.append(f"     {rej:<11}{k['n']:>5}{_o(k['tutma_orani']):>8}"
                         f"{_o(k['kirilma_orani']):>8}{k['lift']:>+7.1f}p  {w}{ayr}")
        s.append("")
    s.append("★ = Wilson aralığı tabanı içermiyor (TEK BAŞINA şansla açıklanması zor)")
    if ck:
        s.append("")
        s.append("── ⚠️ ÇOKLU KARŞILAŞTIRMA MUHASEBESİ (★'ları okumadan ÖNCE) ──")
        s.append(f"  test edilen hücre: {ck['test_edilen_hucre']} · "
                 f"şansla BEKLENEN ★: ~{ck['sansla_beklenen_yildiz']} "
                 f"(α={ck['alfa']}) · GÖZLENEN ★: {ck['gozlenen_yildiz']}")
        if ck["yildizlar"]:
            s.append(f"  ★ hücreler: {', '.join(ck['yildizlar'])}")
        s.append(f"  → {ck['yargi']}")
    s.append("")
    s.append("── ÖN-KAYITLI HİPOTEZ KARNESİ ──")
    s.append(f"⚠️ Aşağıdaki '✅ DOĞRULANDI' etiketi TEK HÜCRE yargısıdır; yukarıdaki çoklu-"
             f"karşılaştırma satırıyla BİRLİKTE okunur. ÖN-KAYITLI BİRİNCİL pencere = "
             f"{ANA_PENCERE}; diğer pencereler KEŞİFSEL (aynı veriye ek bakış).")
    s.append("H1: aşırı-pozitif funding → ALT duvar kırılır, ÜST duvar tutar")
    s.append("H2: negatif funding      → ÜST duvar kırılır, ALT duvar tutar")
    for pen_ad, _ in PENCERELER:
        satir = karne.get(pen_ad, {})
        ozet = " · ".join(f"{k.split('_', 1)[1]}: {v['durum']}" for k, v in satir.items())
        s.append(f"  {pen_ad:<7} {ozet}")
    return "\n".join(s)


def _sat(k):
    """⚠️ serap karnesi İÇ İÇE dict (oar_serap_testi.serap_karnesi) — düz anahtar YOK.
       _g sola yaslar → etiketli biçim kullanılır (sütun çakışması olmaz)."""
    if "serap" not in k:
        return f"  {k['ad']:<24} n{_g(k.get('n'), 6)} {k.get('karar', '—')}"
    s = k["serap"]
    dsr = (s.get("deflated_sharpe") or {}).get("dsr")
    ci = (s.get("bootstrap_beklenti_ci") or {}).get("alt")
    lik = ((s.get("mc_equity") or {}).get("5x") or {}).get("likidasyon_orani")
    return (f"  {k['ad']:<24} n{_g(k.get('n'), 6)} WR%{_g(k.get('wr'), 6)} PF {_g(k.get('pf'), 6)}"
            f" bek {_g(k.get('beklenti'), 8)} OOS {_g(k.get('oos_beklenti'), 8)}"
            f" DSR {_g(dsr, 7)} p {_g(s.get('permutasyon_p'), 8)}"
            f" CI-alt {_g(ci, 8)} 5xlik {_g(lik, 6)} {k.get('karar', '')}")


def tani_metni(tani):
    if not tani:
        return ""
    s = ["═══ ⓪ TEŞHİS — duvar ulaşılabilir mi (ölçüm n-açlığından ölüyor mu) ═══", ""]
    for duvar in ("ust", "alt"):
        t = tani.get(duvar)
        if not t:
            continue
        ad = "ÜST DUVAR" if duvar == "ust" else "ALT DUVAR"
        s.append(f"  {ad} · mesafe medyan %{t['mesafe_pct_medyan']} = "
                 f"{t['mesafe_sigma_medyan']}σ_gün "
                 f"(p25 {t['mesafe_sigma_p25']}σ · p75 {t['mesafe_sigma_p75']}σ)")
        s.append(f"     ufuk {t['ufuk_saat']}s ≈ {t['ufuk_sigma']}σ_gün yol "
                 f"→ duvar bundan uzaksa temas YAPISAL olarak beklenmez")
        s.append(f"     {'mesafe':<10}{'gün':>7}{'temas':>8}{'oran':>8}")
        for k in t["kovalar"]:
            s.append(f"     {k['aralik']:<10}{k['gun']:>7}{k['temas']:>8}"
                     f"{_o(k['temas_orani']):>8}")
        s.append("")
    return "\n".join(s)


def rapor_metni(sonuc):
    uf = (sonuc.get("parametreler") or {}).get("ufuk_saat", TUT_SAAT)
    s = [f"═══ DUVAR GEÇERLİLİĞİ — funding rejimi duvarı güvenilir kılıyor mu ═══",
         f"{sonuc['sembol']} · {sonuc['bas']}..{sonuc['bit']} · {sonuc['gun_sayisi']} karar günü "
         f"· ufuk {uf}s", ""]
    tm = tani_metni(sonuc.get("mesafe_tanisi"))
    if tm:
        s.append(tm)
    s.append(olasilik_metni(sonuc["olasilik"], sonuc["hipotez_karnesi"],
                            sonuc.get("coklu_karsilastirma")))
    s.append("")
    s.append("═══ ② İŞLEM KATMANI — vuruş oranı ≠ edge (§5d/§5g/§5m dersi) ═══")
    s.append(f"Giriş duvar fiyatından (limit) · SL {SL_SIGMA}σ ötede · TP {TP_R}R · "
             f"time-stop {uf}s · fee+slip düşülü")
    s.append("")
    kartlar = sonuc["kartlar"]
    for ad, k in sorted(kartlar.items(),
                        key=lambda x: -(x[1].get("beklenti") or -9e9)):
        s.append(_sat(k))
    s.append("")
    s.append(f"DSR cezası n_deneme={N_DENEME} (pencere×rejim×duvar×kol tarama genişliği). "
             f"✅ GERÇEK EDGE = DSR≥0.95 ∧ CI-alt>0 ∧ perm-p<0.05 (FDR) ∧ 5x-likidasyon=0")
    uyari = sonuc.get("ters_kol_uyari") or []
    if uyari:
        s.append("")
        s.append("⚠️ TERS KOL UYARISI:")
        for u in uyari:
            s.append(f"   • {u}")
    else:
        s.append("")
        s.append("✓ Ters kol kontrolü: FADE ve KIRILIM kolları birlikte pozitif DEĞİL "
                 "(dönem eğilimi şüphesi yok).")
    s.append("")
    s.append("⚠️ SINIRLAR: ① duvar haritası MODELDİR (giriş fiyatı/kaldıraç kamuya açık değil; "
             "kaldıraç dağılımı varsayım) ② aynı gün üst+alt duvar birlikte tetiklenebilir → "
             "işlemler tam bağımsız değil ③ metrics parquet 2021+ ④ serap testinden geçse bile "
             "canlıya bağlamak ANAYASA #8 onayı ister.")
    return "\n".join(s)


# ═══════════════════════════════════════════════════════════════════════════════
#  SELF-TEST (parquet/ağ GEREKMEZ)
# ═══════════════════════════════════════════════════════════════════════════════
def kendi_test():
    import numpy as np
    print("═══ oar_duvar_gecerlilik — self-test ═══\n", flush=True)
    ok = True

    def kontrol(ad, kosul, ek=""):
        nonlocal ok
        print(f"  {'✓' if kosul else '✗'} {ad}{(' — ' + ek) if ek else ''}", flush=True)
        ok = ok and bool(kosul)

    # ── 1) olay sınıflandırma ──
    sig = 10.0   # σ_px
    W = 1000.0
    # üst duvara dokunup geri çekildi (tuttu): high 1000, sonra low 990 (=W−0.5σ*... )
    hi = np.array([995., 1001., 998., 996.]); lo = np.array([990., 997., 993., 990.])
    d, j = duvar_olayi(W, sig, True, hi, lo, 0, 4)
    kontrol("üst duvar TUTTU", d == "TUTTU" and j == 1, f"durum={d}")

    # üst duvarı 0.5σ aştı (kırdı): 1000+5 = 1005
    hi = np.array([995., 1006., 1010., 1012.]); lo = np.array([990., 999., 1004., 1008.])
    d, _ = duvar_olayi(W, sig, True, hi, lo, 0, 4)
    kontrol("üst duvar KIRDI", d == "KIRDI", f"durum={d}")

    # hiç dokunmadı
    hi = np.array([980., 985., 990., 992.]); lo = np.array([970., 975., 980., 985.])
    d, _ = duvar_olayi(W, sig, True, hi, lo, 0, 4)
    kontrol("DOKUNMADI", d == "DOKUNMADI", f"durum={d}")

    # aynı barda hem kırılım hem tepki → KIRILIM kazanmalı (kötümser)
    hi = np.array([1006.]); lo = np.array([994.])
    d, _ = duvar_olayi(W, sig, True, hi, lo, 0, 1)
    kontrol("eşitlikte KIRILIM önce (kötümser)", d == "KIRDI", f"durum={d}")

    # alt duvar aynası
    lo = np.array([1005., 999., 1002., 1004.]); hi = np.array([1010., 1006., 1007., 1009.])
    d, _ = duvar_olayi(W, sig, False, hi, lo, 0, 4)
    kontrol("alt duvar TUTTU (ayna)", d == "TUTTU", f"durum={d}")

    # ── 2) işlem simülasyonu ──
    # üst duvardan SHORT, fiyat düşüyor → TP (3R = 30 aşağı → 970)
    hi = np.array([1000., 998., 995., 990.]); lo = np.array([999., 994., 985., 968.])
    cl = np.array([999., 995., 986., 969.])
    net, tur = _simule_duvar("SHORT", W, sig, 0, 4, hi, lo, cl)
    kontrol("SHORT fade → TP", tur == "TP" and net > 0, f"{tur} net={net:.3f}")
    # fiyat yükseliyor → SL (1σ = 1010)
    hi = np.array([1000., 1005., 1012., 1020.]); lo = np.array([999., 1000., 1004., 1010.])
    cl = np.array([1000., 1004., 1011., 1019.])
    net, tur = _simule_duvar("SHORT", W, sig, 0, 4, hi, lo, cl)
    kontrol("SHORT fade → SL", tur == "SL" and net < 0, f"{tur} net={net:.3f}")
    # aynı barda SL+TP → SL önce
    hi = np.array([1011.]); lo = np.array([969.]); cl = np.array([1000.])
    net, tur = _simule_duvar("SHORT", W, sig, 0, 1, hi, lo, cl)
    kontrol("SL/TP çakışması → SL önce", tur == "SL", f"{tur}")

    # ── 3) rejim kovaları (mutlak çıpa) ──
    kontrol("rejim: negatif", _rejim(-0.0002) == "negatif")
    kontrol("rejim: nötr", _rejim(0.00005) == "notr")
    kontrol("rejim: pozitif", _rejim(0.00015) == "pozitif")
    kontrol("rejim: aşırı", _rejim(0.0003) == "asiri_poz", "≥2× taban")

    # ── 4) olasılık katmanı: GÖMÜLÜ mekanizma yakalanıyor mu ──
    # aşırı-poz günlerde alt duvar hep kırılıyor, negatif günlerde hep tutuyor
    satir = []
    for i in range(200):
        rej = "asiri_poz" if i % 2 == 0 else "negatif"
        durum = ("KIRDI" if rej == "asiri_poz" else "TUTTU")
        r = {"gun": i, "alt_durum": durum, "ust_durum": "TUTTU" if rej == "asiri_poz" else "KIRDI"}
        for ad, _ in PENCERELER:
            r["rejim_" + ad] = rej
        satir.append(r)
    ol = olasilik_tablosu(satir)
    alt = ol[ANA_PENCERE]["alt"]["kovalar"]
    kontrol("gömülü mekanizma: aşırı-poz'da ALT kırılma %100",
            alt["asiri_poz"]["kirilma_orani"] == 1.0 and alt["asiri_poz"]["tabandan_ayrik"])
    kontrol("gömülü mekanizma: negatif'te ALT tutma %100",
            alt["negatif"]["tutma_orani"] == 1.0 and alt["negatif"]["tabandan_ayrik"])
    karne = hipotez_karnesi(ol)
    kontrol("hipotez karnesi H1/H2 DOĞRULANDI der",
            all("DOĞRULANDI" in v["durum"] for v in karne[ANA_PENCERE].values()),
            str({k: v["durum"] for k, v in karne[ANA_PENCERE].items()}))

    # ── 5) SAF GÜRÜLTÜ: mekanizma YOKken uydurmamalı ──
    import random
    random.seed(7)
    satir = []
    for i in range(400):
        rej = random.choice(REJIM_SIRA)
        r = {"gun": i,
             "alt_durum": random.choice(["TUTTU", "KIRDI"]),
             "ust_durum": random.choice(["TUTTU", "KIRDI"])}
        for ad, _ in PENCERELER:
            r["rejim_" + ad] = rej
        satir.append(r)
    ol = olasilik_tablosu(satir)
    ayrik = [k.get("tabandan_ayrik") for k in ol[ANA_PENCERE]["alt"]["kovalar"].values()
             if not k.get("yetersiz")]
    kontrol("saf gürültü: hiçbir kova tabandan ayrışmıyor", not any(ayrik), str(ayrik))
    karne = hipotez_karnesi(ol)
    kontrol("saf gürültü: hipotez DOĞRULANDI demiyor",
            not any("DOĞRULANDI" in v["durum"] for v in karne[ANA_PENCERE].values()),
            str({k: v["durum"] for k, v in karne[ANA_PENCERE].items()}))
    # ⚠️ gürültüde LIFT işareti rahatça ±8 puan sallanır — o yüzden karar Wilson'a bağlı:
    #    hiçbir kova ayrışmadığı sürece karne "AYRIŞMADI" demeli, "eğilim" DEMEMELİ.
    kontrol("saf gürültü: tüm hücreler 'AYRIŞMADI' (LIFT işaretine aldanmıyor)",
            all("AYRIŞMADI" in v["durum"] for v in karne[ANA_PENCERE].values()),
            str({k: round(v["lift"], 1) for k, v in karne[ANA_PENCERE].items()}))
    ck = coklu_karsilastirma(ol)
    kontrol("saf gürültü: çoklu-karşılaştırma 'ŞANS SEVİYESİNDE' der",
            "ŞANS SEVİYESİNDE" in ck["yargi"],
            f"{ck['gozlenen_yildiz']} ★ / {ck['test_edilen_hucre']} hücre "
            f"(beklenen ~{ck['sansla_beklenen_yildiz']})")

    # ── 5b) çoklu-karşılaştırma muhasebesi TEK ★'ı kanıt saymamalı ──
    sahte = {"p1": {"ust": {"kovalar": {
        "a": {"tabandan_ayrik": True}, **{f"k{i}": {"tabandan_ayrik": False} for i in range(36)}}}}}
    ck2 = coklu_karsilastirma(sahte)
    kontrol("37 hücrede 1 ★ → 'ŞANS SEVİYESİNDE'",
            "ŞANS SEVİYESİNDE" in ck2["yargi"],
            f"beklenen ~{ck2['sansla_beklenen_yildiz']} · gözlenen {ck2['gozlenen_yildiz']}")
    sahte2 = {"p1": {"ust": {"kovalar": {
        **{f"a{i}": {"tabandan_ayrik": True} for i in range(8)},
        **{f"k{i}": {"tabandan_ayrik": False} for i in range(29)}}}}}
    ck3 = coklu_karsilastirma(sahte2)
    kontrol("37 hücrede 8 ★ → 'ŞANSTAN FAZLA'",
            "ŞANSTAN FAZLA" in ck3["yargi"],
            f"beklenen ~{ck3['sansla_beklenen_yildiz']} · gözlenen {ck3['gozlenen_yildiz']}")

    # ── 6) no-lookahead: duvar seviyesi karar anından SONRAKİ veriyi kullanmıyor ──
    # duvar_olayi yalnız [i0,i1) penceresine bakar; i0 öncesi bar sonucu değiştirmemeli
    hi_a = np.array([9999., 995., 1001., 998., 996.]); lo_a = np.array([9999., 990., 997., 993., 990.])
    d_a, _ = duvar_olayi(W, sig, True, hi_a, lo_a, 1, 5)
    hi_b = np.array([1.,    995., 1001., 998., 996.]); lo_b = np.array([1.,    990., 997., 993., 990.])
    d_b, _ = duvar_olayi(W, sig, True, hi_b, lo_b, 1, 5)
    kontrol("no-lookahead: pencere öncesi bar sonucu değiştirmiyor", d_a == d_b, f"{d_a}/{d_b}")

    print(f"\n{'✅ TÜM TESTLER GEÇTİ' if ok else '❌ TEST BAŞARISIZ'}", flush=True)
    return 0 if ok else 1


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(
        description="Duvar geçerliliği: funding rejimi duvarın tutup tutmayacağını söylüyor mu")
    ap.add_argument("--symbol", default="BTCUSDT,ETHUSDT")
    ap.add_argument("--from", dest="bas", default="2021-01",
                    help="başlangıç ayı (metrics parquet 2021+ olduğu için varsayılan 2021-01)")
    ap.add_argument("--to", dest="bit", default="2025-06")
    ap.add_argument("--ufuk-saat", type=int, default=72,
                    help="duvara ulaşma/çözülme ufku (saat). Varsayılan 72: duvar ÇOK GÜNLÜK "
                         "bir nesne (medyan mesafe birkaç sigma), 24 saat yapısal olarak kısa "
                         "kalıp temas oranini eritir. Kıyas icin 24 verilebilir.")
    ap.add_argument("--telegram", action="store_true", help="raporu ajan kanalına gönder")
    ap.add_argument("--kendi-test", action="store_true",
                    help="parquet/ag gerektirmeyen mekanik dogrulama")
    a = ap.parse_args()

    if a.kendi_test:
        raise SystemExit(kendi_test())

    semboller = [s.strip().upper() for s in a.symbol.split(",") if s.strip()]
    tum_satir, seri_liste = [], []
    for s in semboller:
        print(f"[Duvar] {s} {a.bas}..{a.bit} · ufuk {a.ufuk_saat}s — günlük tablo kuruluyor…",
              flush=True)
        satirlar, hi, lo, cl = gun_tablosu(s, a.bas, a.bit, ufuk_saat=a.ufuk_saat)
        if not satirlar:
            print(f"   [{s}] ⚠ satır yok, atlanıyor", flush=True)
            continue
        print(f"   [{s}] ✓ {len(satirlar)} karar günü", flush=True)
        havuz = _islem_havuzu(satirlar, hi, lo, cl)
        seri_liste.append(seriler_uret(satirlar, havuz))
        tum_satir.extend(satirlar)

    if not tum_satir:
        print("⚠ hiç karar günü üretilemedi — klines/metrics/funding verisini kontrol et.",
              flush=True)
        raise SystemExit(1)

    olasilik = olasilik_tablosu(tum_satir)
    karne = hipotez_karnesi(olasilik)
    tani = mesafe_tanisi(tum_satir, a.ufuk_saat)
    seriler = seriler_birlestir(seri_liste)
    yon_map = {ad: yon for ad, yon, _kol, _sec in _varyantlar()}
    print("[Duvar] serap bateri uygulanıyor…", flush=True)
    kartlar = degerlendir(seriler, yon_map=yon_map, n_deneme=N_DENEME)

    sonuc = {
        "sembol": ",".join(semboller), "bas": a.bas, "bit": a.bit,
        "gun_sayisi": len(tum_satir),
        "parametreler": {
            "kirilim_sigma": KIRILIM_SIGMA, "tepki_sigma": TEPKI_SIGMA,
            "sl_sigma": SL_SIGMA, "tp_r": TP_R, "tut_saat": TUT_SAAT,
            "ana_pencere": ANA_PENCERE, "n_deneme": N_DENEME,
            "taban_oran": TABAN_ORAN, "asiri_carpan": ASIRI_CARPAN,
            "ufuk_saat": a.ufuk_saat,
        },
        "mesafe_tanisi": tani, "coklu_karsilastirma": coklu_karsilastirma(olasilik),
        "olasilik": olasilik, "hipotez_karnesi": karne, "kartlar": kartlar,
    }
    sonuc["ters_kol_uyari"] = _ters_kontrol(kartlar)

    rapor = rapor_metni(sonuc)
    print("\n" + rapor, flush=True)
    Path(CIKTI).write_text(json.dumps(sonuc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✓ {CIKTI} yazıldı (commit+push → lider+Claude okur)", flush=True)

    if a.telegram:
        try:
            import asyncio                       # ⚠ bildir ASYNC — asyncio.run şart
            from ajan_merkez import bildir
            asyncio.run(bildir("Duvar Geçerliliği", "backtest",
                               f"Duvar geçerliliği {sonuc['sembol']} — "
                               f"{sonuc['gun_sayisi']} karar günü", detay=rapor))
            print("[Telegram] gönderildi ✓", flush=True)
        except Exception as e:
            print(f"[Telegram] gönderilemedi: {e}", flush=True)


if __name__ == "__main__":
    main()
