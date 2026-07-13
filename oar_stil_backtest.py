"""
oar_stil_backtest.py — Kullanıcı Stil Senaryoları 3+4 Backtest'i (dokümandan koda)
═══════════════════════════════════════════════════════════════════════════════════════
KAYNAK: docs/kural_kaynak/Oar_hacim_kullanimi.md (kullanıcının kendi dokümanı).
Ajanlar artık kullanıcı dokümanından kural çıkarıp test ediyor — LLM lafı değil, SAYI.

DOKÜMANDAN ÇIKAN KURALLAR (birebir):
  STİL-3 "377 dönüşü": "Asia-High/Low geçilirse 377 fib seviyesine temasta zayıflık ya da
    likidite alımı ile Asia Range İÇİNDEKİ seviyelere kadar shortlanır."
    → Kod: Asia-High kırıldıktan SONRA fiyat 1.377'ye ilk temas → SHORT, TP=0.5 (EQ),
      SL=1.618 (bir üst fib). Ayna: Asia-Low kırık + -0.377 temas → LONG, TP=0.5, SL=-0.618.
  STİL-4 "EQ kalıcılık": "Fiyat asia-EQ seviyesinde güçleniyor... long alıyorum,
    stop VAL/vwap alt bandı."
    → Kod: post-Asia fiyat EQ(0.5) ÜSTÜNDE ≥30dk kalıcı (kapanışlar) + hacim desteği
      → LONG, TP=1.0 (Asia High), SL=0.0 (Asia Low; VAL proxy'si — klines'ta VAL yok).
      Ayna: EQ altında kalıcı → SHORT, TP=0.0, SL=1.0.

Her stil AYRI rapor: n/WR/PF/beklenti/maxDD/OOS + $1000 equity. Kazanan → backtest
defterine yazılır. NO-LOOKAHEAD, bar-bar high/low, aynı barda SL önce (kötümser), fee dahil.

Çalıştırma:  python oar_stil_backtest.py --symbol BTCUSDT,ETHUSDT --from 2019-01 --to 2025-06 --telegram
"""
import argparse
from collections import deque
from datetime import datetime, timezone

from oar_local_backtest import (_klines_oku, _ms_olcekle, fib_seviyeleri,
                                FEE_PCT, SLIP_PCT, GUN_MS, SAAT_MS)

MIN_RANGE     = 1.0
ASIA_BIT      = 4.0
HACIM_PENCERE = 20
KALICILIK_DK  = 30      # EQ kalıcılık: ≥30 dk (30 × 1m bar) aynı tarafta kapanış
OOS_ORAN      = 0.20
MIN_N         = 40


def _islem(giris, tp, sl, hi, lo, yon):
    """Bar-bar TP/SL (aynı barda SL önce — kötümser). Net % (fee dahil)."""
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
        return {"stil3": [], "stil4": []}
    k = k.copy()
    k["open_time"] = _ms_olcekle(k["open_time"])
    OT_MIN, OT_MAX = 1_400_000_000_000, 2_000_000_000_000
    k = k[(k["open_time"] >= OT_MIN) & (k["open_time"] < OT_MAX)]
    k["gun"]  = (k["open_time"] // GUN_MS).astype("int64")
    k["saat"] = (k["open_time"] % GUN_MS) / SAAT_MS

    stil3, stil4 = [], []
    hacim_taban = deque(maxlen=HACIM_PENCERE)
    son_ay = None
    for g in sorted(k["gun"].unique()):
        ay = datetime.fromtimestamp(int(g) * 86400, timezone.utc).strftime("%Y-%m")
        if ay != son_ay:
            print(f"      · [{sembol}] {ay} analiz ediliyor…", flush=True)
            son_ay = ay
        gk = k[k["gun"] == g]
        asia = gk[gk["saat"] < ASIA_BIT]
        post = gk[gk["saat"] >= ASIA_BIT].sort_values("open_time")
        if asia.empty or len(post) < 60:
            continue
        a_h = float(asia["high"].max()); a_l = float(asia["low"].min())
        gun_dk_hacim = float(post["volume"].mean())
        if a_l <= 0 or (a_h - a_l) / a_l * 100.0 < MIN_RANGE:   # Asia ≥%1 kuralı
            hacim_taban.append(gun_dk_hacim)
            continue
        fibs = fib_seviyeleri(a_l, a_h)
        hi = post["high"].to_numpy(); lo = post["low"].to_numpy()
        cl = post["close"].to_numpy(); vol = post["volume"].to_numpy()
        taban = float(np.median(hacim_taban)) if len(hacim_taban) >= 5 else None
        ts0 = int(post["open_time"].iloc[0])

        # ── STİL-3: kırılım sonrası 1.377 / -0.377 dönüşü ──
        brk_hi = int(np.argmax(hi >= a_h)) if (hi >= a_h).any() else -1
        if brk_hi >= 0:
            seg = hi[brk_hi:]
            t = int(np.argmax(seg >= fibs[1.377])) if (seg >= fibs[1.377]).any() else -1
            if t >= 0:
                j = brk_hi + t
                net = _islem(fibs[1.377], fibs[0.5], fibs[1.618], hi[j:], lo[j:], "SHORT")
                stil3.append((ts0, net))
        brk_lo = int(np.argmax(lo <= a_l)) if (lo <= a_l).any() else -1
        if brk_lo >= 0:
            seg = lo[brk_lo:]
            t = int(np.argmax(seg <= fibs[-0.377])) if (seg <= fibs[-0.377]).any() else -1
            if t >= 0:
                j = brk_lo + t
                net = _islem(fibs[-0.377], fibs[0.5], fibs[-0.618], hi[j:], lo[j:], "LONG")
                stil3.append((ts0, net))

        # ── STİL-4: EQ (0.5) kalıcılık + hacim desteği ──
        eq = fibs[0.5]
        if len(cl) > KALICILIK_DK + 5:
            ust = cl[:KALICILIK_DK] > eq
            alt = cl[:KALICILIK_DK] < eq
            hac_ok = (taban is not None) and (float(vol[:KALICILIK_DK].mean()) > taban)
            j = KALICILIK_DK
            if ust.all() and hac_ok:      # EQ üstü 30dk kalıcı → LONG
                net = _islem(cl[j], fibs[1.0], fibs[0.0], hi[j:], lo[j:], "LONG")
                stil4.append((ts0, net))
            elif alt.all() and hac_ok:    # EQ altı 30dk kalıcı → SHORT
                net = _islem(cl[j], fibs[0.0], fibs[1.0], hi[j:], lo[j:], "SHORT")
                stil4.append((ts0, net))
        hacim_taban.append(gun_dk_hacim)

    print(f"   [{sembol}] ✓ stil3(377 dönüş)={len(stil3)} · stil4(EQ kalıcılık)={len(stil4)}", flush=True)
    return {"stil3": stil3, "stil4": stil4}


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

    S = {"stil3": [], "stil4": []}
    for sym in [s.strip().upper() for s in args.symbol.split(",") if s.strip()]:
        print(f"[StilBacktest] {sym} {args.bas}..{args.bit}…", flush=True)
        r = _analiz(sym, args.bas, args.bit)
        S["stil3"] += r["stil3"]; S["stil4"] += r["stil4"]

    L = ["═══ KULLANICI STİL SENARYOLARI 3+4 (dokümandan koda) ═══"]
    adlar = {"stil3": "STİL-3: kırılım sonrası 1.377/-0.377 dönüşü (TP=EQ, SL=next-fib)",
             "stil4": "STİL-4: Asia-EQ 30dk kalıcılık + hacim (TP=karşı ekstrem, SL=diğer uç)"}
    sonuc = {}
    for st, kayitlar in S.items():
        m = _metrik(kayitlar)
        sonuc[st] = m
        if m:
            iy = "✅" if (m["pf"] >= 1.3 and m["beklenti"] > 0 and (m["oos"] or 0) >= 0) else "  "
            L.append(f"\n{iy} {adlar[st]}\n    n{m['n']} · WR%{m['wr']} · PF {m['pf']} · "
                     f"beklenti {m['beklenti']:+.3f}% · maxDD {m['maxdd']}% · "
                     f"toplam {m['toplam']:+.0f}% · OOS {m['oos']}")
        else:
            L.append(f"\n  {adlar[st]}: yetersiz örneklem (n<{MIN_N})")
    L.append("\nKARAR: PF≥1.3 + beklenti>0 + OOS≥0 = portföy adayı (✅). "
             "Aksi = deftere 'elendi' yazılır, tekrar kovalanmaz.")
    rapor = "\n".join(L)
    print("\n" + rapor)

    # Backtest defterine yaz (kurumsal hafıza — ajanlar tekrar test etmesin)
    try:
        import backtest_sonuclari as bs
        tum = []
        for st, m in sonuc.items():
            if m:
                lift_proxy = round((m["pf"] - 1.0) * 10, 1)   # PF-tabanlı kaba skor (defter için)
                tum.append({"ad": f"[stil] {adlar[st]}", "wr": m["wr"], "taban": 50.0,
                            "lift": lift_proxy, "wilson_alt": None, "oos_wr": m["oos"],
                            "n": m["n"]})
        if tum:
            say = bs.kaydet_toplu(tum, kaynak="stil_backtest", aralik=args.symbol)
            print(f"[StilBacktest] defter: {say}", flush=True)
    except Exception as e:
        print(f"[StilBacktest] defter hata: {str(e)[:60]}", flush=True)

    if args.telegram:
        try:
            import asyncio
            from ajan_merkez import bildir
            asyncio.run(bildir("Stil Backtest (doküman→kod)", "backtest",
                               "Kullanıcı stilleri 3+4 test edildi", detay=rapor))
            print("[Telegram] thread 4129'a gönderildi ✓", flush=True)
        except Exception as e:
            print(f"[Telegram] gönderilemedi: {str(e)[:80]}", flush=True)


if __name__ == "__main__":
    main()
