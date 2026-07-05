"""
oar_hipotez_motoru.py — OAR Otonom Hipotez ÜRETİCİ + Yerel Araştırıcı + Telegram
═══════════════════════════════════════════════════════════════════════════════════════
AMAÇ (kullanıcı isteği): Hipotezi ben dikte etmeyeyim. Ajan SÜREKLİ yeni OAR hipotezleri
ÜRETSİN → yerel derin-geçmiş parquet'te ARAŞTIRSIN → LIFT + OOS ile elesin → kazananı
Telegram'a (thread 4129) atsın → denenen/reddedileni HATIRLASIN (aynı şeyi tekrar denemesin).

autonomous_researcher.py yalnız parametre gridi deniyordu; theory_lab.py sabit 10 teori +
canlı sığ veri. Bu motor FARKLI: kombinatoryal hipotez uzayı + yerel yıllarca veri.

HİPOTEZ UZAYI (yapı taşları — koşul × hedef):
  TETİK   : asia_high_kırılım | asia_low_kırılım | üst_ekstrem_temas(fade) | alt_ekstrem_temas(fade)
  FİLTRE  : hacim_destek(var/yok) · gün(hepsi/haftaiçi/haftasonu/tek gün) · asia_genlik kovası
  HEDEF   : fib 1.618 vur | fib 2.618 vur | range_ortası(0.5) dönüş | ileri getiri yönü (intraday/hafta)

METODOLOJİ: NO-LOOKAHEAD (hacim tabanı önceki 20 gün), LIFT = koşullu − taban, MIN_N örneklem,
OOS holdout (son %20). "OOS ✅" yalnız yeterli örneklem; edge kanıtı LIFT'tir.

Veri: yerel BTC/ETH parquet klines(1m). aggTrades gerekmez → hızlı.
Çalıştırma (tek seferlik):  python oar_hipotez_motoru.py --symbol BTCUSDT,ETHUSDT --from 2019-01 --to 2025-06 --telegram
Sürekli döngü:             python oar_hipotez_motoru.py --loop --aralik-saat 6 --telegram
"""
import argparse
import hashlib
import json
from collections import deque
from pathlib import Path
from datetime import datetime, timezone

from oar_local_backtest import (_klines_oku, _ms_olcekle, fib_seviyeleri,
                                GUN_MS, SAAT_MS)

MIN_RANGE     = 1.0
HACIM_PENCERE = 20
ASIA_BIT      = 4.0
MIN_N         = 40      # bir hipotezi raporlamak için asgari örneklem
OOS_ORAN      = 0.20
LIFT_ESIK     = 5.0     # kazanan sayılmak için asgari LIFT (yüzde puan)

HAFIZA = Path(__file__).resolve().parent / ".hipotez_hafiza.json"


# ─── Gün×seans feature tablosu (bir kez hesapla, hipotezler bunun üstünde maske) ───
def _feature_tablo(sembol, bas, bit):
    import pandas as pd
    import numpy as np

    print(f"   [{sembol}] klines yükleniyor…", flush=True)
    k = _klines_oku(sembol, bas, bit)
    if k is None or not len(k):
        print(f"   [{sembol}] ⚠ klines yok", flush=True)
        return []
    k = k.copy()
    k["open_time"] = _ms_olcekle(k["open_time"])
    OT_MIN, OT_MAX = 1_400_000_000_000, 2_000_000_000_000
    k = k[(k["open_time"] >= OT_MIN) & (k["open_time"] < OT_MAX)]
    k["gun"]  = (k["open_time"] // GUN_MS).astype("int64")
    k["saat"] = (k["open_time"] % GUN_MS) / SAAT_MS
    print(f"   [{sembol}] {len(k):,} mum · gün×feature çıkarılıyor…", flush=True)

    gun_kapanis = k.groupby("gun")["close"].last()
    satirlar = []
    hacim_taban = deque(maxlen=HACIM_PENCERE)
    gunler = sorted(k["gun"].unique())
    for g in gunler:
        gk = k[k["gun"] == g]
        asia = gk[gk["saat"] < ASIA_BIT]
        post = gk[gk["saat"] >= ASIA_BIT].sort_values("open_time")
        if asia.empty or post.empty:
            continue
        a_high = float(asia["high"].max()); a_low = float(asia["low"].min())
        a_close = float(asia["close"].iloc[-1])
        if a_low <= 0:
            continue
        rng_pct = (a_high - a_low) / a_low * 100.0
        taban = float(np.median(hacim_taban)) if len(hacim_taban) >= 5 else None
        gun_dk_hacim = float(post["volume"].mean())

        row = {
            "sembol": sembol, "gun": int(g),
            "gecerli": rng_pct >= MIN_RANGE,
            "rng_pct": round(rng_pct, 3),
            "hafta_gunu": (int(g) + 3) % 7,   # 1970-01-01 = Perşembe(3); 5=Cmt 6=Pzr
        }
        if row["gecerli"]:
            fibs = fib_seviyeleri(a_low, a_high)
            hi = post["high"].to_numpy(); lo = post["low"].to_numpy()
            cl = post["close"].to_numpy()
            # üst kırılım
            brk_hi_idx = int(np.argmax(hi >= a_high)) if (hi >= a_high).any() else -1
            brk_lo_idx = int(np.argmax(lo <= a_low)) if (lo <= a_low).any() else -1
            row["brk_hi"] = brk_hi_idx >= 0
            row["brk_lo"] = brk_lo_idx >= 0
            if brk_hi_idx >= 0:
                sonr = post.iloc[brk_hi_idx:]
                kir_hacim = float(sonr["volume"].iloc[:60].mean())
                row["hacim_destek"] = (taban is not None) and (kir_hacim > taban)
                mh = float(sonr["high"].max())
                row["tp1_618"] = mh >= fibs[1.618]
                row["tp_2618"] = mh >= fibs[2.618]
            else:
                row["hacim_destek"] = False
                row["tp1_618"] = False; row["tp_2618"] = False
            # fade: üst ekstrem temas → range ortasına (0.5) dönüş oldu mu
            row["ust_temas"] = (hi >= fibs[1.618]).any()
            if row["ust_temas"]:
                ti = int(np.argmax(hi >= fibs[1.618]))
                row["fade_mid"] = bool((lo[ti:] <= fibs[0.5]).any())
            else:
                row["fade_mid"] = False
            # ileri getiriler
            row["ret_intraday"] = round((cl[-1] - a_close) / a_close * 100.0, 3)
        hacim_taban.append(gun_dk_hacim)
        satirlar.append(row)

    # ileri (hafta) getiri: bugünün kapanışından +7 gün
    gk_map = gun_kapanis.to_dict()
    for r in satirlar:
        g = r["gun"]; c0 = gk_map.get(g)
        c7 = next((gk_map[g + d] for d in range(7, 0, -1) if (g + d) in gk_map), None)
        r["ret_week"] = round((c7 - c0) / c0 * 100.0, 3) if (c0 and c7) else None
    print(f"   [{sembol}] ✓ {len(satirlar)} gün", flush=True)
    return satirlar


# ─── Hipotez uzayı: (ad, koşul-fonksiyonu, hedef-fonksiyonu) üret ───
def _hipotezler():
    hafta_adi = {5: "Cumartesi", 6: "Pazar", 0: "Pazartesi", 1: "Salı",
                 2: "Çarşamba", 3: "Perşembe", 4: "Cuma"}
    gun_kova = [("hepsi", lambda r: True),
                ("haftaiçi", lambda r: r["hafta_gunu"] < 5),
                ("haftasonu", lambda r: r["hafta_gunu"] >= 5)]
    for gn, gf in [(hafta_adi[i], (lambda i: lambda r: r["hafta_gunu"] == i)(i)) for i in range(7)]:
        gun_kova.append((gn, gf))
    genlik_kova = [("genlik≥%1", lambda r: True),
                   ("genlik1-2", lambda r: 1.0 <= r["rng_pct"] < 2.0),
                   ("genlik≥2", lambda r: r["rng_pct"] >= 2.0)]

    H = []
    # 1) Asia-high kırılım + (hacim var/yok) → fib TP hedefleri
    for hac_ad, hac_f in [("hacim-destekli", lambda r: r.get("hacim_destek")),
                          ("hacim-farketmez", lambda r: True)]:
        for gk_ad, gk_f in genlik_kova:
            for hedef_ad, hedef_f in [("TP1=1.618", lambda r: r.get("tp1_618")),
                                      ("FULL=2.618", lambda r: r.get("tp_2618"))]:
                kosul = (lambda gk_f, hac_f: lambda r: r["gecerli"] and r.get("brk_hi") and gk_f(r) and hac_f(r))(gk_f, hac_f)
                H.append((f"Asia-high kırılım·{hac_ad}·{gk_ad} → {hedef_ad}",
                          kosul, hedef_f, "hit"))
    # 2) Gün bazlı: Asia-high kırılım → hafta bull
    for gn, gf in gun_kova:
        kosul = (lambda gf: lambda r: r["gecerli"] and r.get("brk_hi") and gf(r) and r.get("ret_week") is not None)(gf)
        H.append((f"{gn}·Asia-high kırılım → hafta bull",
                  kosul, lambda r: (r.get("ret_week") or 0) > 0, "hafta"))
    # 3) Fade: üst ekstrem (1.618) temas → range ortasına dönüş
    for gk_ad, gk_f in genlik_kova:
        kosul = (lambda gk_f: lambda r: r["gecerli"] and r.get("ust_temas") and gk_f(r))(gk_f)
        H.append((f"Üst ekstrem temas·{gk_ad} → 0.5 fade dönüşü",
                  kosul, lambda r: r.get("fade_mid"), "fade"))
    return H


def _degerlendir(rows, kosul, hedef):
    alt = [r for r in rows if kosul(r)]
    n = len(alt)
    if n < MIN_N:
        return None
    k = sum(1 for r in alt if hedef(r))
    wr = 100.0 * k / n
    # OOS: kronolojik son %20
    alt_s = sorted(alt, key=lambda r: r["gun"])
    kes = int(len(alt_s) * (1 - OOS_ORAN))
    oos = alt_s[kes:]
    oos_wr = (100.0 * sum(1 for r in oos if hedef(r)) / len(oos)) if oos else None
    return {"n": n, "wr": round(wr, 1),
            "oos_n": len(oos), "oos_wr": round(oos_wr, 1) if oos_wr is not None else None}


def _taban_wr(rows, hedef, evren):
    """Koşulsuz taban: aynı hedef, aynı evren (ör. tüm geçerli kırılım günleri)."""
    alt = [r for r in rows if evren(r)]
    if not alt:
        return None
    return round(100.0 * sum(1 for r in alt if hedef(r)) / len(alt), 1)


def _hafiza_yukle():
    try:
        return set(json.loads(HAFIZA.read_text())) if HAFIZA.exists() else set()
    except Exception:
        return set()


def _hafiza_kaydet(s):
    try:
        HAFIZA.write_text(json.dumps(sorted(s), ensure_ascii=False, indent=0))
    except Exception:
        pass


def arastir(rows):
    """Tüm hipotezleri değerlendir → (kazananlar, tüm sonuç satırları)."""
    # evren fonksiyonları (taban için)
    ev_brk  = lambda r: r["gecerli"] and r.get("brk_hi")
    ev_week = lambda r: r["gecerli"] and r.get("brk_hi") and r.get("ret_week") is not None
    ev_fade = lambda r: r["gecerli"] and r.get("ust_temas")
    ev_map  = {"hit": ev_brk, "hafta": ev_week, "fade": ev_fade}

    kazanan, tum = [], []
    for ad, kosul, hedef, tip in _hipotezler():
        s = _degerlendir(rows, kosul, hedef)
        if not s:
            continue
        taban = _taban_wr(rows, hedef, ev_map[tip])
        lift = round(s["wr"] - taban, 1) if taban is not None else None
        rec = {"ad": ad, "n": s["n"], "wr": s["wr"], "taban": taban,
               "lift": lift, "oos_wr": s["oos_wr"], "oos_n": s["oos_n"]}
        tum.append(rec)
        if lift is not None and lift >= LIFT_ESIK:
            kazanan.append(rec)
    kazanan.sort(key=lambda r: r["lift"], reverse=True)
    tum.sort(key=lambda r: (r["lift"] if r["lift"] is not None else -99), reverse=True)
    return kazanan, tum


def _rapor_metni(kazanan, tum, toplam_gun):
    L = [f"═══ OAR HİPOTEZ MOTORU — {len(tum)} hipotez test edildi ({toplam_gun} gün) ═══"]
    if kazanan:
        L.append(f"🏆 KAZANAN ({len(kazanan)}, LIFT≥{LIFT_ESIK:g}):")
        for r in kazanan[:8]:
            L.append(f"  • {r['ad']}: WR%{r['wr']} vs taban%{r['taban']} → LIFT {r['lift']:+} "
                     f"(n{r['n']}, OOS%{r['oos_wr']})")
    else:
        L.append("❌ Bu turda LIFT≥eşik kazanan hipotez YOK. En iyi 5:")
        for r in tum[:5]:
            L.append(f"  • {r['ad']}: LIFT {r['lift']} (WR%{r['wr']} vs %{r['taban']}, n{r['n']})")
    return "\n".join(L)


def tek_tur(semboller, bas, bit, telegram=False):
    rows = []
    for sym in semboller:
        print(f"[HipotezMotoru] {sym} {bas}..{bit} feature…", flush=True)
        rows += _feature_tablo(sym, bas, bit)
    kazanan, tum = arastir(rows)

    # HAFIZA: daha önce raporlanmış kazananları tekrar rapor etme (yeni olanları işaretle)
    hafiza = _hafiza_yukle()
    yeni = [r for r in kazanan if hashlib.md5(r["ad"].encode()).hexdigest()[:10] not in hafiza]
    for r in kazanan:
        hafiza.add(hashlib.md5(r["ad"].encode()).hexdigest()[:10])
    _hafiza_kaydet(hafiza)

    rapor = _rapor_metni(kazanan, tum, len({r["gun"] for r in rows}))
    print("\n" + rapor)
    if yeni:
        print(f"\n[HipotezMotoru] {len(yeni)} YENİ kazanan (ilk kez): "
              + " · ".join(r["ad"] for r in yeni), flush=True)

    if telegram and (yeni or not kazanan):
        try:
            import asyncio
            from ajan_merkez import bildir
            ozet = (f"{len(yeni)} YENİ kazanan hipotez bulundu" if yeni
                    else "Bu turda kazanan hipotez yok")
            asyncio.run(bildir("Hipotez Motoru", "research", ozet, detay=rapor))
            print("[Telegram] rapor thread 4129'a gönderildi ✓", flush=True)
        except Exception as e:
            print(f"[Telegram] gönderilemedi: {str(e)[:80]}", flush=True)
    return kazanan, tum


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT,ETHUSDT")
    ap.add_argument("--from", dest="bas", default="2019-01")
    ap.add_argument("--to",   dest="bit", default="2025-06")
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--loop", action="store_true", help="sürekli döngü modu")
    ap.add_argument("--aralik-saat", type=int, default=6)
    args = ap.parse_args()
    semboller = [s.strip().upper() for s in args.symbol.split(",") if s.strip()]

    if not args.loop:
        tek_tur(semboller, args.bas, args.bit, telegram=args.telegram)
        return
    import time
    while True:
        try:
            tek_tur(semboller, args.bas, args.bit, telegram=args.telegram)
        except Exception as e:
            print(f"[HipotezMotoru] hata: {str(e)[:100]}", flush=True)
        print(f"[HipotezMotoru] {args.aralik_saat}h uyku…", flush=True)
        time.sleep(args.aralik_saat * 3600)


if __name__ == "__main__":
    main()
