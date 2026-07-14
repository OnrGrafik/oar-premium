"""
oar_micro_scalp.py — Asia-Dışı Fib Seviyelerinde İLK TEMAS Micro-Scalp Backtest'i
═══════════════════════════════════════════════════════════════════════════════════════
KULLANICI İSTEĞİ: "Asia range dışındaki fib seviyelerinde ilk temasta işlem alan backtest"
— micro scalp: her uzatma fib'i bir S/R gibi; İLK temasta range yönüne fade, hedef BİR fib.

KURALLAR (mekanik, netleştirilebilir):
  • Asia ≥ %1 (genel kural). Fibler Asia'ya çekilir.
  • ÜST uzatmalar (1.377, 1.618, 2.272, 2.618): post-Asia fiyat seviyeye İLK temas
    → SHORT. TP = BİR ALT fib (micro hedef). SL = BİR ÜST fib.
  • ALT uzatmalar (-0.377, -0.618, -1.272, -1.618): İLK temas → LONG.
    TP = BİR ÜST fib. SL = BİR ALT fib.
  • Her seviye günde 1 kez (ilk temas). Bar-bar, aynı barda SL önce (kötümser), fee dahil.
  • Rapor SEVİYE BAZINDA: hangi fib'de ilk-temas fade'i çalışıyor, hangisinde çalışmıyor.

NO-LOOKAHEAD, yalnız klines (hızlı). Sonuç backtest defterine yazılır.
Çalıştırma:  python oar_micro_scalp.py --symbol BTCUSDT,ETHUSDT --from 2019-01 --to 2025-06 --telegram
"""
import argparse
from datetime import datetime, timezone

from oar_local_backtest import (_klines_oku, _ms_olcekle, fib_seviyeleri,
                                FEE_PCT, SLIP_PCT, GUN_MS, SAAT_MS, OAR_FIB)

MIN_RANGE = 1.0
ASIA_BIT  = 4.0
OOS_ORAN  = 0.20
MIN_N     = 40

UST = [1.377, 1.618, 2.272, 2.618]        # SHORT bölgesi (bir altı TP, bir üstü SL)
ALT = [-0.377, -0.618, -1.272, -1.618]    # LONG bölgesi
SIRALI = sorted(OAR_FIB)                   # komşu fib bulmak için


def _komsu(oran, yon):
    """Bir fib'in TP/SL komşuları: yon='SHORT' → TP=bir alt, SL=bir üst (LONG tersi)."""
    i = SIRALI.index(oran)
    alt = SIRALI[i - 1] if i > 0 else None
    ust = SIRALI[i + 1] if i < len(SIRALI) - 1 else None
    return (alt, ust) if yon == "SHORT" else (ust, alt)


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


def _analiz(sembol, bas, bit):
    import numpy as np
    print(f"   [{sembol}] klines yükleniyor…", flush=True)
    k = _klines_oku(sembol, bas, bit)
    if k is None or not len(k):
        print(f"   [{sembol}] ⚠ klines yok", flush=True)
        return {}
    k = k.copy()
    k["open_time"] = _ms_olcekle(k["open_time"])
    OT_MIN, OT_MAX = 1_400_000_000_000, 2_000_000_000_000
    k = k[(k["open_time"] >= OT_MIN) & (k["open_time"] < OT_MAX)]
    k["gun"]  = (k["open_time"] // GUN_MS).astype("int64")
    k["saat"] = (k["open_time"] % GUN_MS) / SAAT_MS

    seviye_kayit = {}   # oran → [(ts, net)]
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
        fibs = fib_seviyeleri(a_l, a_h)
        hi = post["high"].to_numpy(); lo = post["low"].to_numpy()
        ts0 = int(post["open_time"].iloc[0])

        for oran in UST:                                   # SHORT: ilk temas
            sev = fibs[oran]
            t = int(np.argmax(hi >= sev)) if (hi >= sev).any() else -1
            if t < 0:
                continue
            tp_o, sl_o = _komsu(oran, "SHORT")
            if tp_o is None or sl_o is None:
                continue
            net = _islem(sev, fibs[tp_o], fibs[sl_o], hi[t:], lo[t:], "SHORT")
            seviye_kayit.setdefault(oran, []).append((ts0, net))
        for oran in ALT:                                   # LONG: ilk temas
            sev = fibs[oran]
            t = int(np.argmax(lo <= sev)) if (lo <= sev).any() else -1
            if t < 0:
                continue
            tp_o, sl_o = _komsu(oran, "LONG")
            if tp_o is None or sl_o is None:
                continue
            net = _islem(sev, fibs[tp_o], fibs[sl_o], hi[t:], lo[t:], "LONG")
            seviye_kayit.setdefault(oran, []).append((ts0, net))

    print(f"   [{sembol}] ✓ {sum(len(v) for v in seviye_kayit.values())} micro-scalp işlemi", flush=True)
    return seviye_kayit


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
    kes = int(n * (1 - OOS_ORAN))
    oos = netler[kes:]
    oos_b = round(sum(oos) / len(oos), 3) if oos else None
    return {"n": n, "wr": round(100 * len(kaz) / n, 1), "pf": pf,
            "beklenti": round(sum(netler) / n, 3), "maxdd": round(dd, 2),
            "toplam": round(sum(netler), 1), "oos": oos_b}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT,ETHUSDT")
    ap.add_argument("--from", dest="bas", default="2019-01")
    ap.add_argument("--to",   dest="bit", default="2025-06")
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args()

    tum = {}
    for sym in [s.strip().upper() for s in args.symbol.split(",") if s.strip()]:
        print(f"[MicroScalp] {sym} {args.bas}..{args.bit}…", flush=True)
        r = _analiz(sym, args.bas, args.bit)
        for oran, kayitlar in r.items():
            tum.setdefault(oran, []).extend(kayitlar)

    L = ["═══ MICRO-SCALP: ASIA-DIŞI FİB İLK TEMAS (TP=1 fib, SL=1 fib) ═══",
         "Seviye bazında — hangi fib'de ilk-temas fade'i çalışıyor:"]
    defter_kayitlari = []
    for oran in UST + ALT:
        m = _metrik(tum.get(oran, []))
        yon = "SHORT" if oran in UST else "LONG"
        if m:
            iy = "✅" if (m["pf"] >= 1.3 and m["beklenti"] > 0 and (m["oos"] or 0) >= 0) else "  "
            L.append(f"{iy} fib {oran:+.3f} ({yon}): n{m['n']} · WR%{m['wr']} · PF {m['pf']} · "
                     f"beklenti {m['beklenti']:+.3f}% · maxDD {m['maxdd']}% · OOS {m['oos']}")
            defter_kayitlari.append({"ad": f"[micro] fib {oran:+.3f} ilk-temas {yon} (TP/SL=1 fib)",
                                     "wr": m["wr"], "taban": 50.0,
                                     "lift": round((m["pf"] - 1.0) * 10, 1), "wilson_alt": None,
                                     "oos_wr": m["oos"], "n": m["n"]})
        else:
            L.append(f"  fib {oran:+.3f} ({yon}): yetersiz örneklem")
    L.append("\nKARAR: ✅ (PF≥1.3 + beklenti>0 + OOS≥0) = micro-scalp portföy adayı. "
             "Fibler S/R gibi — hangi seviyenin gerçekten tuttuğunu bu tablo söyler.")
    rapor = "\n".join(L)
    print("\n" + rapor)

    try:
        import backtest_sonuclari as bs
        if defter_kayitlari:
            say = bs.kaydet_toplu(defter_kayitlari, kaynak="micro_scalp", aralik=args.symbol)
            print(f"[MicroScalp] defter: {say}", flush=True)
    except Exception as e:
        print(f"[MicroScalp] defter hata: {str(e)[:60]}", flush=True)

    if args.telegram:
        try:
            import asyncio
            from ajan_merkez import bildir
            asyncio.run(bildir("Micro-Scalp (fib ilk temas)", "backtest",
                               "Asia-dışı fib ilk-temas micro-scalp test edildi", detay=rapor))
            print("[Telegram] thread 4129'a gönderildi ✓", flush=True)
        except Exception as e:
            print(f"[Telegram] gönderilemedi: {str(e)[:80]}", flush=True)


if __name__ == "__main__":
    main()
