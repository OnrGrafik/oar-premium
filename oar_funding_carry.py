"""
oar_funding_carry.py — FUNDING TAŞIMA HARİTASI + CARRY-NÖTR EŞİĞİ (AŞAMA 1, LOCAL)
═══════════════════════════════════════════════════════════════════════════════════════
KAVRAMSAL TEMEL (kullanıcının Kerem'le konuşmasından — düzeltilmiş hâliyle):
  "Funding GEX" ısı haritası KURULAMAZ. GEX fiyatın fonksiyonudur (spot %1 oynayınca
  dealer'ın hedge etmesi gereken USD). Funding fiyatın fonksiyonu DEĞİL, ZAMANIN
  fonksiyonudur (8 saatte bir ödenen taşıma). Eksenler uyuşmaz.
  → Funding'in doğru haritası: ZAMAN × ŞİDDET (rejim bandı) + taşınan PARA.
  → Perp'te GEX'in gerçek karşılığı funding değil, ZORUNLU AKIŞ'tır (likidasyon
    kümeleri) — o AŞAMA 3, ayrı modül.

TAŞINAN PARA — YAKLAŞIK DEĞİL, TAM:
  Perp'te long notional = short notional = OI notional (yapısal eşitlik). Her long,
  kendi notional'ı üzerinden funding öder. Dolayısıyla bir periyotta taraf değiştiren
  toplam para TAM OLARAK:  transfer_usd = funding_rate × OI_notional
  (+ ise long→short). "Ne kadarı MM'ye ait" bilinemez (kamuya açık veride net MM
  pozisyonu yok) — bu modül o iddiada BULUNMAZ, piyasa toplamını ölçer.

CARRY-NÖTR EŞİĞİ (Kerem'in "carry-nötr" kavramı, sayıya dökülmüş):
  Delta-nötr farm (spot long + perp short) funding TOPLAR ama giriş/çıkış komisyonu
  ve kayması öder. Belirli bir tutma süresi için başa-baş funding oranı:
      esik_rate = (FEE_PCT + SLIP_PCT) / 100 / periyot_sayisi
  Bunun ALTINDAKİ funding "pozitif ama kâr etmez" bölgesidir. Modül tarihin yüzde
  kaçının eşiğin üstünde geçtiğini raporlar.

BACKTEST EDİLEBİLİR SORU (asıl para sorusu):
  Funding EKSTREMİ yön bilgisi taşıyor mu? (aşırı pozitif = long kalabalık → squeeze?)
  WSD ile BİREBİR AYNI işlem modeli ve AYNI serap bateriyle test edilir → sonuçlar
  doğrudan kıyaslanabilir. İşlem motoru `oar_wsd_backtest`ten İMPORT edilir (kopya YOK).

NO-LOOKAHEAD:
  • Karar anı 04:00 UTC. Funding 00:00/08:00/16:00 UTC'de gerçekleşir → 04:00'ta
    bilinen SON oran 00:00'ınkidir. Gelecek periyot ASLA kullanılmaz.
  • Percentile penceresi önceki 30 gün (o günün kendi değeri pencereye girmez).
  • σ_gün önceki 20 gün (WSD modülüyle aynı).

VERİ:
  • Funding geçmişi: borsa REST ucu, 8s periyot, sayfalı. `--indir` ile bir kez
    çekilir → `<veri>/funding/<SEMBOL>-funding.json` (artımlı: sonraki koşu yalnız
    eksik kısmı çeker). stdlib urllib — DIŞ BAĞIMLILIK YOK (hacim_indir.py deseni).
  • OI notional: metrics parquet `sum_open_interest_value` (⚠ 2021+ — öncesi YOK).
  • Fiyat: klines parquet (ileri getiri + σ).

Çalıştırma:
  python oar_funding_carry.py --indir --symbol BTCUSDT,ETHUSDT
  python oar_funding_carry.py --symbol BTCUSDT,ETHUSDT --from 2019-01 --to 2025-06 --telegram
  python oar_funding_carry.py --kendi-test        # ağ/parquet GEREKMEZ
"""
import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from oar_local_backtest import FEE_PCT, SLIP_PCT, GUN_MS, SAAT_MS
from oar_wsd_backtest import (KARAR_SAAT_UTC, PCT_PENCERE, SIGMA_PENCERE, TUT_SAAT,
                              MIN_N, OT_MIN, OT_MAX, _olcek_dogrula, _pct_ekle,
                              _simule, _ts_ms, _utc, degerlendir, _g)

FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
PERIYOT_MS = 8 * SAAT_MS          # funding 8 saatte bir
YIL_PERIYOT = 3 * 365             # yıllıklandırma çarpanı
EKSTREM_UST = 80.0
EKSTREM_ALT = 20.0
N_DENEME = 40
CIKTI = "funding_carry_sonuc.json"


# ═══════════════════════════════════════════════════════════════════════════════
#  FUNDING GEÇMİŞİ — indirici (stdlib, artımlı)
# ═══════════════════════════════════════════════════════════════════════════════
def _funding_yolu(sembol):
    from oar_local_backtest import _hist_dir
    d = _hist_dir() / "funding"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{sembol}-funding.json"


def funding_oku(sembol):
    """Diskteki funding geçmişi: [{"ts": ms, "rate": float}, ...] (kronolojik)."""
    yol = _funding_yolu(sembol)
    if not yol.exists():
        return []
    try:
        return json.loads(yol.read_text(encoding="utf-8"))
    except Exception:
        return []


def funding_indir(sembol, url=FUNDING_URL, sayfa=1000, bekle=0.25):
    """
    Funding geçmişini indirir/tamamlar. ARTIMLI: diskteki son kayıttan devam eder.
    Döner: (toplam_kayit, yeni_kayit). Ağ hatasında elde olanı KORUR (kısmi ilerleme
    diske yazılır) — uzun indirmede Ctrl+C/kopma her şeyi çöpe atmaz.
    """
    mevcut = funding_oku(sembol)
    gorulen = {int(r["ts"]) for r in mevcut}
    basla = (max(gorulen) + 1) if gorulen else 1_500_000_000_000   # ~2017-07
    yeni = 0
    print(f"   [{sembol}] funding indiriliyor (diskte {len(mevcut)} kayıt)…", flush=True)

    while True:
        q = f"{url}?symbol={sembol}&startTime={basla}&limit={sayfa}"
        try:
            with urllib.request.urlopen(q, timeout=30) as r:
                parca = json.load(r)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            print(f"   ⚠ ağ hatası ({str(e)[:60]}) — {yeni} yeni kayıt korundu", flush=True)
            break
        if not parca:
            break
        for kayit in parca:
            ts = int(kayit["fundingTime"])
            if ts in gorulen:
                continue
            gorulen.add(ts)
            mevcut.append({"ts": ts, "rate": float(kayit["fundingRate"])})
            yeni += 1
        basla = int(parca[-1]["fundingTime"]) + 1
        print(f"      · {_utc(parca[-1]['fundingTime']):%Y-%m} · toplam {len(mevcut)}", flush=True)
        if len(parca) < sayfa:
            break
        time.sleep(bekle)

    mevcut.sort(key=lambda r: r["ts"])
    _funding_yolu(sembol).write_text(json.dumps(mevcut), encoding="utf-8")
    print(f"   [{sembol}] ✓ {len(mevcut)} kayıt ({yeni} yeni)", flush=True)
    return len(mevcut), yeni


# ═══════════════════════════════════════════════════════════════════════════════
#  CARRY TABLOSU + REJİM (tanımlayıcı — işlem testi değil)
# ═══════════════════════════════════════════════════════════════════════════════
def carry_notr_esik(tutma_gun=1.0):
    """
    Bir pozisyonu `tutma_gun` gün taşımanın başa-baş funding oranı.
    Giriş+çıkış maliyeti (fee+slip) o sürede toplanan funding'e eşitlenir.
    """
    periyot = max(tutma_gun * 86400_000 / PERIYOT_MS, 1e-9)
    return (FEE_PCT + SLIP_PCT) / 100.0 / periyot


TUTMA_MERDIVEN = [("1 gün", 1.0), ("1 hafta", 7.0), ("1 ay", 30.0), ("1 yıl", 365.0)]


def carry_merdiven(kayitlar):
    """
    TUTMA SÜRESİ MERDİVENİ — "farm kârlı mı" sorusunun asıl cevabı.

    ⚠️ NEDEN GEREKLİ: tek bir carry-nötr eşiği (1 gün) YANILTICI. Giriş+çıkış
    maliyeti pozisyon başına BİR KEZ ödenir; ne kadar uzun tutarsan yıla düşen
    maliyet o kadar azalır. 1 günlük tutmada eşik tipik funding'in ~4 katıdır
    (farm kârsız görünür), 1 aylık tutmada eşiğin ÇOK altına iner (farm kârlı).
    Merdiven olmadan "%5.5 zaman eşik üstünde" satırı "bu iş para kazandırmaz"
    diye okunur — YANLIŞ sonuç.

    net_yillik = brüt carry − (fee+slip) × (365 / tutma_gün)
    """
    import numpy as np
    if not kayitlar:
        return []
    r = np.array([k["rate"] for k in kayitlar], dtype=float)
    brut = float(r.mean()) * YIL_PERIYOT * 100
    out = []
    for ad, gun in TUTMA_MERDIVEN:
        esik = carry_notr_esik(gun)
        maliyet = (FEE_PCT + SLIP_PCT) * (365.0 / gun)
        out.append({
            "tutma": ad,
            "esik_pct": round(esik * 100, 6),
            "esik_ustu_zaman_pct": round(100.0 * float((r > esik).mean()), 1),
            "brut_yillik_pct": round(brut, 2),
            "maliyet_yillik_pct": round(maliyet, 2),
            "net_yillik_pct": round(brut - maliyet, 2),
        })
    return out


def carry_ozet(kayitlar, oi_deger_map=None):
    """
    Funding serisinden tanımlayıcı özet:
      ortalama/medyan oran · yıllıklandırılmış carry% · pozitif periyot oranı
      · carry-nötr eşiğinin üstünde geçen zaman oranı · taşınan toplam para (OI varsa)
    oi_deger_map: {periyot_ts: OI_notional_usd} — yoksa transfer hesaplanmaz.
    """
    import numpy as np
    if not kayitlar:
        return {"n": 0}
    r = np.array([k["rate"] for k in kayitlar], dtype=float)
    esik = carry_notr_esik(1.0)
    out = {
        "n": len(r),
        "ilk": f"{_utc(kayitlar[0]['ts']):%Y-%m-%d}",
        "son": f"{_utc(kayitlar[-1]['ts']):%Y-%m-%d}",
        "ort_oran_pct": round(float(r.mean()) * 100, 5),
        "medyan_oran_pct": round(float(np.median(r)) * 100, 5),
        "yillik_carry_pct": round(float(r.mean()) * YIL_PERIYOT * 100, 2),
        "pozitif_periyot_pct": round(100.0 * float((r > 0).mean()), 1),
        "carry_notr_esik_pct": round(esik * 100, 5),
        "esik_ustu_zaman_pct": round(100.0 * float((r > esik).mean()), 1),
    }
    if oi_deger_map:
        tr = [k["rate"] * oi_deger_map[k["ts"]] for k in kayitlar if k["ts"] in oi_deger_map]
        if tr:
            out["transfer_kapsanan_periyot"] = len(tr)
            out["transfer_toplam_usd"] = round(float(np.sum(tr)))
            out["transfer_ort_periyot_usd"] = round(float(np.mean(tr)))
            out["transfer_gunluk_ort_usd"] = round(float(np.mean(tr)) * 3)
    return out


REJIM_PAY_ESIK = 40.0     # bir ayı etiketlemek için gereken ekstrem-gün payı (%)
TABAN_ORAN = 0.0001       # piyasa konvansiyonu: %0.01/8s "nötr" funding (≈%10.95/yıl)
ASIRI_CARPAN = 2.0        # taban oranın bu katı üstü = aşırı-pozitif gün


def rejim_haritasi(satirlar):
    """
    ZAMAN × ŞİDDET haritası (funding'in GEX-benzeri görünümü — fiyat ekseni YOK).

    ⚠️ İKİ TASARIM TUZAĞI (ikisi de yaşandı, ikisi de burada kapatıldı):
    ① Ay ORTALAMA'sı ile etiketleme: ortalama, ay içindeki ekstrem günleri normal
       günlerle söndürür → hiçbir ay ekstrem görünmez. Çözüm: ayın EKSTREM GÜN PAYI
       (rejim, süre değil YOĞUNLUK sorusudur).
    ② GEZİCİ PERCENTILE ile etiketleme: gezici pencere `değişimi` ölçer, `seviyeyi`
       DEĞİL. Aylarca süren yüksek funding kendi son 30 gününe göre normalleşir →
       kalıcı rejim tam da görünmesi gereken yerde görünmez olur. Çözüm: rejim
       haritası MUTLAK çıpa kullanır (piyasa konvansiyonu %0.01/8s taban oran).
       Gezici percentile yerinde kalır ama ait olduğu yerde: hipotez testi
       varyantlarında (göreli ekstrem, no-lookahead).

    Gün etiketi:  funding ≥ 2×taban → aşırı-pozitif · funding < 0 → negatif/ters
    Ay etiketi:   ilgili gün payı ≥ %40
    `esik_ustu_gun_pct` = funding'in carry-nötr eşiğini aştığı gün payı — farm için
    pratikte anlamlı ölçü ("pozitif" değil, "kâr eder" bölgesi).
    """
    from collections import defaultdict
    esik = carry_notr_esik(1.0)
    asiri_esik = TABAN_ORAN * ASIRI_CARPAN
    aylar = defaultdict(list)
    for s in satirlar:
        aylar[f"{_utc(s['ts']):%Y-%m}"].append(s)

    out = []
    for ay in sorted(aylar):
        oranlar = [g["funding"] for g in aylar[ay] if g.get("funding") is not None]
        if not oranlar:
            continue
        n = len(oranlar)
        pay = lambda x: round(100.0 * x / n, 1)
        asiri_pay = pay(sum(1 for o in oranlar if o >= asiri_esik))
        negatif_pay = pay(sum(1 for o in oranlar if o < 0))

        rejim = "NORMAL"
        if asiri_pay >= REJIM_PAY_ESIK:
            rejim = "AŞIRI-POZİTİF"
        elif negatif_pay >= REJIM_PAY_ESIK:
            rejim = "NEGATİF/TERS"

        ort = sum(oranlar) / n
        out.append({
            "ay": ay, "gun": len(aylar[ay]),
            "ort_oran_pct": round(ort * 100, 5),
            "yillik_carry_pct": round(ort * YIL_PERIYOT * 100, 2),
            "asiri_poz_gun_pct": asiri_pay, "negatif_gun_pct": negatif_pay,
            "esik_ustu_gun_pct": pay(sum(1 for o in oranlar if o > esik)),
            "rejim": rejim,
        })
    return out


# ═══════════════════════════════════════════════════════════════════════════════
#  GÜNLÜK TABLO (no-lookahead) — WSD ile AYNI iskelet
# ═══════════════════════════════════════════════════════════════════════════════
def gun_tablosu(sembol, bas, bit, k_df=None, funding=None, m_df=None):
    """
    Döner: (satirlar, hi, lo, cl, oi_deger_map)
      satirlar[] = {gun, ts, giris, sigma, funding, funding_3g, oi_deger, i0, i1}
    """
    import numpy as np
    import pandas as pd
    from oar_local_backtest import _klines_oku, _metrics_oku, _ms_olcekle

    k = k_df if k_df is not None else _klines_oku(sembol, bas, bit)
    if k is None or not len(k):
        print(f"      ⚠ {sembol}: klines yok", flush=True)
        return [], None, None, None, {}
    kayitlar = funding if funding is not None else funding_oku(sembol)
    if not kayitlar:
        print(f"      ⚠ {sembol}: funding geçmişi yok → önce `--indir` çalıştır", flush=True)
        return [], None, None, None, {}

    k = k.copy()
    k["open_time"] = _ms_olcekle(k["open_time"])
    k = k[(k["open_time"] >= OT_MIN) & (k["open_time"] < OT_MAX)].sort_values("open_time")
    ot = k["open_time"].to_numpy(dtype="int64")
    hi = k["high"].to_numpy(dtype="float64")
    lo = k["low"].to_numpy(dtype="float64")
    cl = k["close"].to_numpy(dtype="float64")
    op = k["open"].to_numpy(dtype="float64")
    _olcek_dogrula(f"{sembol} klines", ot)

    fts = np.array([int(r["ts"]) for r in kayitlar], dtype="int64")
    frt = np.array([float(r["rate"]) for r in kayitlar], dtype="float64")
    sira = np.argsort(fts)
    fts, frt = fts[sira], frt[sira]
    _olcek_dogrula(f"{sembol} funding", fts)

    # OI notional (metrics parquet; ⚠ 2021+). Yoksa transfer hesaplanmaz.
    oi_deger_map = {}
    # m_df=False → metrics'i hiç okuma (self-test / OI istenmeyen koşu)
    m = None if m_df is False else (m_df if m_df is not None else _metrics_oku(sembol, bas, bit))
    if m is not None and len(m) and "sum_open_interest_value" in m.columns:
        mm = m.copy()
        kolon = "create_time" if "create_time" in mm.columns else "ts_ms"
        mm["ts_ms"] = _ts_ms(mm[kolon])
        mm = mm.sort_values("ts_ms")
        mts = mm["ts_ms"].to_numpy(dtype="int64")
        mval = mm["sum_open_interest_value"].astype(float).to_numpy()
        for ts in fts:                                   # her funding anına en yakın ÖNCEKİ OI
            j = int(np.searchsorted(mts, ts, side="right")) - 1
            if j >= 0 and ts - int(mts[j]) <= 30 * 60_000:
                oi_deger_map[int(ts)] = float(mval[j])

    # σ_gün: önceki SIGMA_PENCERE gününün ortalama günlük range%'i (STRICT geçmiş)
    gd = pd.DataFrame({"gun": ot // GUN_MS, "high": hi, "low": lo, "open": op}) \
           .groupby("gun").agg(h=("high", "max"), l=("low", "min"), o=("open", "first"))
    rngs = ((gd["h"] - gd["l"]) / gd["o"] * 100.0).to_numpy(dtype="float64")
    gunler = gd.index.to_numpy()
    sigma_map = {int(gunler[i]): float(rngs[i - SIGMA_PENCERE:i].mean())
                 for i in range(SIGMA_PENCERE, len(gunler))
                 if np.isfinite(rngs[i - SIGMA_PENCERE:i]).all()
                 and rngs[i - SIGMA_PENCERE:i].mean() > 0}

    satirlar, son_ay = [], ""
    red = {"sigma_penceresi": 0, "giris_bari": 0, "ileri_pencere": 0, "funding_yok": 0}
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
        i1 = int(np.searchsorted(ot, karar_ts + TUT_SAAT * SAAT_MS, side="right"))
        if i1 - i0 < 60:
            red["ileri_pencere"] += 1
            continue
        # 04:00'ta BİLİNEN son funding = 00:00 settlement (gelecek periyot ASLA)
        fi = int(np.searchsorted(fts, karar_ts, side="right")) - 1
        if fi < 0 or karar_ts - int(fts[fi]) > 2 * PERIYOT_MS:
            red["funding_yok"] += 1
            continue
        son9 = frt[max(0, fi - 8):fi + 1]                # son ~3 gün (9 periyot)

        satirlar.append({
            "gun": g, "ts": karar_ts, "giris": float(cl[gi]), "sigma": sigma,
            "i0": i0, "i1": i1,
            "funding": float(frt[fi]),
            "funding_3g": float(son9.mean()) if len(son9) else None,
            "oi_deger": oi_deger_map.get(int(fts[fi])),
        })

    if red["funding_yok"] or not satirlar:
        ara = lambda a: (f"{_utc(a[0]):%Y-%m-%d} → {_utc(a[-1]):%Y-%m-%d}") if len(a) else "boş"
        print(f"      · elenen gün: {red} | klines {ara(ot)} | funding {ara(fts)}", flush=True)

    _pct_ekle(satirlar, ["funding", "funding_3g"])
    return satirlar, hi, lo, cl, oi_deger_map


# ═══════════════════════════════════════════════════════════════════════════════
#  VARYANTLAR (hipotez: funding ekstremi yön bilgisi taşıyor mu)
# ═══════════════════════════════════════════════════════════════════════════════
def _varyantlar():
    ust = lambda a: (lambda r: r.get(a + "_pct") is not None and r[a + "_pct"] >= EKSTREM_UST)
    alt = lambda a: (lambda r: r.get(a + "_pct") is not None and r[a + "_pct"] <= EKSTREM_ALT)
    return [
        ("TABAN_LONG",              "LONG",  lambda r: True),
        ("TABAN_SHORT",             "SHORT", lambda r: True),
        # aşırı pozitif funding = long tarafı kalabalık + taşıma pahalı → squeeze hipotezi
        ("FUND_ASIRI_POZ_SHORT",    "SHORT", ust("funding")),
        ("FUND_ASIRI_POZ_LONG",     "LONG",  ust("funding")),      # ters kol (kontrol)
        # negatif/ekstrem düşük funding = short tarafı kalabalık
        ("FUND_NEGATIF_LONG",       "LONG",  alt("funding")),
        ("FUND_NEGATIF_SHORT",      "SHORT", alt("funding")),      # ters kol (kontrol)
        # ham işaret (percentile'sız)
        ("FUND_ISARET_POZ_SHORT",   "SHORT", lambda r: r.get("funding") is not None and r["funding"] > 0),
        ("FUND_ISARET_NEG_LONG",    "LONG",  lambda r: r.get("funding") is not None and r["funding"] < 0),
        # 3 günlük kalıcılık (tek periyot gürültüsünü söndürür)
        ("FUND3G_ASIRI_POZ_SHORT",  "SHORT", ust("funding_3g")),
        ("FUND3G_NEGATIF_LONG",     "LONG",  alt("funding_3g")),
    ]


def seriler_uret(satirlar, hi, lo, cl):
    havuz = {r["gun"]: {"LONG": _simule("LONG", r, hi, lo, cl),
                        "SHORT": _simule("SHORT", r, hi, lo, cl)} for r in satirlar}
    out = {}
    for ad, yon, sec in _varyantlar():
        p, t, tu = [], [], []
        for r in satirlar:
            if not sec(r):
                continue
            net, tur = havuz[r["gun"]][yon]
            p.append(net); t.append(r["ts"]); tu.append(tur)
        out[ad] = (p, t, tu)
    return out


def seriler_birlestir(liste):
    out = {}
    for ad, _, _ in _varyantlar():
        p, t, tu = [], [], []
        for s in liste:
            sp, st, stu = s.get(ad, ([], [], []))
            p += sp; t += st; tu += stu
        sira = sorted(range(len(t)), key=lambda i: t[i])
        out[ad] = ([p[i] for i in sira], [t[i] for i in sira], [tu[i] for i in sira])
    return out


def _yon_map():
    return {ad: yon for ad, yon, _ in _varyantlar()}


# ═══════════════════════════════════════════════════════════════════════════════
#  RAPOR
# ═══════════════════════════════════════════════════════════════════════════════
def _sat(k):
    if "serap" not in k:
        return f"  {k['ad']:<26} {k.get('karar', '—')}"
    s = k["serap"]
    dsr = (s.get("deflated_sharpe") or {}).get("dsr")
    ci = (s.get("bootstrap_beklenti_ci") or {}).get("alt")
    lik = ((s.get("mc_equity") or {}).get("5x") or {}).get("likidasyon_orani")
    return (f"  {k['ad']:<26} n{_g(k.get('n'), 6)} WR%{_g(k.get('wr'), 5)} PF {_g(k.get('pf'), 6)}"
            f" bek {_g(k.get('beklenti'), 8)} OOS {_g(k.get('oos_beklenti'), 8)}"
            f" DD%{_g(k.get('maxdd_1x_pct'), 5)} DSR {_g(dsr, 6)}"
            f" CI-alt {_g(ci, 8)} 5xlik {_g(lik, 6)} {k.get('karar', '')}")


def rapor_metni(sonuc):
    kartlar = sorted(sonuc["varyantlar"].values(),
                     key=lambda k: (-(k.get("beklenti") or -99), k["ad"]))
    gecen = [k["ad"] for k in kartlar if "GERÇEK EDGE" in k.get("karar", "")]
    bas = ("Funding serap-geçer varyant: " + ", ".join(gecen)) if gecen else \
          "Funding ekstremi yön bilgisi TAŞIMIYOR — tüm varyantlar elendi"

    sat = ["═══ FUNDING TAŞIMA HARİTASI + CARRY (AŞAMA 1) ═══", bas,
           f"Aralık {sonuc['aralik']} · {', '.join(sonuc['semboller'])}",
           f"Karar anı {KARAR_SAAT_UTC:.0f}:00 UTC · SL=1.0σ_gün · TP=3R · time-stop {TUT_SAAT}s",
           "", "① CARRY ÖZETİ (tanımlayıcı — taşınan para)"]
    for sym, oz in sonuc["carry"].items():
        if not oz.get("n"):
            sat.append(f"  {sym}: funding verisi yok")
            continue
        sat.append(f"  {sym}  {oz['ilk']}→{oz['son']} · {oz['n']} periyot")
        sat.append(f"     ort oran %{oz['ort_oran_pct']} → yıllık carry %{oz['yillik_carry_pct']}"
                   f" · pozitif periyot %{oz['pozitif_periyot_pct']}")
        sat.append(f"     carry-nötr eşiği %{oz['carry_notr_esik_pct']} (1 gün taşıma)"
                   f" · eşik ÜSTÜ zaman %{oz['esik_ustu_zaman_pct']}")
        merdiven = sonuc.get("merdiven", {}).get(sym) or []
        if merdiven:
            sat.append("     TUTMA SÜRESİ MERDİVENİ (maliyet pozisyon başına BİR KEZ ödenir):")
            for m in merdiven:
                sat.append(f"       {m['tutma']:<8} eşik %{m['esik_pct']:<9}"
                           f" eşik-üstü zaman %{m['esik_ustu_zaman_pct']:<5}"
                           f" → brüt %{m['brut_yillik_pct']} − maliyet %{m['maliyet_yillik_pct']}"
                           f" = NET %{m['net_yillik_pct']}/yıl")
        if "transfer_toplam_usd" in oz:
            sat.append(f"     taşınan para: toplam ${oz['transfer_toplam_usd']:,}"
                       f" · günlük ort ${oz['transfer_gunluk_ort_usd']:,}"
                       f" ({oz['transfer_kapsanan_periyot']} periyot kapsandı)")
        else:
            sat.append("     taşınan para: OI notional yok (metrics 2021 öncesi yok) → hesaplanmadı")

    sat += ["", "② REJİM HARİTASI (zaman × şiddet — funding'in fiyat ekseni YOKTUR)",
            f"   ay etiketi = ekstrem GÜN PAYI ≥ %{REJIM_PAY_ESIK:.0f} (ortalama değil)"]
    for sym, harita in sonuc["rejim"].items():
        asiri = [h for h in harita if h["rejim"] == "AŞIRI-POZİTİF"]
        negatif = [h for h in harita if h["rejim"] == "NEGATİF/TERS"]
        farm = [h for h in harita if (h.get("esik_ustu_gun_pct") or 0) >= 50.0]
        sat.append(f"  {sym}: {len(harita)} ay · AŞIRI-POZİTİF {len(asiri)}"
                   f" · NEGATİF/TERS {len(negatif)}"
                   f" · carry-nötr eşiği üstünde geçen ay {len(farm)}")
        if asiri:
            sat.append("     aşırı-pozitif aylar: "
                       + ", ".join(f"{h['ay']}(%{h['asiri_poz_gun_pct']})" for h in asiri[:10])
                       + (" …" if len(asiri) > 10 else ""))
        if negatif:
            sat.append("     negatif/ters aylar: "
                       + ", ".join(f"{h['ay']}(%{h['negatif_gun_pct']})" for h in negatif[:10])
                       + (" …" if len(negatif) > 10 else ""))

    sat += ["", "③ HİPOTEZ TESTİ (funding ekstremi → yön; WSD ile AYNI model/bateri)"]
    sat += [_sat(k) for k in kartlar]
    sat += ["",
            "YORUM ANAHTARI:",
            "  TABAN_*   = her gün aynı yönde işlem. Varyant TABAN'ı geçemiyorsa funding",
            "              o kurulumda BİLGİ KATMIYOR demektir.",
            "  ters kollar (AŞIRI_POZ_LONG / NEGATİF_SHORT) KONTROL: ikisi de pozitifse",
            "              sinyal funding'den değil, dönemin yön eğiliminden geliyordur.",
            "  ✅ GERÇEK EDGE ancak DSR≥0.95 ∧ CI-alt>0 ∧ p<0.05 ∧ FDR ∧ 5x-likidasyon=0.",
            "",
            "⚠️ ŞAMPİYONLARA DOKUNULMADI (ANAYASA #8). Serap-geçen varyant bile canlıya",
            "   bağlanmadan önce walk-forward + onay ister (§5w London dersi)."]
    return "\n".join(sat)


# ═══════════════════════════════════════════════════════════════════════════════
#  SELF-TEST (ağ + parquet GEREKMEZ)
# ═══════════════════════════════════════════════════════════════════════════════
def kendi_test():
    """
    Sentetik: funding AŞIRI POZİTİF günlerde sonraki 24s AŞAĞI sürüklenir (squeeze
    hipotezi GÖMÜLÜ). Beklenen: FUND_ASIRI_POZ_SHORT pozitif; ters kolu (LONG) negatif.
    Ayrıca no-lookahead ve carry-nötr eşiği aritmetiği doğrulanır.
    """
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(17)
    N_GUN = 420
    bas_ts = 1_600_000_000_000 // GUN_MS * GUN_MS

    # funding: 8 saatlik seri; gün bazında şiddet.
    # 120.-210. günler arası GERÇEK BİR REJİM bloğu (uzun süreli yüksek funding —
    # gerçek boğa dönemlerinin davranışı). Rejim haritası burayı yakalamalı,
    # normal aylarda YANLIŞ ALARM vermemeli.
    gun_rate = rng.normal(0.0001, 0.00012, N_GUN)
    gun_rate[120:210] = rng.normal(0.00055, 0.00010, 90)
    kayitlar = []
    for g in range(N_GUN):
        for p in range(3):
            kayitlar.append({"ts": bas_ts + g * GUN_MS + p * PERIYOT_MS,
                             "rate": float(gun_rate[g] + rng.normal(0, 1e-5))})

    esik_yuksek = np.quantile(gun_rate, 0.8)
    fiyat = 30000.0
    ot, o_, h_, l_, c_ = [], [], [], [], []
    for g in range(N_GUN):
        egim = -0.00003 if gun_rate[g] >= esik_yuksek else 0.0   # squeeze GÖMÜLÜ
        for dk in range(1440):
            mu = egim if dk > 240 else 0.0                        # yalnız karar SONRASI
            ac = fiyat
            fiyat = max(1.0, fiyat + fiyat * (mu + rng.normal(0, 0.0006)))
            ot.append(bas_ts + (g * 1440 + dk) * 60_000)
            o_.append(ac); c_.append(fiyat)
            h_.append(max(ac, fiyat) * (1 + abs(rng.normal(0, 0.0002))))
            l_.append(min(ac, fiyat) * (1 - abs(rng.normal(0, 0.0002))))
    k = pd.DataFrame({"open_time": ot, "open": o_, "high": h_, "low": l_,
                      "close": c_, "volume": np.ones(len(ot))})

    # carry-nötr eşiği aritmetiği: 1 gün = 3 periyot
    esik = carry_notr_esik(1.0)
    beklenen = (FEE_PCT + SLIP_PCT) / 100.0 / 3.0
    assert abs(esik - beklenen) < 1e-12, "carry-nötr eşiği yanlış"
    assert carry_notr_esik(3.0) < carry_notr_esik(1.0), "uzun tutma eşiği düşürmeli"
    print(f"[SELF-TEST] carry-nötr eşiği (1 gün) = %{esik*100:.5f} ✓")

    oz = carry_ozet(kayitlar, {r["ts"]: 5e9 for r in kayitlar})
    assert oz["n"] == len(kayitlar) and "transfer_toplam_usd" in oz, "carry özeti eksik"
    # transfer aritmetiği: rate × OI toplamı
    beklenen_tr = round(float(sum(r["rate"] * 5e9 for r in kayitlar)))
    assert oz["transfer_toplam_usd"] == beklenen_tr, "transfer hesabı yanlış"
    print(f"[SELF-TEST] taşınan para aritmetiği ✓ (yıllık carry %{oz['yillik_carry_pct']})")

    satirlar, hi, lo, cl, _ = gun_tablosu("TESTUSDT", "", "", k_df=k, funding=kayitlar,
                                          m_df=False)
    print(f"[SELF-TEST] {len(satirlar)} karar günü", flush=True)
    assert len(satirlar) > 300, "gün tablosu küçük"
    assert satirlar[0]["funding_pct"] is None, "percentile geçmişe sızıyor"
    assert satirlar[-1]["funding_pct"] is not None, "percentile hiç üretilmedi"
    # no-lookahead: kullanılan funding karar anından ÖNCE gerçekleşmiş olmalı
    fts = {int(r["ts"]) for r in kayitlar}
    for s in satirlar[:50]:
        assert any(t <= s["ts"] and abs(frate - s["funding"]) < 1e-12
                   for t, frate in ((r["ts"], r["rate"]) for r in kayitlar) if t in fts), \
            "funding değeri karar anından sonraki periyottan gelmiş olabilir"

    seriler = seriler_uret(satirlar, hi, lo, cl)
    snc = degerlendir(seriler, yon_map=_yon_map())
    harita = rejim_haritasi(satirlar)
    merd = carry_merdiven(kayitlar)
    assert [m["net_yillik_pct"] for m in merd] == sorted(m["net_yillik_pct"] for m in merd), \
        "uzun tutma net carry'yi ARTIRMALI (maliyet bir kez ödenir)"
    assert merd[0]["net_yillik_pct"] < merd[-1]["net_yillik_pct"], "merdiven düz çıktı"
    print(f"[SELF-TEST] tutma merdiveni: 1 gün net %{merd[0]['net_yillik_pct']}"
          f" → 1 yıl net %{merd[-1]['net_yillik_pct']} ✓")
    print("\n" + rapor_metni({"aralik": "sentetik", "semboller": ["TESTUSDT"],
                              "carry": {"TESTUSDT": oz}, "merdiven": {"TESTUSDT": merd},
                              "rejim": {"TESTUSDT": harita}, "varyantlar": snc}))

    gomulu = snc["FUND_ASIRI_POZ_SHORT"]["beklenti"]
    ters = snc["FUND_ASIRI_POZ_LONG"]["beklenti"]
    print(f"\n[SELF-TEST] gömülü squeeze {gomulu} · ters kol {ters}")
    assert gomulu > 0 > ters, "gömülü squeeze yakalanamadı / ters kol ayrışmadı"

    # rejim haritası: gömülü blok yakalanmalı, normal aylar YANLIŞ ALARM vermemeli
    blok_aylar = {f"{_utc(bas_ts + g * GUN_MS):%Y-%m}" for g in range(125, 205)}
    isaretli = {h["ay"] for h in harita if h["rejim"] == "AŞIRI-POZİTİF"}
    assert isaretli, "rejim haritası gömülü rejimi hiç etiketlemedi"
    assert isaretli <= blok_aylar, f"blok DIŞINDA yanlış alarm: {isaretli - blok_aylar}"
    print(f"[SELF-TEST] rejim haritası: {len(isaretli)} ay AŞIRI-POZİTİF, hepsi gömülü blokta ✓")
    print("[SELF-TEST] ✓ boru hattı doğru (squeeze yakalandı, ters kol ayrıştı, rejim temiz)")
    return True


# ═══════════════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT,ETHUSDT")
    ap.add_argument("--from", dest="bas", default="2019-01")
    ap.add_argument("--to", dest="bit", default="2025-06")
    ap.add_argument("--indir", action="store_true", help="funding geçmişini indir/tamamla")
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--kendi-test", action="store_true")
    args = ap.parse_args()

    if args.kendi_test:
        kendi_test()
        return

    semboller = [s.strip().upper() for s in args.symbol.split(",") if s.strip()]

    if args.indir:
        for sym in semboller:
            funding_indir(sym)
        print("\n✓ indirme bitti. Analiz için --indir olmadan tekrar çalıştır.")
        return

    carry, merdiven, rejim, seri_listesi, per_sembol, ozet = {}, {}, {}, [], {}, []
    for sym in semboller:
        print(f"[Funding] {sym} {args.bas}..{args.bit}…", flush=True)
        satirlar, hi, lo, cl, oi_map = gun_tablosu(sym, args.bas, args.bit)
        if not satirlar:
            continue
        kayitlar = funding_oku(sym)
        carry[sym] = carry_ozet(kayitlar, oi_map)
        merdiven[sym] = carry_merdiven(kayitlar)
        rejim[sym] = rejim_haritasi(satirlar)
        seriler = seriler_uret(satirlar, hi, lo, cl)
        seri_listesi.append(seriler)
        per_sembol[sym] = degerlendir(seriler, yon_map=_yon_map())
        ozet.append(f"{sym} n{len(satirlar)}")
        print(f"      ✓ {sym}: {len(satirlar)} karar günü", flush=True)

    if not per_sembol:
        print("❌ Veri yok — funding indirildi mi (`--indir`)? klines parquet var mı?")
        return

    portfoy = (degerlendir(seriler_birlestir(seri_listesi), yon_map=_yon_map())
               if len(seri_listesi) > 1 else None)

    sonuc = {"tarih": datetime.now(timezone.utc).isoformat(),
             "aralik": f"{args.bas}..{args.bit}",
             "semboller": semboller, "carry": carry, "merdiven": merdiven,
             "rejim": rejim,
             "varyantlar": portfoy or per_sembol[list(per_sembol)[0]],
             "per_sembol": per_sembol}
    rapor = rapor_metni(sonuc)
    print("\n" + rapor)

    Path(CIKTI).write_text(json.dumps(sonuc, ensure_ascii=False, indent=2, default=str),
                           encoding="utf-8")
    print(f"\n💾 {CIKTI} yazıldı (git-senkron)")

    if args.telegram:
        try:
            import asyncio
            from ajan_merkez import bildir
            asyncio.run(bildir("Funding Carry Haritası", "backtest",
                               rapor.split("\n", 2)[1], detay=rapor))
            print("[Telegram] thread 4129'a gönderildi ✓", flush=True)
        except Exception as e:
            print(f"[Telegram] gönderilemedi: {str(e)[:80]}", flush=True)


if __name__ == "__main__":
    main()
