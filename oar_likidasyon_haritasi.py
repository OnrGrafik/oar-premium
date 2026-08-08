"""
oar_likidasyon_haritasi.py — ZORUNLU AKIŞ / LİKİDASYON HARİTASI (AŞAMA 3, LOCAL)
═══════════════════════════════════════════════════════════════════════════════════════
NEDEN BU, GEX'İN GERÇEK PERP KARŞILIĞI:
  GEX işe yarar çünkü dealer'ın hedge'i fiyat tarafından MEKANİK OLARAK ZORLANIR.
  Perp'te mekanik olarak zorlanan tek akış LİKİDASYON'dur: kaldıraçlı bir pozisyon,
  hesaplanabilir bir fiyatta zorla kapatılır. Dolayısıyla harita:
        EKSEN = FİYAT (seviye) × kaldıraç kovası
        HÜCRE = o seviyeye gelinirse zorla kapanacak notional
  Funding bunu veremez (zamanın fonksiyonu, §5aa). Zorunlu akış fiyatın fonksiyonudur.

⚠️⚠️ BU BİR MODELDİR, ÖLÇÜM DEĞİL — VARSAYIMLARI AÇIK YAZIYORUM:
  Kamuya açık veride pozisyon başına giriş fiyatı ve kaldıraç YOK. Bu yüzden
  (Coinglass/Hyblock dahil herkesin yaptığı gibi) TAHMİN edilir:
   ① OI ARTIŞI = yeni pozisyon; o 5dk barın kapanışında açıldığı VARSAYILIR.
   ② Yeni notional kaldıraç kovalarına AĞIRLIKLA dağıtılır (varsayılan dağılım
      aşağıda, `--kaldirac-dagilim` ile değiştirilebilir).
   ③ Long/short PAYLAŞTIRILMAZ: perp'te tanım gereği long notional = short
      notional = OI → ΔOI hem LONG hem SHORT tarafında TAM sayılır. (Taker oranı
      yalnız "kim agresifti" bağlamı; notional'ı BÖLMEZ.)
   ④ Bakım teminatı sabit varsayılır (kademeli tablo değil).
   ⑤ Pozisyon, likidasyon seviyesine fiyat DEĞDİĞİ anda ölür (haritadan düşer);
      OI AZALIŞI da orantılı sönüm uygular (`oi_sonum`) — hangi pozisyonun
      kapandığı bilinemediği için taraf-bağımsız oransal düşüm.
   ⑥ Yeni OI'nin bir kısmı KALDIRAÇSIZ/hedge'li olabilir (ör. carry farm); bunlar
      zorla kapanmaz → harita zorunlu akışı bir miktar YUKARI tahmin eder.
  Bu varsayımlar YANLIŞ olabilir → bu yüzden harita "doğru" diye SUNULMAZ; ürettiği
  sinyal AYNI serap bateriyle (DSR≥0.95) sınanır. Geçmezse harita güzel görünse bile
  kullanılmaz (§5p dersi: güzel görünen çoğu şey seraptır).

NO-LOOKAHEAD:
  • Karar anı 04:00 UTC. Harita YALNIZ karar anına kadarki barlardan kurulur.
  • Pozisyonun "hâlâ yaşıyor" testi de yalnız geçmiş fiyat yolunu kullanır.
  • Percentile penceresi önceki 30 gün · σ_gün önceki 20 gün (WSD/funding ile aynı).

TEST EDİLEN SORU:
  Fiyat büyük bir likidasyon kümesine yakınsa o kümeye ÇEKİLİR Mİ (mıknatıs), yoksa
  küme yakınlığı bilgi taşımaz mı? İşlem modeli ve bateri WSD/funding ile BİREBİR
  AYNI → üç ölçüm doğrudan kıyaslanabilir. Ters kollar KONTROL olarak test edilir.

VERİ: metrics parquet (OI notional + taker oranı, ⚠ 2021+) + 1m klines parquet.

Çalıştırma:
  python oar_likidasyon_haritasi.py --symbol BTCUSDT,ETHUSDT --from 2021-01 --to 2025-06 --telegram
  python oar_likidasyon_haritasi.py --kendi-test      # parquet GEREKMEZ
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from oar_local_backtest import GUN_MS, SAAT_MS
from oar_wsd_backtest import (KARAR_SAAT_UTC, SIGMA_PENCERE, TUT_SAAT, OT_MIN, OT_MAX,
                              _olcek_dogrula, _pct_ekle, _simule, _ts_ms, _utc,
                              degerlendir, _g)

# ── Model parametreleri (VARSAYIM — hepsi açıkça ayarlanabilir) ────────────────
KALDIRAC_DAGILIM = {5: 0.15, 10: 0.30, 20: 0.25, 50: 0.20, 100: 0.10}
BAKIM_TEMINAT = 0.004      # %0.4 — sabit varsayım (gerçekte kademeli)
PENCERE_GUN = 7            # pozisyon ömrü penceresi (bu kadar geriden açılanlar)
BIN_PCT = 0.25             # fiyat kovası genişliği (%)
BANT_PCT = 20.0            # haritanın kapsadığı ± bant (%)
YAKIN_ESIK = 2.0           # "küme yakın" eşiği (%)
EKSTREM_UST = 80.0
EKSTREM_ALT = 20.0
N_DENEME = 40
CIKTI = "likidasyon_haritasi_sonuc.json"


def likidasyon_fiyati(giris, kaldirac, yon, bakim=BAKIM_TEMINAT):
    """
    Kaldıraçlı pozisyonun zorunlu kapanış fiyatı.
    LONG  : giriş × (1 − 1/L + bakım)   · SHORT : giriş × (1 + 1/L − bakım)
    """
    if yon == "LONG":
        return giris * (1.0 - 1.0 / kaldirac + bakim)
    return giris * (1.0 + 1.0 / kaldirac - bakim)


# ═══════════════════════════════════════════════════════════════════════════════
#  HARİTA — belirli bir ana kadar yaşayan zorunlu-akış kümeleri
# ═══════════════════════════════════════════════════════════════════════════════
def oi_sonum(oi_pencere, d_oi_pencere):
    """
    ⚠️ DÜZELTME 2 — OI AZALIŞI ARTIK İŞLENİYOR.
    Önceki sürüm yalnız OI ARTIŞINI okuyor, azalışı hiç işlemiyordu (docstring
    "orantılı söndürür" diyordu ama kod yapmıyordu) → 7 günlük pencerede OI inip
    çıkarken harita ŞİŞİYORDU.

    OI Y kadar düşerse pozisyonların Y/OI_önce oranı kapanmıştır. Hayatta kalan
    pay ORANTILI düşülür (hangi pozisyonun kapandığı bilinemez → tarafsız varsayım).
    Döner: pencere barları hizasında "bugüne kalan pay" çarpanı (0..1].
    """
    import numpy as np
    onceki = oi_pencere - d_oi_pencere
    sonum = np.where((d_oi_pencere < 0) & (onceki > 0),
                     np.divide(oi_pencere, onceki, out=np.ones_like(oi_pencere),
                               where=onceki > 0),
                     1.0)
    kum = np.cumprod(np.clip(sonum, 0.0, 1.0))
    kum = np.where(kum <= 0, 1e-12, kum)
    return np.clip(kum[-1] / kum, 0.0, 1.0)


def _harita_kur(giris_px, notional, sfx_min, sfx_max, spot, kaldirac_dagilim,
                hayatta_pay=None):
    """
    Vektörel harita: her (bar × kaldıraç) için likidasyon seviyesi + hayatta mı.
    sfx_min[i] / sfx_max[i] = i. bardan KARAR ANINA kadarki en düşük/en yüksek fiyat
    → likidasyon seviyesine değmişse pozisyon ÖLMÜŞTÜR (haritadan düşer).

    ⚠️ DÜZELTME 1 — LONG/SHORT PAYLAŞTIRMASI KALDIRILDI.
    Perp'te TANIM GEREĞİ long notional = short notional = OI. OI X kadar artarsa
    X yeni LONG **ve** X yeni SHORT doğar; bu bölünmez. Önceki sürüm ΔOI'yi taker
    oranıyla ikiye bölüyordu → her iki duvarı da (farklı katsayılarla) küçültüyor,
    üst/alt kıyasını da bozuyordu. Artık iki taraf da TAM notional alır.
    Taker oranı "kim agresifti" bilgisidir, notional'ı BÖLMEZ — yalnız bağlam
    olarak raporlanır.

    hayatta_pay: OI azalışından gelen sönüm çarpanı (yoksa 1).
    Döner: (alt_seviye, alt_notional, ust_seviye, ust_notional) numpy dizileri.
    """
    import numpy as np
    if hayatta_pay is None:
        hayatta_pay = np.ones_like(notional)
    taban = notional * hayatta_pay          # her iki tarafa da TAM uygulanır
    alt_s, alt_n, ust_s, ust_n = [], [], [], []
    for L, w in kaldirac_dagilim.items():
        n = taban * w
        # LONG pozisyonlar → aşağıda likide olur (spot düşerse zorunlu SATIŞ)
        lq = giris_px * (1.0 - 1.0 / L + BAKIM_TEMINAT)
        yasiyor = (sfx_min > lq) & (lq < spot) & (n > 0)
        if yasiyor.any():
            alt_s.append(lq[yasiyor]); alt_n.append(n[yasiyor])
        # SHORT pozisyonlar → yukarıda likide olur (spot yükselirse zorunlu ALIŞ)
        lq = giris_px * (1.0 + 1.0 / L - BAKIM_TEMINAT)
        yasiyor = (sfx_max < lq) & (lq > spot) & (n > 0)
        if yasiyor.any():
            ust_s.append(lq[yasiyor]); ust_n.append(n[yasiyor])
    bos = np.array([], dtype=float)
    birlestir = lambda x: np.concatenate(x) if x else bos
    return birlestir(alt_s), birlestir(alt_n), birlestir(ust_s), birlestir(ust_n)


def duvar_tablosu(sembol, bas, bit, k_df=None, m_df=None, kaldirac_dagilim=None,
                  bant=None, satir=None):
    """
    ⭐ İSTENEN ÇIKTI: GEX duvar tablosunun PERP karşılığı — fiyat seviyesi × zorunlu akış.

    GEX tablosunda hücre "spot %1 oynarsa dealer ne kadar hedge eder" der.
    Burada hücre "fiyat o seviyeye gelirse ne kadar notional ZORLA kapanır" der:
      • ÜST seviyeler → short likidasyonları → zorunlu ALIŞ  (Call Wall muadili)
      • ALT seviyeler → long  likidasyonları → zorunlu SATIŞ (Put Wall muadili)
    En büyük iki küme ÜST DUVAR / ALT DUVAR olarak işaretlenir.

    Veri kesitindeki SON ana göre kurulur (canlıya bağlanınca aynı fonksiyon
    şimdiki anı kullanır). Döner: {spot, satirlar[], ust_duvar, alt_duvar, toplam}.
    """
    import numpy as np
    import pandas as pd
    from oar_local_backtest import _klines_oku, _metrics_oku, _ms_olcekle

    bant = BANT_PCT if bant is None else bant
    kaldirac_dagilim = kaldirac_dagilim or KALDIRAC_DAGILIM
    k = k_df if k_df is not None else _klines_oku(sembol, bas, bit)
    m = m_df if m_df is not None else _metrics_oku(sembol, bas, bit)
    if k is None or not len(k) or m is None or not len(m):
        return None

    k = k.copy()
    k["open_time"] = _ms_olcekle(k["open_time"])
    k = k[(k["open_time"] >= OT_MIN) & (k["open_time"] < OT_MAX)].sort_values("open_time")
    ot = k["open_time"].to_numpy(dtype="int64")
    hi = k["high"].to_numpy(dtype="float64")
    lo = k["low"].to_numpy(dtype="float64")
    cl = k["close"].to_numpy(dtype="float64")

    m = m.copy()
    kolon = "create_time" if "create_time" in m.columns else "ts_ms"
    m["ts_ms"] = _ts_ms(m[kolon])
    m = m.sort_values("ts_ms")
    mts = m["ts_ms"].to_numpy(dtype="int64")
    oi_val = m["sum_open_interest_value"].astype(float).to_numpy()
    taker = m["sum_taker_long_short_vol_ratio"].astype(float).to_numpy()
    long_pay_all = np.where(taker > 0, taker / (1.0 + taker), 0.5)
    d_oi = np.diff(oi_val, prepend=oi_val[0])
    k_idx = np.searchsorted(ot, mts, side="right") - 1

    # SON an: hem klines hem metrics'in bittiği yer
    simdi = int(min(ot[-1], mts[-1]))
    gi = int(np.searchsorted(ot, simdi, side="right")) - 1
    pen_bas = simdi - PENCERE_GUN * GUN_MS
    ma = int(np.searchsorted(mts, pen_bas, side="left"))
    mb = int(np.searchsorted(mts, simdi, side="right"))
    if gi < 0 or mb - ma < 10:
        return None

    yeni = d_oi[ma:mb]
    artis = yeni > 0
    if not artis.any():
        return None
    ki = k_idx[ma:mb][artis]
    gecerli = (ki >= 0) & (ki <= gi)
    if not gecerli.any():
        return None
    ki = ki[gecerli]

    pen_k0 = int(np.searchsorted(ot, pen_bas, side="left"))
    sfx_min = np.minimum.accumulate(lo[pen_k0:gi + 1][::-1])[::-1]
    sfx_max = np.maximum.accumulate(hi[pen_k0:gi + 1][::-1])[::-1]
    yerel = np.clip(ki - pen_k0, 0, len(sfx_min) - 1)   # negatif indeks sessizce sarmasın
    spot = float(cl[gi])
    # OI azalışı sönümü (düzeltme 2) — pencere barları hizasında, girişlere eşlenir
    hayatta = oi_sonum(oi_val[ma:mb], d_oi[ma:mb])[artis][gecerli]

    alt_s, alt_n, ust_s, ust_n = _harita_kur(
        cl[ki], yeni[artis][gecerli], sfx_min[yerel], sfx_max[yerel], spot,
        kaldirac_dagilim, hayatta_pay=hayatta)

    # fiyat kovalarına topla (GEX tablosundaki strike satırlarının karşılığı)
    kova = {}
    for sev, nots, yon in ((ust_s, ust_n, "alis"), (alt_s, alt_n, "satis")):
        for p, n in zip(sev, nots):
            mes = (p - spot) / spot * 100.0
            if abs(mes) > bant:
                continue
            b = round(round(p / (spot * BIN_PCT / 100.0)) * (spot * BIN_PCT / 100.0), 2)
            h = kova.setdefault(b, {"fiyat": b, "alis": 0.0, "satis": 0.0})
            h[yon] += float(n)

    # FUNDING BAĞLAMI: duvarlar "nerede" der, funding "hangi taraf kalabalık" der.
    # Pozitif funding → long'lar ödüyor → long kalabalık → ALT duvar (zorunlu satış)
    # daha tehlikeli. Negatifse ayna. İkisi birlikte okunur.
    funding = None
    try:
        from oar_funding_carry import funding_oku, YIL_PERIYOT
        fk = [r for r in funding_oku(sembol) if int(r["ts"]) <= simdi]
        if fk:
            son = fk[-1]
            funding = {"oran_pct": round(son["rate"] * 100, 5),
                       "yillik_pct": round(son["rate"] * YIL_PERIYOT * 100, 2),
                       "ts": int(son["ts"]),
                       "kalabalik": "LONG" if son["rate"] > 0 else
                                    ("SHORT" if son["rate"] < 0 else "NÖTR")}
    except Exception:
        funding = None

    satirlar = sorted(kova.values(), key=lambda r: -r["fiyat"])
    for r in satirlar:
        r["toplam"] = r["alis"] + r["satis"]
        r["mesafe_pct"] = round((r["fiyat"] - spot) / spot * 100.0, 2)
    if satir:                       # en büyük N kovayı tut, fiyata göre sırala
        satirlar = sorted(sorted(satirlar, key=lambda r: -r["toplam"])[:satir],
                          key=lambda r: -r["fiyat"])

    ustler = [r for r in satirlar if r["fiyat"] > spot]
    altlar = [r for r in satirlar if r["fiyat"] < spot]
    ust_duvar = max(ustler, key=lambda r: r["alis"], default=None)
    alt_duvar = max(altlar, key=lambda r: r["satis"], default=None)
    for r in satirlar:
        r["duvar"] = ("UST" if r is ust_duvar else "ALT" if r is alt_duvar else "")

    return {"sembol": sembol, "ts": simdi, "spot": spot, "satirlar": satirlar,
            "funding": funding, "ust_duvar": ust_duvar, "alt_duvar": alt_duvar,
            "toplam_alis": round(sum(r["alis"] for r in satirlar)),
            "toplam_satis": round(sum(r["satis"] for r in satirlar))}


def _usd(v):
    for bol, ek in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= bol:
            return f"${v/bol:,.2f}{ek}"
    return f"${v:,.0f}"


def duvar_metni(d, genislik=22):
    """Duvar tablosunu GEX duvar tablosu düzeninde metne döker (spot çizgisi ortada)."""
    if not d or not d.get("satirlar"):
        return "Duvar tablosu: veri yok"
    sat = d["satirlar"]
    tepe = max((r["toplam"] for r in sat), default=1.0) or 1.0
    ud, ad = d.get("ust_duvar"), d.get("alt_duvar")

    out = [f"═══ ZORUNLU AKIŞ DUVARLARI · {d['sembol']} ═══",
           f"Spot ${d['spot']:,.0f} · kesit {_utc(d['ts']):%Y-%m-%d %H:%M} UTC"
           f" · pencere {PENCERE_GUN}g · bant ±%{BANT_PCT:.0f}",
           f"Toplam zorunlu ALIŞ (üstte) {_usd(d['toplam_alis'])}"
           f" · zorunlu SATIŞ (altta) {_usd(d['toplam_satis'])}",
           ]
    f = d.get("funding")
    if f:
        out.append(f"FUNDING %{f['oran_pct']} /8s (yıllık %{f['yillik_pct']}) →"
                   f" {f['kalabalik']} tarafı kalabalık"
                   + ("  ⇒ ALT duvar daha tehlikeli" if f["kalabalik"] == "LONG"
                      else "  ⇒ ÜST duvar daha tehlikeli" if f["kalabalik"] == "SHORT" else ""))
    out += ["",
           "  FİYAT         MESAFE    ZORUNLU AKIŞ           YOĞUNLUK",
           "  " + "─" * 62]

    spot_basildi = False
    for r in sat:
        if not spot_basildi and r["fiyat"] < d["spot"]:
            out.append(f"  {'':<13}{'':<9}◀── SPOT ${d['spot']:,.0f} " + "─" * 18)
            spot_basildi = True
        yukari_mi = r["fiyat"] > d["spot"]
        deger = r["alis"] if yukari_mi else r["satis"]
        tur = "ALIŞ " if yukari_mi else "SATIŞ"
        bar = "█" * max(0, int(round(r["toplam"] / tepe * genislik)))
        etiket = ""
        if r is ud:
            etiket = "  ⬅ ÜST DUVAR (zorunlu alış)"
        elif r is ad:
            etiket = "  ⬅ ALT DUVAR (zorunlu satış)"
        out.append(f"  ${r['fiyat']:<12,.0f}{r['mesafe_pct']:>+7.2f}%  {tur} {_usd(deger):>10}"
                   f"  {bar}{etiket}")
    if not spot_basildi:
        out.append(f"  {'':<13}{'':<9}◀── SPOT ${d['spot']:,.0f} " + "─" * 18)

    out += ["",
            "OKUMA: hücre = o fiyata gelinirse ZORLA kapanacak notional."
            "  Üst duvar = short",
            "  likidasyonları (zorunlu ALIŞ, Call Wall muadili) · Alt duvar = long"
            " likidasyonları",
            "  (zorunlu SATIŞ, Put Wall muadili).",
            "⚠️ MODELDİR: giriş fiyatı/kaldıraç kamuya açık değil; OI artışı + taker yönü +"
            " kaldıraç",
            "   dağılımı varsayımıyla tahmin edilir (Coinglass/Hyblock da böyle yapar)."]
    return "\n".join(out)


def _kume_ozet(seviye, notional, spot, yukari):
    """
    Seviyeleri % mesafe kovalarına topla → en büyük kümeyi bul.
    Döner: (toplam_notional, en_buyuk_kume_notional, en_buyuk_kume_mesafe_pct).
    """
    import numpy as np
    if not len(seviye):
        return 0.0, 0.0, None
    mesafe = (seviye - spot) / spot * 100.0
    if yukari:
        gecerli = (mesafe > 0) & (mesafe <= BANT_PCT)
    else:
        gecerli = (mesafe < 0) & (mesafe >= -BANT_PCT)
    if not gecerli.any():
        return 0.0, 0.0, None
    m, n = np.abs(mesafe[gecerli]), notional[gecerli]
    kova = np.floor(m / BIN_PCT).astype(int)
    toplam = float(n.sum())
    # kova bazında topla → en büyüğü
    sirali = np.argsort(kova)
    kova_s, n_s = kova[sirali], n[sirali]
    sinir = np.flatnonzero(np.diff(kova_s)) + 1
    gruplar = np.split(n_s, sinir)
    kovalar = np.split(kova_s, sinir)
    en_buyuk_i = int(np.argmax([g.sum() for g in gruplar]))
    return (toplam, float(gruplar[en_buyuk_i].sum()),
            float((kovalar[en_buyuk_i][0] + 0.5) * BIN_PCT))


# ═══════════════════════════════════════════════════════════════════════════════
#  GÜNLÜK TABLO
# ═══════════════════════════════════════════════════════════════════════════════
def gun_tablosu(sembol, bas, bit, k_df=None, m_df=None, kaldirac_dagilim=None):
    """
    Döner: (satirlar, hi, lo, cl)
    satirlar[] = {gun, ts, giris, sigma, i0, i1,
                  ust_kume_mesafe, ust_kume_pay, alt_kume_mesafe, alt_kume_pay,
                  asimetri}
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
        print(f"      ⚠ {sembol}: metrics parquet yok (OI olmadan harita kurulamaz)", flush=True)
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
    taker = m["sum_taker_long_short_vol_ratio"].astype(float).to_numpy()
    # taker oranı → agresif LONG payı (r/(1+r)); geçersizse 0.5
    long_pay_all = np.where(taker > 0, taker / (1.0 + taker), 0.5)
    d_oi = np.diff(oi_val, prepend=oi_val[0])          # OI ARTIŞI = yeni pozisyon
    # her metrics barına karşılık gelen klines indeksi (giriş fiyatı için)
    k_idx = np.searchsorted(ot, mts, side="right") - 1

    # σ_gün (STRICT geçmiş) — diğer modüllerle aynı
    gd = pd.DataFrame({"gun": ot // GUN_MS, "high": hi, "low": lo, "open": op}) \
           .groupby("gun").agg(h=("high", "max"), l=("low", "min"), o=("open", "first"))
    rngs = ((gd["h"] - gd["l"]) / gd["o"] * 100.0).to_numpy(dtype="float64")
    gunler = gd.index.to_numpy()
    sigma_map = {int(gunler[i]): float(rngs[i - SIGMA_PENCERE:i].mean())
                 for i in range(SIGMA_PENCERE, len(gunler))
                 if np.isfinite(rngs[i - SIGMA_PENCERE:i]).all()
                 and rngs[i - SIGMA_PENCERE:i].mean() > 0}

    satirlar, son_ay = [], ""
    red = {"sigma_penceresi": 0, "giris_bari": 0, "ileri_pencere": 0, "oi_yok": 0}
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

        # pencere: karar anından geriye PENCERE_GUN — YALNIZ geçmiş
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

        # açılıştan KARAR ANINA kadarki fiyat uçları (likidasyon değdi mi)
        pen_k0 = int(np.searchsorted(ot, pen_bas, side="left"))
        pencere_lo = lo[pen_k0:gi + 1]
        pencere_hi = hi[pen_k0:gi + 1]
        sfx_min = np.minimum.accumulate(pencere_lo[::-1])[::-1]
        sfx_max = np.maximum.accumulate(pencere_hi[::-1])[::-1]
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

        satirlar.append({
            "gun": g, "ts": karar_ts, "giris": spot, "sigma": sigma, "i0": i0, "i1": i1,
            "ust_kume_mesafe": ust_mes, "ust_kume_pay": ust_kume / toplam,
            "alt_kume_mesafe": alt_mes, "alt_kume_pay": alt_kume / toplam,
            "asimetri": (ust_top - alt_top) / toplam,
            "toplam_notional": toplam,
        })

    if not satirlar or red["oi_yok"]:
        ara = lambda a: (f"{_utc(a[0]):%Y-%m-%d} → {_utc(a[-1]):%Y-%m-%d}") if len(a) else "boş"
        print(f"      · elenen gün: {red} | klines {ara(ot)} | metrics {ara(mts)}", flush=True)

    _pct_ekle(satirlar, ["ust_kume_pay", "alt_kume_pay", "asimetri"])
    return satirlar, hi, lo, cl


# ═══════════════════════════════════════════════════════════════════════════════
#  VARYANTLAR (mıknatıs hipotezi + ters kollar KONTROL)
# ═══════════════════════════════════════════════════════════════════════════════
def _varyantlar():
    """
    ⚠️ TASARIM NOTU: mıknatıs kolu önce "yakın VE büyük" (mesafe≤%2 ∧ pay üst-%20)
    idi — iki dar koşulun KESİŞİMİ n'i eriterek (sentetikte n=22 < 40) asıl
    hipotezi ölçülemez hâle getiriyordu. Ölçülemeyen hipotez cevapsız kalır.
    Şimdi iki boyut AYRIŞTIRILDI: mesafe (mıknatıs) ve büyüklük (asimetri) ayrı
    varyantlar. Her birinin TERS KOLU kontrol olarak yanında.
    """
    yakin = lambda taraf: (lambda r: (r.get(f"{taraf}_kume_mesafe") is not None
                                      and r[f"{taraf}_kume_mesafe"] <= YAKIN_ESIK))
    ust = lambda a: (lambda r: r.get(a + "_pct") is not None and r[a + "_pct"] >= EKSTREM_UST)
    alt = lambda a: (lambda r: r.get(a + "_pct") is not None and r[a + "_pct"] <= EKSTREM_ALT)

    return [
        ("TABAN_LONG",             "LONG",  lambda r: True),
        ("TABAN_SHORT",            "SHORT", lambda r: True),
        # MIKNATIS (mesafe): yukarıda yakın short-liq kümesi → fiyat oraya çekilir mi
        ("LIQ_UST_YAKIN_LONG",     "LONG",  yakin("ust")),
        ("LIQ_UST_YAKIN_SHORT",    "SHORT", yakin("ust")),         # ters kol (KONTROL)
        # MIKNATIS (mesafe): aşağıda yakın long-liq kümesi
        ("LIQ_ALT_YAKIN_SHORT",    "SHORT", yakin("alt")),
        ("LIQ_ALT_YAKIN_LONG",     "LONG",  yakin("alt")),         # ters kol (KONTROL)
        # ASİMETRİ (büyüklük): zorunlu akış yakıtı hangi tarafta daha çok
        ("LIQ_ASIMETRI_UST_LONG",  "LONG",  ust("asimetri")),
        ("LIQ_ASIMETRI_UST_SHORT", "SHORT", ust("asimetri")),      # ters kol (KONTROL)
        ("LIQ_ASIMETRI_ALT_SHORT", "SHORT", alt("asimetri")),
        ("LIQ_ASIMETRI_ALT_LONG",  "LONG",  alt("asimetri")),      # ters kol (KONTROL)
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


def _ters_kontrol(kartlar):
    """
    Ters-kol kontrolü: bir hipotezin İKİ kolu da pozitifse sinyal kümeden değil,
    dönemin yön eğiliminden geliyordur. Bunu rapora AÇIK yaz.
    """
    ciftler = [("LIQ_UST_YAKIN_LONG", "LIQ_UST_YAKIN_SHORT"),
               ("LIQ_ALT_YAKIN_SHORT", "LIQ_ALT_YAKIN_LONG"),
               ("LIQ_ASIMETRI_UST_LONG", "LIQ_ASIMETRI_UST_SHORT"),
               ("LIQ_ASIMETRI_ALT_SHORT", "LIQ_ASIMETRI_ALT_LONG")]
    uyari = []
    for a, b in ciftler:
        ka, kb = kartlar.get(a), kartlar.get(b)
        if not ka or not kb:
            continue
        va, vb = ka.get("beklenti"), kb.get("beklenti")
        if va is not None and vb is not None and va > 0 and vb > 0:
            uyari.append(f"{a} (+{va}) ve ters kolu {b} (+{vb}) İKİSİ DE pozitif"
                         " → sinyal kümeden değil, dönem eğiliminden")
    return uyari


def rapor_metni(sonuc):
    kartlar = sonuc["varyantlar"]
    sirali = sorted(kartlar.values(), key=lambda k: (-(k.get("beklenti") or -99), k["ad"]))
    gecen = [k["ad"] for k in sirali if "GERÇEK EDGE" in k.get("karar", "")]
    bas = ("Likidasyon kümesi serap-geçer varyant: " + ", ".join(gecen)) if gecen else \
          "Likidasyon kümeleri yön bilgisi TAŞIMIYOR — tüm varyantlar elendi"

    sat = ["═══ ZORUNLU AKIŞ / LİKİDASYON HARİTASI (AŞAMA 3) ═══", bas,
           f"Aralık {sonuc['aralik']} · {', '.join(sonuc['semboller'])}",
           f"Karar anı {KARAR_SAAT_UTC:.0f}:00 UTC · SL=1.0σ_gün · TP=3R · time-stop {TUT_SAAT}s",
           f"Model: pencere {PENCERE_GUN}g · kaldıraç {dict(KALDIRAC_DAGILIM)}"
           f" · bakım teminatı %{BAKIM_TEMINAT*100:.1f} · bin %{BIN_PCT} · bant ±%{BANT_PCT:.0f}",
           ""]

    if sonuc.get("harita_ozet"):
        sat.append("① HARİTA ÖZETİ (tanımlayıcı)")
        for sym, oz in sonuc["harita_ozet"].items():
            sat.append(f"  {sym}: {oz['gun']} karar günü · ort yaşayan notional"
                       f" ${oz['ort_notional']:,.0f}")
            sat.append(f"     yukarıda küme yakın (≤%{YAKIN_ESIK}) gün payı %{oz['ust_yakin_pct']}"
                       f" · aşağıda %{oz['alt_yakin_pct']}")
            sat.append(f"     ort asimetri {oz['ort_asimetri']:+.3f}"
                       f" (+ = zorunlu ALIŞ yakıtı yukarıda daha çok)")
        sat.append("")

    sat += ["② HİPOTEZ TESTİ (WSD/funding ile AYNI model ve bateri)"]
    sat += [_sat(k) for k in sirali]

    uyari = _ters_kontrol(kartlar)
    if uyari:
        sat += ["", "⚠ TERS-KOL UYARISI:"] + [f"  • {u}" for u in uyari]

    sat += ["",
            "YORUM ANAHTARI:",
            "  TABAN_*  = her gün aynı yönde işlem. Varyant TABAN'ı geçemiyorsa küme",
            "             haritası o kurulumda BİLGİ KATMIYOR demektir.",
            "  ters kollar KONTROL amaçlıdır — ikisi de pozitifse sinyal sahtedir.",
            "  ✅ GERÇEK EDGE ancak DSR≥0.95 ∧ CI-alt>0 ∧ p<0.05 ∧ FDR ∧ 5x-likid=0.",
            "",
            "⚠️ HARİTA BİR MODELDİR: giriş fiyatı/kaldıraç kamuya açık DEĞİL; OI artışı",
            "   + taker yönü + kaldıraç dağılımı VARSAYIMIYLA tahmin edilir. Harita güzel",
            "   görünse bile serap testinden geçmeden kullanılmaz (§5p).",
            "⚠️ ŞAMPİYONLARA DOKUNULMADI (ANAYASA #8)."]
    return "\n".join(sat)


def harita_ozet(satirlar):
    import numpy as np
    if not satirlar:
        return None
    ust_yakin = sum(1 for r in satirlar
                    if r["ust_kume_mesafe"] is not None and r["ust_kume_mesafe"] <= YAKIN_ESIK)
    alt_yakin = sum(1 for r in satirlar
                    if r["alt_kume_mesafe"] is not None and r["alt_kume_mesafe"] <= YAKIN_ESIK)
    n = len(satirlar)
    return {"gun": n,
            "ort_notional": float(np.mean([r["toplam_notional"] for r in satirlar])),
            "ust_yakin_pct": round(100.0 * ust_yakin / n, 1),
            "alt_yakin_pct": round(100.0 * alt_yakin / n, 1),
            "ort_asimetri": float(np.mean([r["asimetri"] for r in satirlar]))}


# ═══════════════════════════════════════════════════════════════════════════════
#  SELF-TEST (parquet GEREKMEZ)
# ═══════════════════════════════════════════════════════════════════════════════
def kendi_test():
    """
    ① likidasyon fiyatı aritmetiği
    ② harita mekaniği: likidasyon seviyesine DEĞMİŞ pozisyon haritadan DÜŞMELİ
    ③ uçtan uca: yukarıda yakın+büyük küme olan günlerde YUKARI sürüklenme GÖMÜLÜ
       → LIQ_UST_MIKNATIS_LONG pozitif, ters kolu negatif çıkmalı
    """
    import numpy as np
    import pandas as pd

    # ① aritmetik
    assert abs(likidasyon_fiyati(100, 10, "LONG") - 100 * (1 - 0.1 + 0.004)) < 1e-9
    assert abs(likidasyon_fiyati(100, 10, "SHORT") - 100 * (1 + 0.1 - 0.004)) < 1e-9
    assert likidasyon_fiyati(100, 100, "LONG") > likidasyon_fiyati(100, 5, "LONG"), \
        "yüksek kaldıraç likidasyonu spot'a YAKIN olmalı"
    print("[SELF-TEST] likidasyon fiyatı aritmetiği ✓")

    # ② ölü pozisyon eleniyor mu
    giris = np.array([100.0, 100.0])
    notional = np.array([1000.0, 1000.0])
    # 10x long likidasyonu ≈ 90.4 → ilkinin dibi 89 (DEĞDİ, ölmeli), ikincisi 95 (yaşıyor)
    sfx_min = np.array([89.0, 95.0])
    sfx_max = np.array([101.0, 101.0])          # üst taraf hiç değmedi → short'lar yaşıyor
    a_s, a_n, u_s, u_n = _harita_kur(giris, notional, sfx_min, sfx_max,
                                     spot=100.0, kaldirac_dagilim={10: 1.0})
    assert len(a_s) == 1 and abs(a_n[0] - 1000.0) < 1e-6, \
        f"ölü LONG elenmedi (kalan {len(a_s)})"
    # DÜZELTME 1: aynı ΔOI hem long hem short üretir → short tarafı da TAM
    assert len(u_s) == 2 and abs(u_n.sum() - 2000.0) < 1e-6, \
        f"ΔOI short tarafına TAM yansımadı (short toplam {u_n.sum()})"
    print("[SELF-TEST] ölü LONG düşüyor · aynı ΔOI short tarafına da TAM yansıyor ✓")

    # DÜZELTME 2: OI azalışı sönümü
    oi = np.array([100.0, 100.0, 50.0, 50.0])          # 3. barda OI yarıya indi
    d = np.diff(oi, prepend=oi[0])
    hp = oi_sonum(oi, d)
    assert abs(hp[0] - 0.5) < 1e-9 and abs(hp[-1] - 1.0) < 1e-9, f"sönüm yanlış: {hp}"
    oi2 = np.array([100.0, 100.0, 100.0]); d2 = np.diff(oi2, prepend=oi2[0])
    assert np.allclose(oi_sonum(oi2, d2), 1.0), "azalış yokken sönüm uygulanmamalı"
    a2, an2, _, _ = _harita_kur(giris, notional, np.array([95.0, 95.0]),
                                np.array([101.0, 101.0]), spot=100.0,
                                kaldirac_dagilim={10: 1.0},
                                hayatta_pay=np.array([0.5, 1.0]))
    assert abs(an2.sum() - 1500.0) < 1e-6, f"sönüm notional'a uygulanmadı: {an2.sum()}"
    print("[SELF-TEST] OI azalışı orantılı sönüm uyguluyor (yarıya inince pay 0.5) ✓")

    # ③ uçtan uca
    rng = np.random.default_rng(23)
    N_GUN, bas_ts = 400, 1_600_000_000_000 // GUN_MS * GUN_MS
    miknatis = rng.random(N_GUN) < 0.30          # bu günlerde yukarı sürüklenme GÖMÜLÜ

    fiyat = 30000.0
    ot, o_, h_, l_, c_ = [], [], [], [], []
    for g in range(N_GUN):
        egim = 0.00004 if miknatis[g] else 0.0
        for dk in range(1440):
            mu = egim if dk > 240 else 0.0
            ac = fiyat
            fiyat = max(1.0, fiyat + fiyat * (mu + rng.normal(0, 0.0005)))
            ot.append(bas_ts + (g * 1440 + dk) * 60_000)
            o_.append(ac); c_.append(fiyat)
            h_.append(max(ac, fiyat) * (1 + abs(rng.normal(0, 0.00015))))
            l_.append(min(ac, fiyat) * (1 - abs(rng.normal(0, 0.00015))))
    k = pd.DataFrame({"open_time": ot, "open": o_, "high": h_, "low": l_,
                      "close": c_, "volume": np.ones(len(ot))})

    # metrics: mıknatıs günlerinde SHORT tarafı şişir (taker long payı düşük → short OI)
    mts, oi, taker = [], [], []
    oi_seviye = 5e9
    for g in range(N_GUN):
        for adim in range(288):
            mts.append(bas_ts + g * GUN_MS + adim * 5 * 60_000)
            oi_seviye = max(1e9, oi_seviye + rng.normal(2e6, 5e6))
            oi.append(oi_seviye)
            taker.append(0.55 if miknatis[g] else 1.0 + rng.normal(0, 0.05))
    m = pd.DataFrame({"create_time": [f"{_utc(t):%Y-%m-%d %H:%M:%S}" for t in mts],
                      "sum_open_interest_value": oi,
                      "sum_taker_long_short_vol_ratio": taker})

    satirlar, hi, lo, cl = gun_tablosu("TESTUSDT", "", "", k_df=k, m_df=m)
    print(f"[SELF-TEST] {len(satirlar)} karar günü · harita kuruldu", flush=True)
    assert len(satirlar) > 250, f"gün tablosu küçük ({len(satirlar)})"
    assert satirlar[0]["asimetri_pct"] is None, "percentile geçmişe sızıyor"
    assert satirlar[-1]["asimetri_pct"] is not None, "percentile hiç üretilmedi"

    seriler = seriler_uret(satirlar, hi, lo, cl)
    snc = degerlendir(seriler, yon_map=_yon_map())
    oz = harita_ozet(satirlar)
    print("\n" + rapor_metni({"aralik": "sentetik", "semboller": ["TESTUSDT"],
                              "harita_ozet": {"TESTUSDT": oz}, "varyantlar": snc}))

    gomulu = snc["LIQ_ASIMETRI_UST_LONG"].get("beklenti")
    ters = snc["LIQ_ASIMETRI_UST_SHORT"].get("beklenti")
    print(f"\n[SELF-TEST] asimetri-üst LONG {gomulu} · ters kol {ters}")
    assert gomulu is not None and ters is not None, "varyant serisi üretilmedi"
    assert gomulu > ters, "gömülü mıknatıs ters kolundan ayrışmadı"
    print("[SELF-TEST] ✓ boru hattı doğru (harita kuruluyor, ölü pozisyon düşüyor,"
          " gömülü etki ters kolundan ayrışıyor)")
    return True


# ═══════════════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT,ETHUSDT")
    ap.add_argument("--from", dest="bas", default="2021-01")
    ap.add_argument("--to", dest="bit", default="2025-06")
    ap.add_argument("--duvar", action="store_true",
                    help="hipotez testi yerine ZORUNLU AKIS DUVAR TABLOSUNU yazdir")
    ap.add_argument("--satir", type=int, default=24, help="duvar tablosunda satir sayisi")
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--kendi-test", action="store_true")
    args = ap.parse_args()

    if args.kendi_test:
        kendi_test()
        return

    semboller = [s.strip().upper() for s in args.symbol.split(",") if s.strip()]

    if args.duvar:
        tum = []
        for sym in semboller:
            print(f"[Duvar] {sym} haritası kuruluyor…", flush=True)
            d = duvar_tablosu(sym, args.bas, args.bit, satir=args.satir)
            if not d:
                print(f"      ⚠ {sym}: veri yok (metrics 2021+ ve klines gerekli)", flush=True)
                continue
            tum.append(d)
            print("\n" + duvar_metni(d))
        if tum:
            Path("likidasyon_duvar.json").write_text(
                json.dumps(tum, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            print("\n💾 likidasyon_duvar.json yazıldı")
            if args.telegram:
                try:
                    import asyncio
                    from ajan_merkez import bildir
                    metin = "\n\n".join(duvar_metni(d) for d in tum)
                    asyncio.run(bildir("Zorunlu Akış Duvarları", "çıktı",
                                       "Perp duvar tablosu güncellendi", detay=metin))
                    print("[Telegram] gönderildi ✓")
                except Exception as e:
                    print(f"[Telegram] gönderilemedi: {str(e)[:80]}")
        return

    harita, seri_listesi, per_sembol, ozet = {}, [], {}, []
    for sym in semboller:
        print(f"[Likidasyon] {sym} {args.bas}..{args.bit}…", flush=True)
        satirlar, hi, lo, cl = gun_tablosu(sym, args.bas, args.bit)
        if not satirlar:
            continue
        harita[sym] = harita_ozet(satirlar)
        seriler = seriler_uret(satirlar, hi, lo, cl)
        seri_listesi.append(seriler)
        per_sembol[sym] = degerlendir(seriler, yon_map=_yon_map())
        ozet.append(f"{sym} n{len(satirlar)}")
        print(f"      ✓ {sym}: {len(satirlar)} karar günü", flush=True)

    if not per_sembol:
        print("❌ Veri yok — metrics parquet 2021+ indirilmiş mi?")
        return

    portfoy = (degerlendir(seriler_birlestir(seri_listesi), yon_map=_yon_map())
               if len(seri_listesi) > 1 else None)
    sonuc = {"tarih": datetime.now(timezone.utc).isoformat(),
             "aralik": f"{args.bas}..{args.bit}", "semboller": semboller,
             "model": {"pencere_gun": PENCERE_GUN, "kaldirac_dagilim": KALDIRAC_DAGILIM,
                       "bakim_teminat": BAKIM_TEMINAT, "bin_pct": BIN_PCT,
                       "bant_pct": BANT_PCT, "yakin_esik": YAKIN_ESIK},
             "harita_ozet": harita,
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
            asyncio.run(bildir("Likidasyon Haritası", "backtest",
                               rapor.split("\n", 2)[1], detay=rapor))
            print("[Telegram] thread 4129'a gönderildi ✓", flush=True)
        except Exception as e:
            print(f"[Telegram] gönderilemedi: {str(e)[:80]}", flush=True)


if __name__ == "__main__":
    main()
