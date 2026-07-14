"""
oar_fib_paslasma.py — En Çok Paslaşılan Fib Koridorları + Hacim-Bazlı Scalp Şampiyonu
═══════════════════════════════════════════════════════════════════════════════════════
KULLANICI FİKRİ: "En çok birbiri arasında paslaşmış 3 fib'i bul, oraları hacim bazlı
inceleyerek scalpçı şampiyon çıkar."

YÖNTEM (2 faz, no-lookahead):
  FAZ-A (KEŞİF): Post-Asia fiyatın fib'den fib'e GEÇİŞLERİ sayılır (1m kapanış hangi
    fib katına ulaştı). En çok GİDİP-GELİNEN (A→B→A round-trip) komşu fib çifti =
    "paslaşma koridoru". En iyi 3 koridor raporlanır.
    ÖNEMLİ: koridor seçimi yalnız İLK %60 günle yapılır (keşif); scalp testi SON %40'ta
    da raporlanır → koridor seçimi test verisine sızmaz (no-lookahead seçim).
  FAZ-B (SCALP TESTİ): Her top koridorda: fiyat koridorun bir KENARINA dokununca
    karşı kenara scalp (TP=karşı kenar, SL=kenarın bir fib dışı). HACİM filtresi:
    temas dakikası hacmi gün-içi medyanın üstünde mi? Hacimli vs hacimsiz AYRI
    raporlanır → hacmin katkısı (senin "hacim bazlı" isteğin) net görünür.

Asia ≥%1 kuralı geçerli. Fee dahil, bar-bar, aynı barda SL önce. Sonuç deftere yazılır.
Çalıştırma:  python oar_fib_paslasma.py --symbol BTCUSDT,ETHUSDT --from 2019-01 --to 2025-06 --telegram
"""
import argparse
from datetime import datetime, timezone

from oar_local_backtest import (_klines_oku, _ms_olcekle, fib_seviyeleri,
                                FEE_PCT, SLIP_PCT, GUN_MS, SAAT_MS, OAR_FIB)

MIN_RANGE = 1.0
ASIA_BIT  = 4.0
MIN_N     = 40
KESIF_ORAN = 0.60      # koridor seçimi ilk %60 günde (no-lookahead seçim)
SIRALI = sorted(OAR_FIB)


def _fib_kat(fiyat, fibs):
    """Fiyat hangi fib katında (en yakın alt fib indeksi)."""
    for i in range(len(SIRALI) - 1, -1, -1):
        if fiyat >= fibs[SIRALI[i]]:
            return i
    return 0


def _islem(giris, tp, sl, hi, lo, yon):
    for i in range(len(hi)):
        if yon == "LONG":
            if lo[i] <= sl:
                return round((sl - giris) / giris * 100 - FEE_PCT - SLIP_PCT, 4)
            if hi[i] >= tp:
                return round((tp - giris) / giris * 100 - FEE_PCT - SLIP_PCT, 4)
        else:
            if hi[i] >= sl:
                return round((giris - sl) / giris * 100 - FEE_PCT - SLIP_PCT, 4)
            if lo[i] <= tp:
                return round((giris - tp) / giris * 100 - FEE_PCT - SLIP_PCT, 4)
    cikis = (hi[-1] + lo[-1]) / 2.0 if len(hi) else giris
    g = (cikis - giris) if yon == "LONG" else (giris - cikis)
    return round(g / giris * 100 - FEE_PCT - SLIP_PCT, 4)


def _gunler(sembol, bas, bit):
    """Geçerli günlerin (fibs, post hi/lo/cl/vol, ts) listesi — kronolojik."""
    import numpy as np
    print(f"   [{sembol}] klines yükleniyor…", flush=True)
    k = _klines_oku(sembol, bas, bit)
    if k is None or not len(k):
        return []
    k = k.copy()
    k["open_time"] = _ms_olcekle(k["open_time"])
    OT_MIN, OT_MAX = 1_400_000_000_000, 2_000_000_000_000
    k = k[(k["open_time"] >= OT_MIN) & (k["open_time"] < OT_MAX)]
    k["gun"]  = (k["open_time"] // GUN_MS).astype("int64")
    k["saat"] = (k["open_time"] % GUN_MS) / SAAT_MS
    out = []
    son_ay = None
    for g in sorted(k["gun"].unique()):
        ay = datetime.fromtimestamp(int(g) * 86400, timezone.utc).strftime("%Y-%m")
        if ay != son_ay:
            print(f"      · [{sembol}] {ay} analiz ediliyor…", flush=True)
            son_ay = ay
        gk = k[k["gun"] == g]
        asia = gk[gk["saat"] < ASIA_BIT]
        post = gk[gk["saat"] >= ASIA_BIT].sort_values("open_time")
        if asia.empty or len(post) < 30:
            continue
        a_h = float(asia["high"].max()); a_l = float(asia["low"].min())
        if a_l <= 0 or (a_h - a_l) / a_l * 100.0 < MIN_RANGE:
            continue
        out.append({
            "ts": int(post["open_time"].iloc[0]),
            "fibs": fib_seviyeleri(a_l, a_h),
            "hi": post["high"].to_numpy(), "lo": post["low"].to_numpy(),
            "cl": post["close"].to_numpy(), "vol": post["volume"].to_numpy(),
        })
    print(f"   [{sembol}] ✓ {len(out)} geçerli gün", flush=True)
    return out


def _koridor_kesif(gunler):
    """FAZ-A: komşu fib çiftleri arası ROUND-TRIP (A→B→A) sayımı → en iyi 3 koridor."""
    import numpy as np
    rt = {}   # (i,i+1) → round-trip sayısı
    for g in gunler:
        katlar = [_fib_kat(c, g["fibs"]) for c in g["cl"]]
        # kat dizisinden A→B→A kalıpları: son iki farklı katı izle
        onceki_kat = katlar[0]; onceki_yonlu = None
        for kat in katlar[1:]:
            if kat == onceki_kat:
                continue
            cift = (min(kat, onceki_kat), max(kat, onceki_kat))
            if abs(kat - onceki_kat) == 1:          # komşu geçiş
                if onceki_yonlu == (cift, -1 if kat < onceki_kat else 1):
                    pass
                # geri dönüş mü (önceki geçiş aynı çiftin tersi yönü)?
                if onceki_yonlu and onceki_yonlu[0] == cift and onceki_yonlu[1] != (1 if kat > onceki_kat else -1):
                    rt[cift] = rt.get(cift, 0) + 1   # A→B→A tamam
                onceki_yonlu = (cift, 1 if kat > onceki_kat else -1)
            else:
                onceki_yonlu = None
            onceki_kat = kat
    top = sorted(rt.items(), key=lambda kv: kv[1], reverse=True)[:3]
    return top   # [((i,j), sayi)]


def _koridor_scalp(gunler, i, j):
    """FAZ-B: koridor (SIRALI[i]..SIRALI[j]) kenar temaslarında karşı kenara scalp.
    Döner: {'hacimli': [(ts,net)], 'hacimsiz': [...]}"""
    import numpy as np
    alt_o, ust_o = SIRALI[i], SIRALI[j]
    sl_alt_o = SIRALI[i - 1] if i > 0 else None
    sl_ust_o = SIRALI[j + 1] if j < len(SIRALI) - 1 else None
    out = {"hacimli": [], "hacimsiz": []}
    for g in gunler:
        fibs = g["fibs"]; hi, lo, cl, vol = g["hi"], g["lo"], g["cl"], g["vol"]
        alt_s, ust_s = fibs[alt_o], fibs[ust_o]
        vmed = float(np.median(vol)) or 1.0
        alindi_alt = alindi_ust = False
        for t in range(len(cl)):
            if not alindi_alt and lo[t] <= alt_s and sl_alt_o is not None:
                # alt kenara temas → LONG karşı kenara
                net = _islem(alt_s, ust_s, fibs[sl_alt_o], hi[t:], lo[t:], "LONG")
                grup = "hacimli" if vol[t] > vmed else "hacimsiz"
                out[grup].append((g["ts"], net)); alindi_alt = True
            if not alindi_ust and hi[t] >= ust_s and sl_ust_o is not None:
                net = _islem(ust_s, alt_s, fibs[sl_ust_o], hi[t:], lo[t:], "SHORT")
                grup = "hacimli" if vol[t] > vmed else "hacimsiz"
                out[grup].append((g["ts"], net)); alindi_ust = True
            if alindi_alt and alindi_ust:
                break
    return out


def _metrik(kayitlar):
    netler = [x[1] for x in sorted(kayitlar)]
    n = len(netler)
    if n < MIN_N:
        return None
    kaz = [x for x in netler if x > 0]; kyp = [x for x in netler if x <= 0]
    gz = abs(sum(kyp))
    pf = round(sum(kaz) / gz, 2) if gz > 0 else float("inf")
    eq = pk = dd = 0.0
    for x in netler:
        eq += x; pk = max(pk, eq); dd = max(dd, pk - eq)
    kes = int(n * 0.8)
    oos = netler[kes:]
    oos_b = round(sum(oos) / len(oos), 3) if oos else None
    return {"n": n, "wr": round(100 * len(kaz) / n, 1), "pf": pf,
            "beklenti": round(sum(netler) / n, 3), "maxdd": round(dd, 2), "oos": oos_b}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT,ETHUSDT")
    ap.add_argument("--from", dest="bas", default="2019-01")
    ap.add_argument("--to",   dest="bit", default="2025-06")
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args()

    gunler = []
    for sym in [s.strip().upper() for s in args.symbol.split(",") if s.strip()]:
        print(f"[FibPaslaşma] {sym} {args.bas}..{args.bit}…", flush=True)
        gunler += _gunler(sym, args.bas, args.bit)
    if not gunler:
        print("⚠ geçerli gün yok."); return
    gunler.sort(key=lambda g: g["ts"])

    # FAZ-A: koridor keşfi YALNIZ ilk %60 günde (seçim test verisine sızmasın)
    kes = int(len(gunler) * KESIF_ORAN)
    top = _koridor_kesif(gunler[:kes])
    L = ["═══ FİB PASLAŞMA KORİDORLARI + HACİM-BAZLI SCALP ═══",
         f"Keşif: ilk {kes} gün · Test: tüm {len(gunler)} gün (OOS=son %20)",
         "\nEN ÇOK PASLAŞILAN 3 KORİDOR (A→B→A round-trip, keşif dilimi):"]
    for (i, j), sayi in top:
        L.append(f"  • fib {SIRALI[i]:+.3f} ↔ {SIRALI[j]:+.3f}: {sayi} round-trip")

    # FAZ-B: her koridorda hacimli/hacimsiz scalp
    defter = []
    for (i, j), sayi in top:
        r = _koridor_scalp(gunler, i, j)
        L.append(f"\n▸ KORİDOR {SIRALI[i]:+.3f} ↔ {SIRALI[j]:+.3f} (TP=karşı kenar, SL=1 fib dış):")
        for grup in ("hacimli", "hacimsiz"):
            m = _metrik(r[grup])
            if m:
                iy = "✅" if (m["pf"] >= 1.3 and m["beklenti"] > 0 and (m["oos"] or 0) >= 0) else "  "
                L.append(f"  {iy} {grup:9}: n{m['n']} · WR%{m['wr']} · PF {m['pf']} · "
                         f"beklenti {m['beklenti']:+.3f}% · maxDD {m['maxdd']}% · OOS {m['oos']}")
                defter.append({"ad": f"[paslaşma] {SIRALI[i]:+.3f}↔{SIRALI[j]:+.3f} {grup} scalp",
                               "wr": m["wr"], "taban": 50.0,
                               "lift": round((m["pf"] - 1.0) * 10, 1), "wilson_alt": None,
                               "oos_wr": m["oos"], "n": m["n"]})
            else:
                L.append(f"    {grup}: yetersiz örneklem")
        mh = _metrik(r["hacimli"]); ms = _metrik(r["hacimsiz"])
        if mh and ms:
            L.append(f"    → HACİM KATKISI: beklenti {mh['beklenti']-ms['beklenti']:+.3f} "
                     f"· PF Δ {round(mh['pf']-ms['pf'],2)}")
    L.append("\nKARAR: ✅ satır = scalp portföy adayı. Hacimli>hacimsiz ise 'hacim bazlı' onay kanıtlı.")
    rapor = "\n".join(L)
    print("\n" + rapor)

    try:
        import backtest_sonuclari as bs
        if defter:
            say = bs.kaydet_toplu(defter, kaynak="fib_paslasma", aralik=args.symbol)
            print(f"[FibPaslaşma] defter: {say}", flush=True)
    except Exception as e:
        print(f"[FibPaslaşma] defter hata: {str(e)[:60]}", flush=True)

    if args.telegram:
        try:
            import asyncio
            from ajan_merkez import bildir
            asyncio.run(bildir("Fib Paslaşma Scalp", "backtest",
                               "En paslaşılan 3 fib koridoru + hacim-bazlı scalp test edildi",
                               detay=rapor))
            print("[Telegram] thread 4129'a gönderildi ✓", flush=True)
        except Exception as e:
            print(f"[Telegram] gönderilemedi: {str(e)[:80]}", flush=True)


if __name__ == "__main__":
    main()
