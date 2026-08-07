"""
oar_risk_finansman.py — RİSK FİNANSMAN SKORU (AŞAMA 4, LOCAL)
═══════════════════════════════════════════════════════════════════════════════════════
KERİM'İN ASIL SORUSU, SAYIYA DÖKÜLMÜŞ:
  "Fiyat nereye gider" değil → **MEVCUT RİSK FİNANSE EDİLEBİLİR Mİ?**
  Yani: şok geldiğinde pozisyonun ürettiği carry + elimizdeki likit rezerv, doğacak
  maliyeti karşılamaya yeter mi? Pozisyonu ZORUNLU kapatmadan taşıyabilir miyiz?

⚠️ BU BİR İŞLEM SİNYALİ DEĞİL, ALARM KATMANIDIR. Yön üretmez, "taşınabilir mi"
   sorusunu cevaplar. (Aşama 1-3'te yön arandı ve perp tarafında YOK çıktı:
   WSD ❌ · funding ekstremi ❌ · likidasyon kümesi ❌. Kalan değer taşımada.)

MODELLENEN POZİSYON — delta-nötr carry farm:
  LONG spot + SHORT perp (eşit notional). Funding pozitifken SHORT taraf TAHSİL eder.

  ⚠️ ASIL OPERASYONEL RİSK (çoğu hesabın kaçırdığı): fiyat YÜKSELİNCE toplam PnL
  ~sıfırdır (spot kazanır, perp kaybeder) AMA perp bacağının teminatı AYRI cüzdandadır.
  Transfer yapılmazsa toplam kâr-zarar düz olsa bile HEDGE BACAĞI LİKİDE OLUR ve
  çıplak spot long'a dönersin. Kerem'in "0.001 delta oynama ile poz dengeleyemezsen
  1k gider" dediği yer burası. Bu yüzden rezerv AYRI modellenir.

  sermaye C · pozisyon notional P = C × kaldıraç · teminat M = C × (1 − rezerv_payı)
  likit rezerv R = C × rezerv_payı · perp bacak kaldıracı L = P / M
  short likidasyon yukarı-hareketi:  r_liq = M/P − bakım   (rezervle: (M+R)/P − bakım)

ÜÇ TEST (Kerem'in tanımladığı bateri):
  ① NORMAL   : seçilen tutma süresinde net carry > 0 mı? (giriş/çıkış maliyeti
               pozisyon başına BİR KEZ ödenir → tutma süresi belirleyici, §5aa merdiveni)
  ② STRES    : funding TERSİNE dönerse (tarihsel EN KÖTÜ yuvarlanan pencere) günlük
               kanama ne kadar, rezerv kaç gün dayanır?
  ③ KUYRUK   : tarihsel EN KÖTÜ yukarı hareket geldiğinde hedge bacağı likide olur mu?
               Rezerv transferiyle kurtulur mu?

VERİ: funding geçmişi (oar_funding_carry ile indirilen JSON) + 1m klines parquet.
      Uydurma senaryo YOK — stres ve kuyruk GERÇEKLEŞMİŞ tarihten alınır.

DÜRÜST SINIRLAR (modelde YOK):
  • spot bacağın borçlanma/stablecoin getirisi · borsa iflas/çekim riski
  • kademeli bakım teminatı (sabit varsayılır) · funding tahsil edilemeyen kesintiler
  • likidite/slippage stres anında derinleşir — sabit alınır
  Bu yüzden çıkan "dayanır" hükmü GEREK ŞART'tır, yeter şart değil.

Çalıştırma:
  python oar_risk_finansman.py --symbol BTCUSDT,ETHUSDT --sermaye 10000 --kaldirac 3 \
      --rezerv-pay 0.30 --tutma-gun 30 --telegram
  python oar_risk_finansman.py --kendi-test        # ağ/parquet GEREKMEZ
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from oar_local_backtest import FEE_PCT, SLIP_PCT, GUN_MS
from oar_funding_carry import PERIYOT_MS, YIL_PERIYOT, carry_notr_esik, funding_oku
from oar_wsd_backtest import OT_MIN, OT_MAX, _olcek_dogrula, _utc

BAKIM_TEMINAT = 0.005     # %0.5 — sabit varsayım (gerçekte kademeli, büyük pozisyonda artar)
CIKTI = "risk_finansman_sonuc.json"


# ═══════════════════════════════════════════════════════════════════════════════
#  TARİHSEL STRES GİRDİLERİ (uydurma yok — gerçekleşmiş en kötüler)
# ═══════════════════════════════════════════════════════════════════════════════
def en_kotu_yukari_hareket(sembol, bas, bit, tutma_gun, k_df=None):
    """
    Her giriş anından itibaren `tutma_gun` içinde görülen EN YÜKSEK yukarı hareket
    (short bacak için en kötü senaryo). Döner: {p50, p95, p99, max} oran olarak.

    Günlük high serisi üzerinde yuvarlanan pencere maksimumu kullanılır: girişte
    kapanış, sonraki pencerede görülen en yüksek HIGH → gerçek intraday tepe
    (kapanıştan kapanışa bakmak likidasyonu KAÇIRIR — likidasyon fitile basar).
    """
    import numpy as np
    import pandas as pd
    from oar_local_backtest import _klines_oku, _ms_olcekle

    k = k_df if k_df is not None else _klines_oku(sembol, bas, bit)
    if k is None or not len(k):
        return None
    k = k.copy()
    k["open_time"] = _ms_olcekle(k["open_time"])
    k = k[(k["open_time"] >= OT_MIN) & (k["open_time"] < OT_MAX)].sort_values("open_time")
    ot = k["open_time"].to_numpy(dtype="int64")
    _olcek_dogrula(f"{sembol} klines", ot)

    gd = pd.DataFrame({"gun": ot // GUN_MS,
                       "high": k["high"].to_numpy(dtype="float64"),
                       "close": k["close"].to_numpy(dtype="float64")}) \
        .groupby("gun").agg(h=("high", "max"), c=("close", "last"))
    hi = gd["h"].to_numpy(dtype="float64")
    cl = gd["c"].to_numpy(dtype="float64")
    n = len(cl)
    w = max(1, int(tutma_gun))
    if n <= w + 1:
        return None

    # girişten sonraki w gün içindeki en yüksek high (yuvarlanan pencere maksimumu)
    hareket = np.empty(n - w, dtype=float)
    for i in range(n - w):
        tepe = hi[i + 1:i + 1 + w].max()
        hareket[i] = (tepe - cl[i]) / cl[i]
    hareket = hareket[np.isfinite(hareket)]
    if not len(hareket):
        return None
    return {"n": int(len(hareket)),
            "p50": float(np.percentile(hareket, 50)),
            "p95": float(np.percentile(hareket, 95)),
            "p99": float(np.percentile(hareket, 99)),
            "max": float(hareket.max())}


def en_kotu_funding_penceresi(kayitlar, tutma_gun):
    """
    Tarihsel EN KÖTÜ (en negatif) yuvarlanan funding penceresi ortalaması.
    Carry farm için stres = funding'in tersine dönüp KANAMAYA başlaması.
    Döner: {ort_oran, baslangic, bitis, periyot} — yoksa None.
    """
    import numpy as np
    if not kayitlar:
        return None
    kayitlar = sorted(kayitlar, key=lambda r: r["ts"])
    r = np.array([k["rate"] for k in kayitlar], dtype=float)
    w = max(1, int(tutma_gun * 86400_000 / PERIYOT_MS))
    if len(r) <= w:
        w = max(1, len(r) // 4)
    if len(r) <= w:
        return None
    kum = np.concatenate([[0.0], np.cumsum(r)])
    ort = (kum[w:] - kum[:-w]) / w
    i = int(np.argmin(ort))
    return {"ort_oran": float(ort[i]), "periyot": int(w),
            "baslangic": f"{_utc(kayitlar[i]['ts']):%Y-%m-%d}",
            "bitis": f"{_utc(kayitlar[min(i + w, len(kayitlar) - 1)]['ts']):%Y-%m-%d}"}


# ═══════════════════════════════════════════════════════════════════════════════
#  ÜÇ TEST
# ═══════════════════════════════════════════════════════════════════════════════
def risk_skoru(sermaye, kaldirac, rezerv_pay, tutma_gun,
               ort_funding, kotu_funding, yukari):
    """
    Kerem'in üç testini sayıya döker. Saf fonksiyon → ağsız test edilebilir.
    ort_funding / kotu_funding: periyot başına ONDALIK oran (ör. 0.0001 = %0.01).
    yukari: en_kotu_yukari_hareket çıktısı (oran).
    """
    C = float(sermaye)
    P = C * float(kaldirac)                      # pozisyon notional
    R = C * float(rezerv_pay)                    # likit rezerv (transfer edilebilir)
    M = C - R                                    # perp bacak teminatı
    L = (P / M) if M > 0 else float("inf")       # perp bacak efektif kaldıracı

    # ── ① NORMAL: seçilen tutmada net carry ────────────────────────────────
    brut_yillik = ort_funding * YIL_PERIYOT * 100
    maliyet_yillik = (FEE_PCT + SLIP_PCT) * (365.0 / max(tutma_gun, 1e-9))
    net_yillik_pct = brut_yillik - maliyet_yillik
    net_yillik_usd = P * net_yillik_pct / 100.0
    gunluk_carry_usd = P * ort_funding * 3.0
    normal_gecti = net_yillik_pct > 0

    # ── ② STRES: funding tersine dönerse kanama ve dayanma süresi ──────────
    stres = {"veri_yok": True}
    if kotu_funding:
        kanama_gun = P * kotu_funding["ort_oran"] * 3.0        # negatifse gider
        gunluk_kayip = -kanama_gun if kanama_gun < 0 else 0.0
        stres = {
            "veri_yok": False,
            "en_kotu_ort_oran_pct": round(kotu_funding["ort_oran"] * 100, 5),
            "donem": f"{kotu_funding['baslangic']}→{kotu_funding['bitis']}",
            "gunluk_kayip_usd": round(gunluk_kayip, 2),
            "rezerv_dayanma_gun": (round(R / gunluk_kayip, 1) if gunluk_kayip > 0 else None),
            "gecti": gunluk_kayip <= 0 or (R / gunluk_kayip) >= tutma_gun,
        }

    # ── ③ KUYRUK: en kötü yukarı hareket hedge bacağını likide eder mi ─────
    r_liq = (M / P) - BAKIM_TEMINAT if P > 0 else 0.0          # rezerv transfer YOK
    r_liq_rezervli = ((M + R) / P) - BAKIM_TEMINAT if P > 0 else 0.0
    kuyruk = {"veri_yok": True}
    if yukari:
        kuyruk = {
            "veri_yok": False,
            "ornek": yukari["n"],
            "p95_pct": round(yukari["p95"] * 100, 2),
            "p99_pct": round(yukari["p99"] * 100, 2),
            "max_pct": round(yukari["max"] * 100, 2),
            "likidasyon_esigi_pct": round(r_liq * 100, 2),
            "likidasyon_esigi_rezervli_pct": round(r_liq_rezervli * 100, 2),
            "rezervsiz_dayanir": r_liq > yukari["max"],
            "rezervli_dayanir": r_liq_rezervli > yukari["max"],
            "p99_dayanir": r_liq_rezervli > yukari["p99"],
        }

    finanse_edilebilir = bool(normal_gecti
                              and stres.get("gecti")
                              and kuyruk.get("rezervli_dayanir"))
    return {
        "girdi": {"sermaye": C, "kaldirac": kaldirac, "rezerv_pay": rezerv_pay,
                  "tutma_gun": tutma_gun, "bakim_teminat": BAKIM_TEMINAT},
        "pozisyon": {"notional": round(P, 2), "teminat": round(M, 2),
                     "rezerv": round(R, 2), "perp_kaldirac": round(L, 2)},
        "normal": {"brut_yillik_pct": round(brut_yillik, 2),
                   "maliyet_yillik_pct": round(maliyet_yillik, 2),
                   "net_yillik_pct": round(net_yillik_pct, 2),
                   "net_yillik_usd": round(net_yillik_usd, 2),
                   "gunluk_carry_usd": round(gunluk_carry_usd, 2),
                   "carry_notr_esik_pct": round(carry_notr_esik(tutma_gun) * 100, 5),
                   "gecti": normal_gecti},
        "stres": stres,
        "kuyruk": kuyruk,
        "finanse_edilebilir": finanse_edilebilir,
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  RAPOR
# ═══════════════════════════════════════════════════════════════════════════════
def _isaret(ok):
    return "✅" if ok else "❌"


def rapor_metni(sonuc):
    sat = ["═══ RİSK FİNANSMAN SKORU (AŞAMA 4) ═══",
           "Soru: fiyat nereye gider DEĞİL → MEVCUT RİSK FİNANSE EDİLEBİLİR Mİ?",
           f"Aralık {sonuc['aralik']}", ""]
    for sym, s in sonuc["semboller"].items():
        g, p = s["girdi"], s["pozisyon"]
        n, st, ky = s["normal"], s["stres"], s["kuyruk"]
        sat.append(f"── {sym} ─────────────────────────────────────────────")
        sat.append(f"  Kurulum: sermaye ${g['sermaye']:,.0f} × {g['kaldirac']}x"
                   f" → pozisyon ${p['notional']:,.0f} · teminat ${p['teminat']:,.0f}"
                   f" · rezerv ${p['rezerv']:,.0f} (perp bacak {p['perp_kaldirac']}x)")
        sat.append(f"  ① NORMAL {_isaret(n['gecti'])}  brüt %{n['brut_yillik_pct']}"
                   f" − maliyet %{n['maliyet_yillik_pct']} = NET %{n['net_yillik_pct']}/yıl"
                   f" (${n['net_yillik_usd']:,.0f}) · günlük carry ${n['gunluk_carry_usd']:,.2f}")
        if st.get("veri_yok"):
            sat.append("  ② STRES  ⚠ funding verisi yok")
        else:
            day = st["rezerv_dayanma_gun"]
            sat.append(f"  ② STRES  {_isaret(st['gecti'])}  en kötü funding penceresi"
                       f" %{st['en_kotu_ort_oran_pct']} ({st['donem']})"
                       f" → günlük kayıp ${st['gunluk_kayip_usd']:,.2f}"
                       f" · rezerv {day if day is not None else '∞'} gün dayanır"
                       f" (gereken {g['tutma_gun']})")
        if ky.get("veri_yok"):
            sat.append("  ③ KUYRUK ⚠ fiyat verisi yok")
        else:
            sat.append(f"  ③ KUYRUK {_isaret(ky['rezervli_dayanir'])}  tarihsel en kötü"
                       f" {g['tutma_gun']}g yukarı hareket %{ky['max_pct']}"
                       f" (p99 %{ky['p99_pct']} · p95 %{ky['p95_pct']}, {ky['ornek']} örnek)")
            sat.append(f"           likidasyon eşiği: rezervsiz %{ky['likidasyon_esigi_pct']}"
                       f" · rezerv transferiyle %{ky['likidasyon_esigi_rezervli_pct']}")
            if not ky["rezervsiz_dayanir"] and ky["rezervli_dayanir"]:
                sat.append("           ⚠ REZERV TRANSFERİ ŞART: transfer olmazsa toplam PnL düz"
                           " olsa bile HEDGE BACAĞI likide olur (ayrı cüzdan).")
        sat.append(f"  ⇒ RİSK FİNANSE EDİLEBİLİR: {_isaret(s['finanse_edilebilir'])}")
        sat.append("")
    sat += [
        "OKUMA:",
        "  Üç test de geçmeden 'finanse edilebilir' YAZILMAZ. Tek bir testin geçmesi",
        "  (ör. carry pozitif) pozisyonun taşınabileceği anlamına GELMEZ.",
        "  Stres ve kuyruk girdileri UYDURMA DEĞİL — gerçekleşmiş tarihten alınır.",
        "",
        "⚠️ MODELDE YOK: spot bacak borçlanma maliyeti · borsa/çekim riski · kademeli",
        "   bakım teminatı · stres anında derinleşen slippage. Çıkan hüküm GEREK ŞART,",
        "   yeter şart değil.",
        "⚠️ Bu bir ALARM katmanıdır, işlem sinyali DEĞİL (ANAYASA #8/#9).",
    ]
    return "\n".join(sat)


# ═══════════════════════════════════════════════════════════════════════════════
#  SELF-TEST (ağ/parquet GEREKMEZ)
# ═══════════════════════════════════════════════════════════════════════════════
def kendi_test():
    import numpy as np
    import pandas as pd

    # ① likidasyon eşiği aritmetiği + rezervin etkisi
    s = risk_skoru(10000, 3, 0.30, 30, ort_funding=0.0001,
                   kotu_funding={"ort_oran": -0.0003, "periyot": 90,
                                 "baslangic": "2022-05-01", "bitis": "2022-06-01"},
                   yukari={"n": 1000, "p50": 0.03, "p95": 0.12, "p99": 0.20, "max": 0.35})
    p = s["pozisyon"]
    assert abs(p["notional"] - 30000) < 1e-6 and abs(p["rezerv"] - 3000) < 1e-6
    assert abs(p["teminat"] - 7000) < 1e-6
    bek_liq = 7000 / 30000 - BAKIM_TEMINAT
    assert abs(s["kuyruk"]["likidasyon_esigi_pct"] - round(bek_liq * 100, 2)) < 1e-9
    assert (s["kuyruk"]["likidasyon_esigi_rezervli_pct"]
            > s["kuyruk"]["likidasyon_esigi_pct"]), "rezerv eşiği YÜKSELTMELİ"
    print(f"[SELF-TEST] likidasyon eşiği: rezervsiz %{s['kuyruk']['likidasyon_esigi_pct']}"
          f" → rezervli %{s['kuyruk']['likidasyon_esigi_rezervli_pct']} ✓")

    # ② kaldıraç arttıkça likidasyon eşiği DÜŞMELİ (risk artar)
    esik = [risk_skoru(10000, k, 0.30, 30, 0.0001, None, None)["kuyruk"] for k in (2, 5, 10)]
    assert all(x["veri_yok"] for x in esik), "yukari=None iken kuyruk ölçülmemeli"
    esikler = [risk_skoru(10000, k, 0.30, 30, 0.0001, None,
                          {"n": 10, "p50": .03, "p95": .1, "p99": .15, "max": .3}
                          )["kuyruk"]["likidasyon_esigi_pct"] for k in (2, 5, 10)]
    assert esikler == sorted(esikler, reverse=True), f"kaldıraç↑ eşik↓ olmalı: {esikler}"
    print(f"[SELF-TEST] kaldıraç 2/5/10x → eşik {esikler} (monoton azalan) ✓")

    # ③ üç testin BİRLİKTE karar vermesi (biri düşerse hüküm düşer)
    iyi = risk_skoru(10000, 1.2, 0.60, 90, 0.00012,
                     {"ort_oran": -0.00002, "periyot": 270, "baslangic": "x", "bitis": "y"},
                     {"n": 500, "p50": .02, "p95": .08, "p99": .12, "max": .25})
    assert iyi["finanse_edilebilir"], "muhafazakâr kurulum geçmeliydi"
    kotu = risk_skoru(10000, 8, 0.05, 1, 0.00012,
                      {"ort_oran": -0.0005, "periyot": 3, "baslangic": "x", "bitis": "y"},
                      {"n": 500, "p50": .02, "p95": .08, "p99": .12, "max": .25})
    assert not kotu["finanse_edilebilir"], "agresif kurulum ELENMELİYDİ"
    assert not kotu["normal"]["gecti"], "1 günlük tutmada maliyet carry'yi yemeli"
    print("[SELF-TEST] muhafazakâr ✅ / agresif ❌ ayrışıyor ✓")

    # ④ tarihsel en kötü yukarı hareket: fitile basmalı (kapanışa değil)
    n = 40 * 1440
    t0 = 1_600_000_000_000 // GUN_MS * GUN_MS
    ot = t0 + np.arange(n) * 60_000
    px = np.full(n, 100.0)
    px[20 * 1440 + 700] = 100.0                       # kapanışlar düz
    hi = px.copy()
    hi[20 * 1440 + 700] = 130.0                       # TEK fitil: %30 yukarı
    k = pd.DataFrame({"open_time": ot, "open": px, "high": hi, "low": px,
                      "close": px, "volume": np.ones(n)})
    y = en_kotu_yukari_hareket("TESTUSDT", "", "", 5, k_df=k)
    assert y and abs(y["max"] - 0.30) < 0.01, f"fitil yakalanmadı: {y}"
    print(f"[SELF-TEST] tarihsel en kötü hareket fitilden okunuyor (max %{y['max']*100:.1f}) ✓")

    # ⑤ en kötü funding penceresi doğru yeri buluyor mu
    kay = [{"ts": t0 + i * PERIYOT_MS, "rate": 0.0001} for i in range(300)]
    for i in range(100, 130):
        kay[i]["rate"] = -0.001                       # gömülü kanama bloğu
    kf = en_kotu_funding_penceresi(kay, tutma_gun=10)
    assert kf and kf["ort_oran"] < 0, f"kanama penceresi bulunamadı: {kf}"
    print(f"[SELF-TEST] en kötü funding penceresi %{kf['ort_oran']*100:.4f} ({kf['donem'] if 'donem' in kf else kf['baslangic']}) ✓")

    print("\n" + rapor_metni({"aralik": "sentetik", "semboller": {"TESTUSDT": s}}))
    print("\n[SELF-TEST] ✓ boru hattı doğru")
    return True


# ═══════════════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT,ETHUSDT")
    ap.add_argument("--from", dest="bas", default="2019-01")
    ap.add_argument("--to", dest="bit", default="2025-06")
    ap.add_argument("--sermaye", type=float, default=10000.0)
    ap.add_argument("--kaldirac", type=float, default=3.0)
    ap.add_argument("--rezerv-pay", dest="rezerv", type=float, default=0.30)
    ap.add_argument("--tutma-gun", dest="tutma", type=float, default=30.0)
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--kendi-test", action="store_true")
    args = ap.parse_args()

    if args.kendi_test:
        kendi_test()
        return

    import numpy as np
    semboller = [s.strip().upper() for s in args.symbol.split(",") if s.strip()]
    out = {}
    for sym in semboller:
        print(f"[Risk] {sym} {args.bas}..{args.bit}…", flush=True)
        kayitlar = funding_oku(sym)
        if not kayitlar:
            print(f"      ⚠ {sym}: funding geçmişi yok → "
                  f"`python oar_funding_carry.py --indir --symbol {sym}`", flush=True)
            continue
        ort = float(np.mean([k["rate"] for k in kayitlar]))
        kotu = en_kotu_funding_penceresi(kayitlar, args.tutma)
        print("      · tarihsel en kötü yukarı hareket taranıyor…", flush=True)
        yukari = en_kotu_yukari_hareket(sym, args.bas, args.bit, args.tutma)
        out[sym] = risk_skoru(args.sermaye, args.kaldirac, args.rezerv, args.tutma,
                              ort, kotu, yukari)
        print(f"      ✓ {sym}: finanse edilebilir = {out[sym]['finanse_edilebilir']}", flush=True)

    if not out:
        print("❌ Veri yok — funding indirildi mi?")
        return

    sonuc = {"tarih": datetime.now(timezone.utc).isoformat(),
             "aralik": f"{args.bas}..{args.bit}", "semboller": out}
    rapor = rapor_metni(sonuc)
    print("\n" + rapor)
    Path(CIKTI).write_text(json.dumps(sonuc, ensure_ascii=False, indent=2, default=str),
                           encoding="utf-8")
    print(f"\n💾 {CIKTI} yazıldı (git-senkron)")

    if args.telegram:
        try:
            import asyncio
            from ajan_merkez import bildir
            asyncio.run(bildir("Risk Finansman Skoru", "backtest",
                               rapor.split("\n", 2)[1], detay=rapor))
            print("[Telegram] thread 4129'a gönderildi ✓", flush=True)
        except Exception as e:
            print(f"[Telegram] gönderilemedi: {str(e)[:80]}", flush=True)


if __name__ == "__main__":
    main()
