"""
oar_kirilim_trade.py — Asia-High Kırılım Trend-Devam: GERÇEK İŞLEM Doğrulaması (LOCAL)
═══════════════════════════════════════════════════════════════════════════════════════
Hipotez motorunun bulduğu EN GÜÇLÜ aday:
  "Asia genlik %1-2 + hacim destekli + Asia-HIGH kırılım → fib 1.618 devam"
  (vuruş WR %70, OOS %73, LIFT +9.7 — AMA vuruş oranı ≠ KÂR).

Bu modül onu GERÇEK İŞLEME çevirir ve net beklentiyi ölçer:
  • Giriş  = Asia-high (kırılım seviyesi, retest fill varsayımı)
  • TP      = fib 1.618
  • SL adayları (hepsi ayrı test edilir):
      asia_high_alti  : a_high − 0.15·range  (dar — whipsaw testi)
      range_orta_0.5  : fib 0.5
      range_dibi_0.0  : fib 0.0 (Asia-low)
      yarim_R         : a_high − 0.5·range
  • Bar-bar high/low ile değerlendirme; AYNI barda SL de TP de mümkünse SL önce
    kabul edilir (KÖTÜMSER — iyimser yanlılık yok). Fee+slippage düşülür.

Çıktı (SL varyantı başına): n · WR · PF · beklenti(ort net %) · maxDD · toplam% · OOS beklenti.
NO-LOOKAHEAD hacim tabanı (önceki 20 gün). Whipsaw doğrudan SL varyantlarında görünür.

⚠️ Bu bir TREND-DEVAM kurulumu; şampiyon fade+htf_vpfr'nin TERSİ rejim (ANAYASA #8 —
   şampiyon koduna DOKUNMAZ). Amaç: kırılım günü fade'i bastırıp devam confirm'i sağlamak.

Çalıştırma:  python oar_kirilim_trade.py --symbol BTCUSDT,ETHUSDT --from 2019-01 --to 2025-06 --telegram
"""
import argparse
from collections import deque
from datetime import datetime

from oar_local_backtest import (_klines_oku, _ms_olcekle, fib_seviyeleri,
                                FEE_PCT, SLIP_PCT, GUN_MS, SAAT_MS)

MIN_RANGE_ALT = 1.0   # genlik kovası alt (%)
MIN_RANGE_UST = 2.0   # genlik kovası üst (%) — hipotez motorunun kazanan kovası
HACIM_PENCERE = 20
ASIA_BIT      = 4.0
TP_FIB        = 1.618
MIN_N         = 40
OOS_ORAN      = 0.20


def _sl_adaylari(a_high, a_low, fibs):
    rng = a_high - a_low
    return {
        "asia_high_alti": a_high - 0.15 * rng,
        "range_orta_0.5": fibs[0.5],
        "range_dibi_0.0": fibs[0.0],
        "yarim_R":        a_high - 0.5 * rng,
    }


def _islem_sonucu(giris, tp, sl, hi, lo):
    """
    Bar-bar LONG değerlendirme (giriş sonrası high/low dizileri).
    Aynı barda hem SL hem TP mümkünse SL ÖNCE (kötümser). Döner: net_pct (fee dahil).
    Hiçbiri vurmazsa son bar kapanışı yerine son low/high ortası → burada 'ulaşmadı'
    kabul edip giriş=çıkış (sadece maliyet kaybı) SAYMAK yanıltır; gerçekçi: gün sonu
    kapanışı bilinmiyor → son barın (hi+lo)/2 ile çıkış.
    """
    for i in range(len(hi)):
        if lo[i] <= sl:                      # önce SL (kötümser)
            gross = (sl - giris) / giris * 100.0
            return round(gross - FEE_PCT - SLIP_PCT, 4)
        if hi[i] >= tp:
            gross = (tp - giris) / giris * 100.0
            return round(gross - FEE_PCT - SLIP_PCT, 4)
    cikis = (hi[-1] + lo[-1]) / 2.0 if len(hi) else giris
    gross = (cikis - giris) / giris * 100.0
    return round(gross - FEE_PCT - SLIP_PCT, 4)


def _analiz(sembol, bas, bit):
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
    print(f"   [{sembol}] {len(k):,} mum · günler işleniyor…", flush=True)

    islemler = []
    hacim_taban = deque(maxlen=HACIM_PENCERE)
    gunler = sorted(k["gun"].unique())
    son_ay = None
    for g in gunler:
        ay = datetime.utcfromtimestamp(int(g) * 86400).strftime("%Y-%m")
        if ay != son_ay:
            print(f"      · [{sembol}] {ay} analiz ediliyor…", flush=True)
            son_ay = ay
        gk = k[k["gun"] == g]
        asia = gk[gk["saat"] < ASIA_BIT]
        post = gk[gk["saat"] >= ASIA_BIT].sort_values("open_time")
        if asia.empty or post.empty:
            continue
        a_high = float(asia["high"].max()); a_low = float(asia["low"].min())
        if a_low <= 0:
            continue
        rng_pct = (a_high - a_low) / a_low * 100.0
        taban = float(np.median(hacim_taban)) if len(hacim_taban) >= 5 else None
        gun_dk_hacim = float(post["volume"].mean())

        # HİPOTEZ FİLTRESİ: genlik %1-2
        if not (MIN_RANGE_ALT <= rng_pct < MIN_RANGE_UST):
            hacim_taban.append(gun_dk_hacim)
            continue

        hi_all = post["high"].to_numpy(); lo_all = post["low"].to_numpy()
        kir_idx = int(np.argmax(hi_all >= a_high)) if (hi_all >= a_high).any() else -1
        if kir_idx < 0:
            hacim_taban.append(gun_dk_hacim)
            continue

        sonr = post.iloc[kir_idx:]
        kir_hacim = float(sonr["volume"].iloc[:60].mean())
        destek = (taban is not None) and (kir_hacim > taban)

        fibs = fib_seviyeleri(a_low, a_high)
        tp = fibs[TP_FIB]
        sl_map = _sl_adaylari(a_high, a_low, fibs)
        hi = sonr["high"].to_numpy(); lo = sonr["low"].to_numpy()
        giris = a_high

        rec = {"gun": int(g), "destek": destek, "sonuc": {}}
        for sl_ad, sl in sl_map.items():
            if sl >= giris:      # geçersiz SL (girişin üstünde) — atla
                continue
            rec["sonuc"][sl_ad] = _islem_sonucu(giris, tp, sl, hi, lo)
        islemler.append(rec)
        hacim_taban.append(gun_dk_hacim)

    print(f"   [{sembol}] ✓ {len(islemler)} kırılım işlemi (genlik%1-2)", flush=True)
    return islemler


def _metrikler(netler):
    """net % listesi → WR, PF, beklenti, maxDD, toplam."""
    n = len(netler)
    if not n:
        return None
    kazanan = [x for x in netler if x > 0]
    kayip   = [x for x in netler if x <= 0]
    gross_k = sum(kazanan)
    gross_z = abs(sum(kayip))
    pf = round(gross_k / gross_z, 2) if gross_z > 0 else float("inf")
    # maxDD (equity eğrisi)
    eq = pk = dd = 0.0
    for x in netler:
        eq += x; pk = max(pk, eq); dd = max(dd, pk - eq)
    return {
        "n": n,
        "wr": round(100.0 * len(kazanan) / n, 1),
        "pf": pf,
        "beklenti": round(sum(netler) / n, 3),
        "maxdd": round(dd, 2),
        "toplam": round(sum(netler), 1),
    }


def _oos_bol(islemler):
    isl = sorted(islemler, key=lambda r: r["gun"])
    kes = int(len(isl) * (1 - OOS_ORAN))
    return isl[:kes], isl[kes:]


def _rapor(islemler):
    destekli = [r for r in islemler if r["destek"]]
    L = [f"═══ KIRILIM TREND-DEVAM İŞLEM DOĞRULAMASI (genlik%1-2, fee dahil) ═══",
         f"Toplam kırılım işlemi: {len(islemler)} · hacim-destekli: {len(destekli)}"]
    sl_adlar = ["asia_high_alti", "range_orta_0.5", "range_dibi_0.0", "yarim_R"]
    for grup_ad, grup in [("HACİM DESTEKLİ", destekli), ("TÜM KIRILIM", islemler)]:
        if len(grup) < MIN_N:
            continue
        L.append(f"\n▸ {grup_ad} (n{len(grup)}):")
        ins, oos = _oos_bol(grup)
        for sl_ad in sl_adlar:
            netler = [r["sonuc"][sl_ad] for r in grup if sl_ad in r["sonuc"]]
            m = _metrikler(netler)
            if not m or m["n"] < MIN_N:
                continue
            oos_netler = [r["sonuc"][sl_ad] for r in oos if sl_ad in r["sonuc"]]
            om = _metrikler(oos_netler)
            oos_bek = om["beklenti"] if om else None
            iy = "✅" if (m["pf"] != float("inf") and m["pf"] >= 1.3 and m["beklenti"] > 0) else "  "
            L.append(f"  {iy} SL={sl_ad:15} WR%{m['wr']:<4} PF {m['pf']:<4} "
                     f"beklenti {m['beklenti']:+.3f}% maxDD {m['maxdd']}% "
                     f"toplam {m['toplam']:+.0f}% | OOS beklenti {oos_bek}")
    L.append("\nNOT: beklenti = işlem başına ort. net % (fee sonrası). PF≥1.3 + beklenti>0 = aday.")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT,ETHUSDT")
    ap.add_argument("--from", dest="bas", default="2019-01")
    ap.add_argument("--to",   dest="bit", default="2025-06")
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args()

    tum = []
    for sym in [s.strip().upper() for s in args.symbol.split(",") if s.strip()]:
        print(f"[KırılımTrade] {sym} {args.bas}..{args.bit}…", flush=True)
        tum += _analiz(sym, args.bas, args.bit)

    rapor = _rapor(tum)
    print("\n" + rapor)

    if args.telegram:
        try:
            import asyncio
            from ajan_merkez import bildir
            asyncio.run(bildir("Kırılım Trade Doğrulama", "backtest",
                               rapor.split("\n", 2)[1], detay=rapor))
            print("\n[Telegram] thread 4129'a gönderildi ✓", flush=True)
        except Exception as e:
            print(f"\n[Telegram] gönderilemedi: {str(e)[:80]}", flush=True)


if __name__ == "__main__":
    main()
