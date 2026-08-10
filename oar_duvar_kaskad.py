"""
oar_duvar_kaskad.py — LİKİDASYON SEVİYESİ = TETİKLEYİCİ Mİ? (kullanıcı düzeltmesi)
═══════════════════════════════════════════════════════════════════════════════════════
⚠️ ÖNCEKİ MODÜLÜN (oar_duvar_gecerlilik) SORUSU YANLIŞTI — kullanıcı düzeltti:
   "duvar olup olmaması değil aslında mesele; fiyat şu seviyeyi aşarsa DEVAM EDER
    tarzı duvar bunlar."
   HAKLI. Likidasyon kümesi bir BARİYER değil, bir TETİKLEYİCİ'dir. Fiyat seviyeye
   ulaşınca zorunlu emirler ateşlenir (long likidasyonu → zorunlu SATIŞ, short
   likidasyonu → zorunlu ALIŞ) ve hareket HIZLANARAK DEVAM eder. "Tuttu mu/kırıldı mı"
   çerçevesi mekanizmaya terstir; §5ab'nin tüm bulguları o yanlış çerçevede üretildi.

⭐ ÖN-KAYITLI HİPOTEZ (tahmin ÖNCE yazıldı):
   **H3 (ANA):** Fiyat BÜYÜK notional taşıyan bir seviyeyi aştığında, KÜÇÜK notional
   taşıyan bir seviyeyi aşmasına göre DAHA FAZLA devam eder — aynı gün, aynı yön,
   aynı mesafe bandında. Etki notional kartiliyle MONOTON artar.
   **H4 (ikincil):** Etki kalabalık tarafta daha güçlüdür (pozitif funding → longlar
   kalabalık → AŞAĞI kesişimler daha çok kaskad yapar).
   Ters çıkarsa hipotez ÇÜRÜR; "başka kombinasyon çalıştı" diye KURTARILMAZ (§5m).

🔑 BU TASARIMIN ÖNCEKİNDEN ÜSTÜN OLDUĞU 3 NOKTA:
   ① **PLASEBO İÇERİDE**: küçük-notional seviyeler AYNI GÜNLERDE kesiliyor. Devam
      ikisinde de aynıysa "zorunlu akış" diye bir şey YOKTUR — sadece momentumdur.
      §5ab'de kontrol "tüm günler ortalaması"ydı; dönem eğilimi sızıyordu (ters kol
      uyarısı tam da bunu yakaladı).
   ② **EŞLEŞTİRİLMİŞ (paired) TEST**: aynı gün + aynı yön + aynı mesafe bandı içinde
      Q4−Q1 farkı alınır. Gün volatilitesi, trend, rejim, σ — hepsi FARKTA SADELEŞİR.
      Gözlemsel bir soruda ulaşılabilecek en temiz kontrol budur.
   ③ **n PATLIYOR**: tek bir "en büyük duvar" yerine merdivendeki HER seviye ölçülür
      (§5ab'de temas %23'te kalmıştı; burada gün başına onlarca kesişim var).

⚠️ MESAFE KONFAUNDU (ANAYASA #3 — varsayım değil, kontrol): spot'a yakın seviyeler
   hem daha çok kesilir hem farklı notional taşıyabilir. Bu yüzden kıyas DAİMA aynı
   mesafe bandı içinde yapılır ve her kartilin mesafe dağılımı rapora YAZILIR.

NO-LOOKAHEAD:
  • Harita 04:00 UTC'de YALNIZ geçmişten kurulur (oar_likidasyon_haritasi boru hattı:
    7 günlük OI penceresi, likidasyona değmiş pozisyon düşer, OI azalış sönümü).
  • Seviye merdiveni karar anında BİLİNİR → seviyede bekleyen limit emir gerçekçi.
  • Betimleyici DEVAM ölçümü kesişim barından SONRA başlar (j+1) → kesişim barının
    kendi hareketi devam diye sayılmaz (KÖTÜMSER).
  • İşlem katmanı ev standardı: kesişim barı DAHİL, SL/TP çakışmasında SL ÖNCE.
  • funding yalnız ödenmiş periyotlardan; σ_gün önceki 20 günden.

⚠️ VERİ SINIRI: harita OI'ye dayanır → metrics parquet **2021+** (§5aa).
⚠️ ŞAMPİYONLARA DOKUNMAZ (ANAYASA #8). Serap testinden geçse bile canlıya bağlamak
   AYRI onay ister (§5p).

Çalıştırma:
  python oar_duvar_kaskad.py --symbol BTCUSDT,ETHUSDT --from 2021-01 --to 2025-06 --telegram
  python oar_duvar_kaskad.py --kendi-test        # parquet/ağ GEREKMEZ
"""
import argparse
import json
from pathlib import Path

from oar_local_backtest import GUN_MS, SAAT_MS, FEE_PCT, SLIP_PCT
from oar_wsd_backtest import (KARAR_SAAT_UTC, SIGMA_PENCERE, TP_R, OT_MIN, OT_MAX,
                              _olcek_dogrula, _ts_ms, _utc, degerlendir, _g)
from oar_likidasyon_haritasi import (KALDIRAC_DAGILIM, PENCERE_GUN, BANT_PCT, BIN_PCT,
                                     _harita_kur, oi_sonum)
from oar_funding_carry import funding_oku, TABAN_ORAN, ASIRI_CARPAN, PERIYOT_MS

# ── Ölçüm parametreleri ───────────────────────────────────────────────────────
UFUK_SAAT    = 72     # kesişim aranan pencere (duvar çok günlük nesne — §5ab teşhisi)
DEVAM_SAAT   = 6      # kesişim sonrası devam ölçüm ufku (kaskad hızlı olmalı)
SL_SIGMA     = 1.0    # işlem SL genişliği (ev standardı)
MESAFE_BANT  = [(0.0, 1.0), (1.0, 2.0), (2.0, 4.0)]   # σ_gün cinsinden eşleştirme bandı
MIN_N        = 40
MIN_PAIR_N   = 30     # eşleştirilmiş test için asgari çift
CIKTI        = "duvar_kaskad_sonuc.json"

# Gerçek tarama genişliği: 2 yön × 3 mesafe bandı × 4 kartil × 2 ufuk-seçimi ≈ 48;
# üstüne funding rejim kırılımı (4) → DSR cezası buna göre.
N_DENEME     = 96

REJIM_SIRA = ["negatif", "notr", "pozitif", "asiri_poz"]


def _rejim(ort):
    if ort is None:
        return None
    if ort < 0:
        return "negatif"
    if ort <= TABAN_ORAN:
        return "notr"
    if ort < ASIRI_CARPAN * TABAN_ORAN:
        return "pozitif"
    return "asiri_poz"


# ═══════════════════════════════════════════════════════════════════════════════
#  MERDİVEN (duvar_tablosu ile AYNI binleme — tek "en büyük duvar" değil, HEPSİ)
# ═══════════════════════════════════════════════════════════════════════════════
def merdiven(alt_s, alt_n, ust_s, ust_n, spot, bant=BANT_PCT):
    """
    Likidasyon seviyelerini fiyat kovalarına topla → tam merdiven.
    Döner: [{fiyat, mesafe_pct, alis, satis}] (alis = short likidasyonu = zorunlu ALIŞ).
    ⚠️ oar_likidasyon_haritasi.duvar_tablosu'ndaki binleme kuralıyla BİREBİR aynı
       (canlı panelde görünen seviyelerle aynı şeyi ölçelim diye).
    """
    genislik = spot * BIN_PCT / 100.0
    if genislik <= 0:
        return []
    kova = {}
    for sev, nots, yon in ((ust_s, ust_n, "alis"), (alt_s, alt_n, "satis")):
        for p, n in zip(sev, nots):
            mes = (p - spot) / spot * 100.0
            if abs(mes) > bant:
                continue
            b = round(round(p / genislik) * genislik, 2)
            h = kova.setdefault(b, {"fiyat": b, "alis": 0.0, "satis": 0.0})
            h[yon] += float(n)
    out = []
    for r in kova.values():
        if r["fiyat"] <= 0:
            continue
        r["mesafe_pct"] = (r["fiyat"] - spot) / spot * 100.0
        out.append(r)
    return sorted(out, key=lambda r: r["fiyat"])


def _kartil_ata(kayitlar, alan="notional"):
    """
    Aynı gün + aynı yön içindeki seviyeleri notional'a göre 4 kartile böler.
    ⚠️ GÜN-İÇİ normalizasyon şart: OI ölçeği yıllar içinde büyüyor; ham notional
    kıyası 2021 ile 2025'i karşılaştırmaya çalışır (sahte trend).
    """
    import numpy as np
    if len(kayitlar) < 4:
        for k in kayitlar:
            k["kartil"] = None
        return
    v = np.asarray([k[alan] for k in kayitlar], dtype=float)
    sinir = np.percentile(v, [25, 50, 75])
    for k in kayitlar:
        x = k[alan]
        k["kartil"] = 1 if x <= sinir[0] else (2 if x <= sinir[1] else
                                               (3 if x <= sinir[2] else 4))


def _mesafe_bandi(sigma_mes):
    for i, (a, b) in enumerate(MESAFE_BANT):
        if a <= sigma_mes < b:
            return f"{a:g}-{b:g}σ"
    return None


# ═══════════════════════════════════════════════════════════════════════════════
#  KESİŞİM + DEVAM (saf fonksiyon — self-test bunu doğrudan sınar)
# ═══════════════════════════════════════════════════════════════════════════════
def kesisim_bul(seviye, yukari, hi, lo, i0, i1):
    """Seviyenin İLK kesildiği bar indeksi (yoksa None)."""
    import numpy as np
    if i1 <= i0:
        return None
    dizi = hi[i0:i1] >= seviye if yukari else lo[i0:i1] <= seviye
    if not bool(dizi.any()):
        return None
    return i0 + int(np.argmax(dizi))


def devam_olc(seviye, yukari, sigma_px, j, hi, lo, cl, bar_sayisi, son_bar):
    """
    Kesişimden SONRA (j+1'den itibaren) devam — σ_gün cinsinden, kesişim YÖNÜNDE +.
    Döner: (net, mfe) veya None.
      net = pencerenin SONUNDAKİ kapanışın seviyeye göre yer değiştirmesi (tradeable)
      mfe = pencere içindeki EN İYİ devam (impulse büyüklüğü)
    ⚠️ Kesişim barı (j) DIŞARIDA — o barın kendi hareketi "devam" sayılmaz (kötümser).
    """
    a = j + 1
    b = min(a + bar_sayisi, son_bar)
    if sigma_px <= 0 or a >= b:
        return None
    if yukari:
        net = (float(cl[b - 1]) - seviye) / sigma_px
        mfe = (float(hi[a:b].max()) - seviye) / sigma_px
    else:
        net = (seviye - float(cl[b - 1])) / sigma_px
        mfe = (seviye - float(lo[a:b].min())) / sigma_px
    return round(net, 5), round(mfe, 5)


def _simule_kesisim(yon, giris, sigma_px, j, i_son, hi, lo, cl):
    """İşlem: seviyeden kesişim yönünde giriş, SL SL_SIGMA·σ, TP 3R, time-stop."""
    import numpy as np
    R = SL_SIGMA * sigma_px
    i1 = min(j + int(DEVAM_SAAT * 60), i_son)
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
#  GÜNLÜK TABLO → KESİŞİM OLAYLARI
# ═══════════════════════════════════════════════════════════════════════════════
def olay_tablosu(sembol, bas, bit, k_df=None, m_df=None, funding=None,
                 kaldirac_dagilim=None, ufuk_saat=UFUK_SAAT):
    """
    Döner: (olaylar, gun_sayisi)
    olaylar[] = {gun, ts, yon(UP/DOWN), seviye, mesafe_sigma, mesafe_bandi,
                 notional, pay, kartil, devam_net, devam_mfe, islem(net,tur), rejim_*}
    """
    import numpy as np
    import pandas as pd
    from oar_local_backtest import _klines_oku, _metrics_oku, _ms_olcekle

    kaldirac_dagilim = kaldirac_dagilim or KALDIRAC_DAGILIM
    k = k_df if k_df is not None else _klines_oku(sembol, bas, bit)
    if k is None or not len(k):
        print(f"      ⚠ {sembol}: klines yok", flush=True)
        return [], 0
    m = m_df if m_df is not None else _metrics_oku(sembol, bas, bit)
    if m is None or not len(m):
        print(f"      ⚠ {sembol}: metrics parquet yok (OI olmadan harita kurulamaz)", flush=True)
        return [], 0
    kayitlar = funding if funding is not None else funding_oku(sembol)
    if not kayitlar:
        print(f"      ⚠ {sembol}: funding geçmişi yok → "
              f"`python oar_funding_carry.py --indir --symbol {sembol}`", flush=True)
        kayitlar = []

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

    if kayitlar:
        fts = np.array([int(r["ts"]) for r in kayitlar], dtype="int64")
        frt = np.array([float(r["rate"]) for r in kayitlar], dtype="float64")
        sira = np.argsort(fts)
        fts, frt = fts[sira], frt[sira]
        _olcek_dogrula(f"{sembol} funding", fts)
    else:
        fts = frt = None

    gd = pd.DataFrame({"gun": ot // GUN_MS, "high": hi, "low": lo, "open": op}) \
           .groupby("gun").agg(h=("high", "max"), l=("low", "min"), o=("open", "first"))
    rngs = ((gd["h"] - gd["l"]) / gd["o"] * 100.0).to_numpy(dtype="float64")
    gunler = gd.index.to_numpy()
    sigma_map = {int(gunler[i]): float(rngs[i - SIGMA_PENCERE:i].mean())
                 for i in range(SIGMA_PENCERE, len(gunler))
                 if np.isfinite(rngs[i - SIGMA_PENCERE:i]).all()
                 and rngs[i - SIGMA_PENCERE:i].mean() > 0}

    olaylar, son_ay, gun_sayisi = [], "", 0
    red = {"sigma_penceresi": 0, "giris_bari": 0, "ileri_pencere": 0, "oi_yok": 0}
    devam_bar = int(DEVAM_SAAT * 60)
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
        pen_k0 = int(np.searchsorted(ot, pen_bas, side="left"))
        sfx_min = np.minimum.accumulate(lo[pen_k0:gi + 1][::-1])[::-1]
        sfx_max = np.maximum.accumulate(hi[pen_k0:gi + 1][::-1])[::-1]
        yerel = np.clip(ki - pen_k0, 0, len(sfx_min) - 1)
        spot = float(cl[gi])
        sigma_px = spot * sigma / 100.0
        hayatta = oi_sonum(oi_val[ma:mb], d_oi[ma:mb])[artis][gecerli]

        alt_s, alt_n, ust_s, ust_n = _harita_kur(
            cl[ki], yeni[artis][gecerli], sfx_min[yerel], sfx_max[yerel], spot,
            kaldirac_dagilim, hayatta_pay=hayatta)
        basamaklar = merdiven(alt_s, alt_n, ust_s, ust_n, spot)
        if not basamaklar:
            red["oi_yok"] += 1
            continue
        gun_sayisi += 1

        # funding rejimleri (ödenmiş periyotlardan)
        rejimler = {}
        if fts is not None:
            fi = int(np.searchsorted(fts, karar_ts, side="right")) - 1
            if fi >= 0 and karar_ts - int(fts[fi]) <= 2 * PERIYOT_MS:
                for ad, np_ in (("f_1g", 3), ("f_3g", 9), ("f_7g", 21)):
                    pen = frt[max(0, fi - np_ + 1):fi + 1]
                    rejimler["rejim_" + ad] = _rejim(float(pen.mean())) if len(pen) else None

        # ── her yön için AYRI kartil (gün-içi normalizasyon) ──
        for yukari, alan in ((True, "alis"), (False, "satis")):
            aday = []
            for b in basamaklar:
                if yukari and b["mesafe_pct"] <= 0:
                    continue
                if not yukari and b["mesafe_pct"] >= 0:
                    continue
                n = b[alan]
                if n <= 0:
                    continue
                sig_mes = abs(b["mesafe_pct"]) / sigma if sigma > 0 else None
                bant = _mesafe_bandi(sig_mes) if sig_mes is not None else None
                if bant is None:            # 4σ ötesi: kesişim ~imkânsız (§5ab teşhisi)
                    continue
                aday.append({"fiyat": b["fiyat"], "notional": n,
                             "mesafe_sigma": round(sig_mes, 3), "mesafe_bandi": bant})
            if len(aday) < 4:
                continue
            _kartil_ata(aday)
            toplam = sum(a["notional"] for a in aday) or 1.0

            for a in aday:
                j = kesisim_bul(a["fiyat"], yukari, hi, lo, i0, i1)
                if j is None:
                    continue
                d = devam_olc(a["fiyat"], yukari, sigma_px, j, hi, lo, cl,
                              devam_bar, len(cl))
                if d is None:
                    continue
                islem = _simule_kesisim("LONG" if yukari else "SHORT", a["fiyat"],
                                        sigma_px, j, len(cl), hi, lo, cl)
                olaylar.append({
                    "gun": g, "ts": karar_ts, "yon": "UP" if yukari else "DOWN",
                    "seviye": a["fiyat"], "mesafe_sigma": a["mesafe_sigma"],
                    "mesafe_bandi": a["mesafe_bandi"], "kartil": a["kartil"],
                    "notional": a["notional"], "pay": round(a["notional"] / toplam, 5),
                    "devam_net": d[0], "devam_mfe": d[1],
                    "islem_net": (islem or (None, None))[0],
                    "islem_tur": (islem or (None, None))[1],
                    **rejimler,
                })

    if red["oi_yok"] or not olaylar:
        ara = lambda a: (f"{_utc(a[0]):%Y-%m-%d} → {_utc(a[-1]):%Y-%m-%d}") if len(a) else "boş"
        print(f"      · elenen gün: {red} | klines {ara(ot)} | metrics {ara(mts)}", flush=True)
    return olaylar, gun_sayisi


# ═══════════════════════════════════════════════════════════════════════════════
#  ① KARTİL KIYASI (ham — mesafe konfaundu GÖRÜNÜR yazılır)
# ═══════════════════════════════════════════════════════════════════════════════
def kartil_tablosu(olaylar, alan="devam_net"):
    import numpy as np
    out = {}
    for yon in ("UP", "DOWN"):
        alt = [o for o in olaylar if o["yon"] == yon and o.get(alan) is not None]
        if not alt:
            continue
        blok = {}
        for q in (1, 2, 3, 4):
            v = [o for o in alt if o["kartil"] == q]
            if len(v) < MIN_N:
                blok[q] = {"n": len(v), "yetersiz": True}
                continue
            d = np.asarray([o[alan] for o in v], dtype=float)
            mes = np.asarray([o["mesafe_sigma"] for o in v], dtype=float)
            blok[q] = {
                "n": len(v), "devam_ort": round(float(d.mean()), 4),
                "devam_medyan": round(float(np.median(d)), 4),
                "pozitif_pay": round(float((d > 0).mean()), 4),
                # ⚠️ KONFAUND KONTROLÜ: kartiller farklı mesafede duruyorsa kıyas kirli
                "mesafe_ort": round(float(mes.mean()), 3),
                "gun": len({o["gun"] for o in v}),
            }
        gecerli = [blok[q]["devam_ort"] for q in (1, 2, 3, 4)
                   if not blok.get(q, {}).get("yetersiz")]
        mono = None
        if len(gecerli) >= 3:
            mono = ("artan" if all(gecerli[i] <= gecerli[i + 1] for i in range(len(gecerli) - 1))
                    else "azalan" if all(gecerli[i] >= gecerli[i + 1] for i in range(len(gecerli) - 1))
                    else "yok")
        # ⚠️ KONFAUND OTOMATİK TESPİTİ: kartillerin ortalama mesafesi belirgin farklıysa
        #    ham monotonluk notional'ın DEĞİL mesafenin eseri olabilir. Bunu göz kararı
        #    bırakma — araç söylesin (gerçek koşuda Q1 0.57σ vs Q4 0.92σ çıktı).
        mesafeler = [blok[q]["mesafe_ort"] for q in (1, 2, 3, 4)
                     if not blok.get(q, {}).get("yetersiz")]
        yayilim = (max(mesafeler) - min(mesafeler)) if len(mesafeler) >= 2 else 0.0
        out[yon] = {"kartiller": blok, "monotonluk": mono,
                    "mesafe_yayilimi": round(yayilim, 3),
                    "mesafe_dengeli": bool(yayilim < 0.15)}
    return out


# ═══════════════════════════════════════════════════════════════════════════════
#  ② EŞLEŞTİRİLMİŞ TEST (ASIL KANIT) — aynı gün + aynı yön + aynı mesafe bandı
# ═══════════════════════════════════════════════════════════════════════════════
def eslestirilmis_test(olaylar, alan="devam_net", tohum=42, blok_gun=None,
                       ufuk_saat=UFUK_SAAT):
    """
    Q4 (büyük notional) − Q1 (küçük notional) farkı, AYNI gün/yön/mesafe bandı içinde.
    Gün volatilitesi, trend, rejim, σ → farkta SADELEŞİR.

    ⚠️⚠️ BLOK BOOTSTRAP ŞART (bulundu+düzeltildi — sıradan bootstrap YANLIŞ CI veriyordu):
    Kesişim ufku {ufuk} saat olduğu için gün g, g+1, g+2 AYNI fiyat yolunu paylaşır;
    ayrıca bir gün birden çok mesafe bandına çift üretir. Yani çiftler BAĞIMSIZ DEĞİL.
    Çiftleri tek tek yeniden örnekleyen bootstrap bağımlılığı yok sayar → CI DARALIR →
    saf gürültüde bile "anlamlı" çıkar (12-seed doğrulamasında tam olarak bu görüldü:
    gerçek fark 0 iken bir koşu p=0.005 ile "TERS" dedi).
    ÇÖZÜM: HAREKETLİ BLOK bootstrap — ardışık `blok_gun` günlük bloklar yeniden
    örneklenir, blok içindeki tüm çiftler birlikte gelir → serisel bağımlılık korunur.
    İşaret testi de GÜN bazında yapılır (gün içi çoklu çift tek oy sayılır).
    Döner: yön → {n_cift, etkin_n, ort_fark, ci95, isaret_testi_p, karar}
    """
    import numpy as np
    from statistics import NormalDist
    blok_gun = blok_gun or max(1, int(round(ufuk_saat / 24.0)))
    rng = np.random.default_rng(tohum)
    out = {}
    for yon in ("UP", "DOWN"):
        gruplar = {}
        for o in olaylar:
            if o["yon"] != yon or o.get(alan) is None or o.get("kartil") is None:
                continue
            gruplar.setdefault((o["gun"], o["mesafe_bandi"]), []).append(o)
        gun_farklar, detay = {}, []
        for (gun, bant), lst in gruplar.items():
            yuksek = [o[alan] for o in lst if o["kartil"] == 4]
            dusuk = [o[alan] for o in lst if o["kartil"] == 1]
            if not yuksek or not dusuk:
                continue
            f = float(np.mean(yuksek) - np.mean(dusuk))
            gun_farklar.setdefault(gun, []).append(f)
            detay.append({"gun": gun, "bant": bant, "fark": round(f, 4)})
        n = sum(len(v) for v in gun_farklar.values())
        gunler = sorted(gun_farklar)
        if n < MIN_PAIR_N or len(gunler) < 2 * blok_gun:
            out[yon] = {"n_cift": n, "gun": len(gunler),
                        "karar": f"❓ YETERSİZ ÇİFT (n={n} < {MIN_PAIR_N})"}
            continue
        tum = np.asarray([f for g in gunler for f in gun_farklar[g]], dtype=float)
        ort = float(tum.mean())

        # ── hareketli blok bootstrap (gün blokları) ──
        G = len(gunler)
        blok_sayi = max(1, int(np.ceil(G / blok_gun)))
        bs = np.empty(5000, dtype=float)
        for b in range(5000):
            bas = rng.integers(0, G, size=blok_sayi)
            secili = []
            for s0 in bas:
                for g in gunler[s0:s0 + blok_gun]:
                    secili.extend(gun_farklar[g])
            bs[b] = float(np.mean(secili)) if secili else np.nan
        bs = bs[np.isfinite(bs)]
        ci = (round(float(np.percentile(bs, 2.5)), 4),
              round(float(np.percentile(bs, 97.5)), 4))

        # ── işaret testi GÜN bazında (gün içi çoklu çift tek oy) ──
        gun_ort = np.asarray([float(np.mean(gun_farklar[g])) for g in gunler])
        arti = int((gun_ort > 0).sum())
        etkin = int((gun_ort != 0).sum())
        p = (2 * (1 - NormalDist(0, 1).cdf(abs(arti - etkin * 0.5) / ((etkin * 0.25) ** 0.5)))
             if etkin >= 20 else None)
        # etkin örneklem: ufuk çakışması nedeniyle bağımsız gün ≈ gün/blok
        etkin_n = round(len(gunler) / blok_gun, 1)

        if ci[0] > 0:
            karar = "✅ Q4 > Q1 (büyük küme DAHA ÇOK devam ettiriyor — H3 yönünde)"
        elif ci[1] < 0:
            karar = "❌ TERS (büyük küme DAHA AZ devam — H3'ün tersi)"
        else:
            karar = "≈ FARK YOK (CI sıfırı içeriyor — kaskad kanıtı YOK)"
        out[yon] = {
            "n_cift": n, "gun": len(gunler), "blok_gun": blok_gun, "etkin_n": etkin_n,
            "ort_fark": round(ort, 4), "ci95": list(ci),
            "pozitif_gun_pay": round(arti / max(1, etkin), 4),
            "isaret_testi_p": round(p, 5) if p is not None else None,
            "karar": karar, "ornek": detay[:5],
        }
    return out


# ═══════════════════════════════════════════════════════════════════════════════
#  ③ İŞLEM KATMANI (plasebo kolu İÇERİDE)
# ═══════════════════════════════════════════════════════════════════════════════
def _varyantlar():
    q = lambda v: (lambda o: o.get("kartil") == v)
    rej = lambda p, v: (lambda o: o.get("rejim_" + p) == v)
    hepsi = lambda o: True
    return [
        ("UP_Q4_BUYUK",     "LONG",  lambda o: o["yon"] == "UP" and o.get("kartil") == 4),
        ("UP_Q1_PLASEBO",   "LONG",  lambda o: o["yon"] == "UP" and o.get("kartil") == 1),
        ("DOWN_Q4_BUYUK",   "SHORT", lambda o: o["yon"] == "DOWN" and o.get("kartil") == 4),
        ("DOWN_Q1_PLASEBO", "SHORT", lambda o: o["yon"] == "DOWN" and o.get("kartil") == 1),
        ("UP_HEPSI",        "LONG",  lambda o: o["yon"] == "UP"),
        ("DOWN_HEPSI",      "SHORT", lambda o: o["yon"] == "DOWN"),
        # H4: kalabalık taraf — pozitif funding → longlar kalabalık → AŞAĞI kaskad
        ("H4_ASIRI_DOWN_Q4", "SHORT",
         lambda o: o["yon"] == "DOWN" and o.get("kartil") == 4
         and o.get("rejim_f_3g") == "asiri_poz"),
        ("H4_NEG_UP_Q4",     "LONG",
         lambda o: o["yon"] == "UP" and o.get("kartil") == 4
         and o.get("rejim_f_3g") == "negatif"),
    ]


def seriler_uret(olaylar):
    seriler = {}
    for ad, _yon, sec in _varyantlar():
        n, t, tu = [], [], []
        for o in olaylar:
            if o.get("islem_net") is None or not sec(o):
                continue
            n.append(o["islem_net"]); t.append(o["ts"]); tu.append(o["islem_tur"])
        seriler[ad] = (n, t, tu)
    return seriler


def seriler_birlestir(liste):
    b = {}
    for s in liste:
        for ad, (n, t, tu) in s.items():
            x, y, z = b.setdefault(ad, ([], [], []))
            x.extend(n); y.extend(t); z.extend(tu)
    for ad, (n, t, tu) in b.items():
        if t:
            sira = sorted(range(len(t)), key=lambda i: t[i])
            b[ad] = ([n[i] for i in sira], [t[i] for i in sira], [tu[i] for i in sira])
    return b


def plasebo_kontrol(kartlar):
    """
    ⚠️⚠️ BU KIYAS MESAFE-EŞLEŞTİRMESİZDİR → KONFAUNDLUDUR. ASIL YARGI DEĞİLDİR.
    Q4 ve Q1 kolları TÜM mesafelerden işlem toplar; kartiller farklı ortalama mesafede
    durduğu için (gerçek koşuda Q1 0.57σ vs Q4 0.92σ) bu kıyas ① ham tablonun taşıdığı
    aynı artefaktı taşır. ASIL YARGI ② eşleştirilmiş testtedir (aynı gün+yön+mesafe).
    Aşağıdaki `plasebo_bantli` mesafe bandı içinde kıyas yapar — okunması gereken odur.
    """
    out = []
    for buyuk, kucuk, ad in (("UP_Q4_BUYUK", "UP_Q1_PLASEBO", "YUKARI"),
                             ("DOWN_Q4_BUYUK", "DOWN_Q1_PLASEBO", "AŞAĞI")):
        kb, kk = kartlar.get(buyuk), kartlar.get(kucuk)
        if not kb or not kk or kb.get("beklenti") is None or kk.get("beklenti") is None:
            out.append(f"{ad}: kıyas yapılamadı (n yetersiz)")
            continue
        fark = kb["beklenti"] - kk["beklenti"]
        out.append(f"{ad}: büyük {kb['beklenti']:+.3f} vs plasebo {kk['beklenti']:+.3f} "
                   f"→ fark {fark:+.3f} ⚠ MESAFE-EŞLEŞTİRMESİZ (konfaundlu)")
    return out


def plasebo_bantli(olaylar):
    """
    MESAFE BANDI İÇİNDE plasebo kıyası — ① ham tablodaki mesafe artefaktı burada YOK.
    Her (yön × mesafe bandı) için Q4 ve Q1 işlem beklentisi ayrı ayrı.
    """
    import numpy as np
    out = {}
    for yon in ("UP", "DOWN"):
        blok = {}
        for bant in [f"{a:g}-{b:g}σ" for a, b in MESAFE_BANT]:
            satir = {}
            for q in (1, 4):
                v = [o["islem_net"] for o in olaylar
                     if o["yon"] == yon and o.get("mesafe_bandi") == bant
                     and o.get("kartil") == q and o.get("islem_net") is not None]
                satir[q] = {"n": len(v),
                            "beklenti": round(float(np.mean(v)), 4) if len(v) >= MIN_N else None}
            b4, b1 = satir[4]["beklenti"], satir[1]["beklenti"]
            satir["fark"] = round(b4 - b1, 4) if (b4 is not None and b1 is not None) else None
            blok[bant] = satir
        out[yon] = blok
    return out


# ═══════════════════════════════════════════════════════════════════════════════
#  RAPOR
# ═══════════════════════════════════════════════════════════════════════════════
def _sat(k):
    if "serap" not in k:
        return f"  {k['ad']:<20} n{_g(k.get('n'), 6)} {k.get('karar', '—')}"
    s = k["serap"]
    dsr = (s.get("deflated_sharpe") or {}).get("dsr")
    ci = (s.get("bootstrap_beklenti_ci") or {}).get("alt")
    lik = ((s.get("mc_equity") or {}).get("5x") or {}).get("likidasyon_orani")
    return (f"  {k['ad']:<20} n{_g(k.get('n'), 6)} WR%{_g(k.get('wr'), 6)} PF {_g(k.get('pf'), 6)}"
            f" bek {_g(k.get('beklenti'), 8)} OOS {_g(k.get('oos_beklenti'), 8)}"
            f" DSR {_g(dsr, 7)} CI-alt {_g(ci, 8)} 5xlik {_g(lik, 6)} {k.get('karar', '')}")


def rapor_metni(sonuc):
    s = ["═══ LİKİDASYON SEVİYESİ = TETİKLEYİCİ Mİ (kaskad testi) ═══",
         f"{sonuc['sembol']} · {sonuc['bas']}..{sonuc['bit']} · {sonuc['gun_sayisi']} gün "
         f"· {sonuc['olay_sayisi']} kesişim olayı · devam ufku {DEVAM_SAAT}s", "",
         "HİPOTEZ H3: büyük notional seviyesi aşılınca, küçük notional seviyesine göre",
         "            DAHA ÇOK devam eder (aynı gün + aynı yön + aynı mesafe bandı).", ""]

    s.append("═══ ① KARTİL KIYASI (ham) — devam σ_gün cinsinden ═══")
    s.append("⚠️ mesafe_ort sütunu KONFAUND kontrolü: kartiller farklı mesafede duruyorsa")
    s.append("   ham kıyas kirlidir → asıl yargı ② eşleştirilmiş testtedir.")
    for yon, blok in (sonuc.get("kartil") or {}).items():
        ok = "YUKARI kesişim (short likidasyonu → zorunlu ALIŞ)" if yon == "UP" \
             else "AŞAĞI kesişim (long likidasyonu → zorunlu SATIŞ)"
        uyari = ("" if blok.get("mesafe_dengeli", True) else
                 f"   ⚠ KONFAUND: kartiller arası mesafe farkı {blok['mesafe_yayilimi']}σ "
                 f"→ bu monotonluk MESAFE eseri olabilir, ②'ye bak")
        s.append(f"  {ok} · monotonluk: {blok['monotonluk'] or '—'}{uyari}")
        s.append(f"     {'kartil':<8}{'n':>7}{'gün':>7}{'devam_ort':>11}{'medyan':>9}"
                 f"{'poz%':>7}{'mesafe_ort':>12}")
        for q in (1, 2, 3, 4):
            k = blok["kartiller"].get(q)
            if not k:
                continue
            if k.get("yetersiz"):
                s.append(f"     Q{q:<7}{k['n']:>7}   — yetersiz (<{MIN_N})")
                continue
            s.append(f"     Q{q:<7}{k['n']:>7}{k['gun']:>7}{k['devam_ort']:>11.4f}"
                     f"{k['devam_medyan']:>9.4f}{k['pozitif_pay']*100:>6.1f}%"
                     f"{k['mesafe_ort']:>11.2f}σ")
        s.append("")

    s.append("═══ ② EŞLEŞTİRİLMİŞ TEST (⭐ ASIL KANIT) — Q4 − Q1, aynı gün/yön/mesafe ═══")
    s.append("⚠️ ① ham tabloda mesafe kartillere DENGESİZ dağılıyorsa (mesafe_ort sütunu),")
    s.append("   oradaki monotonluk notional'ın değil MESAFENİN eseridir. Uzak seviye ancak")
    s.append("   güçlü hareket olduğunda kesilir → seçim etkisi. Burada mesafe SABİTLENİR.")
    for yon, e in (sonuc.get("eslesme") or {}).items():
        ok = "YUKARI" if yon == "UP" else "AŞAĞI"
        if "ort_fark" not in e:
            s.append(f"  {ok}: {e['karar']}")
            continue
        s.append(f"  {ok}: {e['n_cift']} çift / {e['gun']} gün "
                 f"(etkin ~{e['etkin_n']}, blok {e['blok_gun']}g) · "
                 f"ort fark {e['ort_fark']:+.4f}σ "
                 f"· CI95 [{e['ci95'][0]:+.4f}, {e['ci95'][1]:+.4f}] "
                 f"· pozitif gün %{e['pozitif_gun_pay']*100:.1f} "
                 f"· işaret-p {e['isaret_testi_p']}")
        s.append(f"     → {e['karar']}")
    s.append("")

    s.append("═══ ③ İŞLEM KATMANI — plasebo kolu İÇERİDE ═══")
    s.append(f"Giriş seviyeden kesişim yönünde · SL {SL_SIGMA}σ · TP {TP_R}R · "
             f"time-stop {DEVAM_SAAT}s · fee+slip düşülü")
    s.append("")
    for ad, k in sorted((sonuc.get("kartlar") or {}).items(),
                        key=lambda x: -(x[1].get("beklenti") or -9e9)):
        s.append(_sat(k))
    s.append("")
    s.append("── PLASEBO KIYASI (ham — ⚠ mesafe-eşleştirmesiz, KONFAUNDLU) ──")
    for r in sonuc.get("plasebo") or []:
        s.append(f"   • {r}")
    pb = sonuc.get("plasebo_bantli") or {}
    if pb:
        s.append("")
        s.append("── ⭐ PLASEBO KIYASI — MESAFE BANDI İÇİNDE (okunacak olan bu) ──")
        for yon, blok in pb.items():
            ok = "YUKARI" if yon == "UP" else "AŞAĞI"
            for bant, sat in blok.items():
                b4, b1, f = sat[4]["beklenti"], sat[1]["beklenti"], sat["fark"]
                if b4 is None or b1 is None:
                    s.append(f"   • {ok} {bant}: yetersiz n "
                             f"(Q4 {sat[4]['n']} · Q1 {sat[1]['n']})")
                    continue
                yargi = ("büyük küme İYİ" if f > 0 else "KASKAD KANITI YOK")
                s.append(f"   • {ok} {bant}: Q4 {b4:+.3f} (n{sat[4]['n']}) vs "
                         f"Q1 {b1:+.3f} (n{sat[1]['n']}) → fark {f:+.3f} · {yargi}")
    s.append("")
    s.append(f"DSR cezası n_deneme={N_DENEME}. ✅ GERÇEK EDGE = DSR≥0.95 ∧ CI-alt>0 "
             f"∧ perm-p<0.05 (FDR) ∧ 5x-likidasyon=0 — VE plasebodan belirgin iyi.")
    s.append("")
    s.append("⚠️ SINIRLAR: ① harita MODELDİR (giriş fiyatı/kaldıraç kamuya açık değil) "
             "② aynı gün çok sayıda kesişim → olaylar bağımsız değil (eşleştirilmiş test "
             "bunu kısmen çözer; gün sayısı ayrıca yazılır) ③ metrics parquet 2021+ "
             "④ canlıya bağlamak ANAYASA #8 onayı ister.")
    return "\n".join(s)


# ═══════════════════════════════════════════════════════════════════════════════
#  SELF-TEST
# ═══════════════════════════════════════════════════════════════════════════════
def kendi_test():
    import numpy as np
    print("═══ oar_duvar_kaskad — self-test ═══\n", flush=True)
    ok = True

    def kontrol(ad, kosul, ek=""):
        nonlocal ok
        print(f"  {'✓' if kosul else '✗'} {ad}{(' — ' + ek) if ek else ''}", flush=True)
        ok = ok and bool(kosul)

    # ── 1) kesişim bulma ──
    hi = np.array([100., 101., 105., 103.]); lo = np.array([99., 100., 102., 101.])
    kontrol("yukarı kesişim ilk barda bulunur",
            kesisim_bul(104.0, True, hi, lo, 0, 4) == 2)
    kontrol("kesilmeyen seviye None döner",
            kesisim_bul(200.0, True, hi, lo, 0, 4) is None)
    # ⚠️ ayna testi için AYRI dizi: yukarıdaki lo[0]=99 zaten 100'ün altında olduğu için
    #    o dizide "ilk kesişim" bar 0'dır (kod doğru, ilk test beklentim yanlıştı).
    lo_d = np.array([99., 98., 95., 97.]); hi_d = np.array([100., 99., 97., 98.])
    kontrol("aşağı kesişim (ayna)",
            kesisim_bul(96.0, False, hi_d, lo_d, 0, 4) == 2,
            str(kesisim_bul(96.0, False, hi_d, lo_d, 0, 4)))
    kontrol("aşağı: zaten altındaysa ilk bar",
            kesisim_bul(99.5, False, hi_d, lo_d, 0, 4) == 0)

    # ── 2) devam ölçümü: kesişim barı HARİÇ ──
    # seviye 104, kesişim bar 2; sonraki barlarda fiyat yükselirse devam +
    hi = np.array([100., 101., 105., 110., 112.]); lo = np.array([99., 100., 102., 106., 109.])
    cl = np.array([100., 101., 104., 109., 111.])
    d = devam_olc(104.0, True, 10.0, 2, hi, lo, cl, 2, 5)
    kontrol("yukarı devam pozitif ölçülür", d and d[0] > 0 and d[1] > 0, str(d))
    # kesişim barının KENDİ hareketi devam sayılmamalı: j+1'den başladığını doğrula
    hi2 = np.array([100., 101., 130., 104.5, 104.6]); lo2 = np.array([99., 100., 102., 104., 104.2])
    cl2 = np.array([100., 101., 104.2, 104.4, 104.5])
    d2 = devam_olc(104.0, True, 10.0, 2, hi2, lo2, cl2, 2, 5)
    kontrol("kesişim barının kendi spike'ı devam SAYILMAZ",
            d2 is not None and d2[1] < 0.2, f"mfe={d2[1] if d2 else None}")
    # aşağı yön aynası
    hi3 = np.array([100., 99., 96., 92., 90.]); lo3 = np.array([99., 96., 94., 89., 87.])
    cl3 = np.array([99.5, 96.5, 95., 90., 88.])
    d3 = devam_olc(96.0, False, 10.0, 1, hi3, lo3, cl3, 3, 5)
    kontrol("aşağı devam pozitif ölçülür (ayna)", d3 and d3[0] > 0, str(d3))

    # ── 3) merdiven binleme ──
    spot = 1000.0
    alt_s = np.array([990.0, 990.4, 950.0]); alt_n = np.array([10.0, 5.0, 7.0])
    ust_s = np.array([1010.0]); ust_n = np.array([3.0])
    md = merdiven(alt_s, alt_n, ust_s, ust_n, spot)
    toplam_satis = sum(r["satis"] for r in md)
    kontrol("merdiven notional'ı korur", abs(toplam_satis - 22.0) < 1e-6, f"{toplam_satis}")
    kontrol("yakın seviyeler aynı kovada birleşir", len(md) == 3, f"{len(md)} kova")

    # ── 4) kartil ataması gün-içi ──
    kay = [{"notional": v} for v in (1, 2, 3, 4, 5, 6, 7, 800)]
    _kartil_ata(kay)
    kontrol("en büyük notional Q4'e düşer", kay[-1]["kartil"] == 4)
    kontrol("en küçük notional Q1'e düşer", kay[0]["kartil"] == 1)

    # ── 5) EŞLEŞTİRİLMİŞ TEST: gömülü kaskad yakalanıyor mu ──
    rng = np.random.default_rng(11)
    olay = []
    for g in range(200):
        for q, taban in ((1, 0.0), (4, 0.5)):      # Q4 gerçekten daha çok devam ediyor
            olay.append({"gun": g, "yon": "UP", "kartil": q, "mesafe_bandi": "0-1σ",
                         "devam_net": taban + float(rng.normal(0, 0.5)),
                         "mesafe_sigma": 0.5, "ts": g, "islem_net": None})
    e = eslestirilmis_test(olay)
    kontrol("gömülü kaskad: Q4>Q1 tespit edilir",
            e["UP"]["ci95"][0] > 0 and "✅" in e["UP"]["karar"],
            f"fark {e['UP']['ort_fark']:+.3f} CI {e['UP']['ci95']}")

    # ── 6) SAF GÜRÜLTÜ: kaskad UYDURULMAMALI ──
    olay = []
    for g in range(200):
        for q in (1, 4):
            olay.append({"gun": g, "yon": "UP", "kartil": q, "mesafe_bandi": "0-1σ",
                         "devam_net": float(rng.normal(0, 0.5)),
                         "mesafe_sigma": 0.5, "ts": g, "islem_net": None})
    e = eslestirilmis_test(olay)
    kontrol("saf gürültü: 'FARK YOK' der",
            "FARK YOK" in e["UP"]["karar"],
            f"fark {e['UP']['ort_fark']:+.3f} CI {e['UP']['ci95']}")

    # ── 7) TERS etki de doğru raporlanmalı ──
    olay = []
    for g in range(200):
        for q, taban in ((1, 0.5), (4, 0.0)):      # Q4 DAHA AZ devam
            olay.append({"gun": g, "yon": "UP", "kartil": q, "mesafe_bandi": "0-1σ",
                         "devam_net": taban + float(rng.normal(0, 0.5)),
                         "mesafe_sigma": 0.5, "ts": g, "islem_net": None})
    e = eslestirilmis_test(olay)
    kontrol("ters etki 'TERS' diye raporlanır", "TERS" in e["UP"]["karar"],
            e["UP"]["karar"])

    # ── 7b) REGRESYON BEKÇİSİ: blok bootstrap, seri bağımlılıkta naif bootstrap'tan
    #        GENİŞ CI vermeli. (Gerçek hata: naif sürüm saf gürültüde p=0.005 ile
    #        "TERS" dedi — 12-seed doğrulamasında gerçek farkın 0 olduğu görüldü.)
    olay = []
    taban = 0.0
    for g in range(300):
        taban = 0.9 * taban + float(rng.normal(0, 0.3))   # SERİ BAĞIMLI gün etkisi
        for q in (1, 4):
            olay.append({"gun": g, "yon": "UP", "kartil": q, "mesafe_bandi": "0-1σ",
                         "devam_net": (taban if q == 4 else 0.0) + float(rng.normal(0, 0.2)),
                         "mesafe_sigma": 0.5, "ts": g, "islem_net": None})
    genis = eslestirilmis_test(olay, blok_gun=3)["UP"]
    dar = eslestirilmis_test(olay, blok_gun=1)["UP"]
    gen_w = genis["ci95"][1] - genis["ci95"][0]
    dar_w = dar["ci95"][1] - dar["ci95"][0]
    kontrol("blok bootstrap seri bağımlılıkta DAHA GENİŞ CI verir",
            gen_w > dar_w, f"blok3 {gen_w:.4f} > blok1 {dar_w:.4f}")

    # ── 8) eşleştirme GÜN ve BANT'ı gerçekten ayırıyor mu ──
    olay = [{"gun": 1, "yon": "UP", "kartil": 4, "mesafe_bandi": "0-1σ", "devam_net": 5.0,
             "mesafe_sigma": 0.5, "ts": 1, "islem_net": None},
            {"gun": 2, "yon": "UP", "kartil": 1, "mesafe_bandi": "0-1σ", "devam_net": 0.0,
             "mesafe_sigma": 0.5, "ts": 2, "islem_net": None}]
    e = eslestirilmis_test(olay)
    kontrol("farklı GÜNlerdeki Q4/Q1 eşleştirilmez", e["UP"]["n_cift"] == 0,
            f"n_cift={e['UP']['n_cift']}")
    olay = [{"gun": 1, "yon": "UP", "kartil": 4, "mesafe_bandi": "0-1σ", "devam_net": 5.0,
             "mesafe_sigma": 0.5, "ts": 1, "islem_net": None},
            {"gun": 1, "yon": "UP", "kartil": 1, "mesafe_bandi": "1-2σ", "devam_net": 0.0,
             "mesafe_sigma": 1.5, "ts": 1, "islem_net": None}]
    e = eslestirilmis_test(olay)
    kontrol("farklı MESAFE bandındakiler eşleştirilmez", e["UP"]["n_cift"] == 0,
            f"n_cift={e['UP']['n_cift']}")

    print(f"\n{'✅ TÜM TESTLER GEÇTİ' if ok else '❌ TEST BAŞARISIZ'}", flush=True)
    return 0 if ok else 1


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(
        description="Likidasyon seviyesi tetikleyici mi: seviye aşılınca devam eder mi")
    ap.add_argument("--symbol", default="BTCUSDT,ETHUSDT")
    ap.add_argument("--from", dest="bas", default="2021-01",
                    help="baslangic ayi (metrics parquet 2021+)")
    ap.add_argument("--to", dest="bit", default="2025-06")
    ap.add_argument("--ufuk-saat", type=int, default=UFUK_SAAT,
                    help="kesisim aranan pencere (saat)")
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--kendi-test", action="store_true",
                    help="parquet/ag gerektirmeyen mekanik dogrulama")
    a = ap.parse_args()

    if a.kendi_test:
        raise SystemExit(kendi_test())

    semboller = [s.strip().upper() for s in a.symbol.split(",") if s.strip()]
    tum, seri_liste, gun_toplam = [], [], 0
    for s in semboller:
        print(f"[Kaskad] {s} {a.bas}..{a.bit} · ufuk {a.ufuk_saat}s — olaylar çıkarılıyor…",
              flush=True)
        olaylar, gun = olay_tablosu(s, a.bas, a.bit, ufuk_saat=a.ufuk_saat)
        if not olaylar:
            print(f"   [{s}] ⚠ olay yok, atlanıyor", flush=True)
            continue
        print(f"   [{s}] ✓ {gun} gün · {len(olaylar)} kesişim olayı", flush=True)
        seri_liste.append(seriler_uret(olaylar))
        tum.extend(olaylar)
        gun_toplam += gun

    if not tum:
        print("⚠ hiç kesişim olayı üretilemedi — klines/metrics verisini kontrol et.",
              flush=True)
        raise SystemExit(1)

    kartil = kartil_tablosu(tum)
    eslesme = eslestirilmis_test(tum, ufuk_saat=a.ufuk_saat)
    seriler = seriler_birlestir(seri_liste)
    yon_map = {x[0]: x[1] for x in _varyantlar()}
    print("[Kaskad] serap bateri uygulanıyor…", flush=True)
    kartlar = degerlendir(seriler, yon_map=yon_map, n_deneme=N_DENEME)

    sonuc = {
        "sembol": ",".join(semboller), "bas": a.bas, "bit": a.bit,
        "gun_sayisi": gun_toplam, "olay_sayisi": len(tum),
        "parametreler": {"ufuk_saat": a.ufuk_saat, "devam_saat": DEVAM_SAAT,
                         "sl_sigma": SL_SIGMA, "tp_r": TP_R, "bin_pct": BIN_PCT,
                         "n_deneme": N_DENEME},
        "kartil": kartil, "eslesme": eslesme, "kartlar": kartlar,
    }
    sonuc["plasebo"] = plasebo_kontrol(kartlar)
    sonuc["plasebo_bantli"] = plasebo_bantli(tum)

    rapor = rapor_metni(sonuc)
    print("\n" + rapor, flush=True)
    Path(CIKTI).write_text(json.dumps(sonuc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✓ {CIKTI} yazıldı (commit+push → lider+Claude okur)", flush=True)

    if a.telegram:
        try:
            import asyncio                      # ⚠ bildir ASYNC
            from ajan_merkez import bildir
            asyncio.run(bildir("Duvar Kaskad Testi", "backtest",
                               f"Kaskad testi {sonuc['sembol']} — {len(tum)} kesişim olayı",
                               detay=rapor))
            print("[Telegram] gönderildi ✓", flush=True)
        except Exception as e:
            print(f"[Telegram] gönderilemedi: {e}", flush=True)


if __name__ == "__main__":
    main()
