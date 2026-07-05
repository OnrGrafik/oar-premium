"""
oar_sampiyon_confirm.py — ŞAMPİYON (fade+htf_vpfr) + KIRILIM-DEVAM CONFIRM birleşik backtest
═══════════════════════════════════════════════════════════════════════════════════════
SORU (kullanıcı): Hipotez sonucu doğru olsa da, ŞAMPİYONU hipotezle BİRLEŞTİRİP backtest
etmeden değiştirmeyelim. Confirm de olsa SAĞLAM olmalı.

KRİTİK: Şampiyon = FADE (ekstremde tersine dönüş). Kazanan hipotez = KIRILIM-DEVAM (Asia-high
kırıp 1.618'e gider). Bunlar ZIT. Yani "birleştirme" toplama değil, FİLTRE:
  → Kırılım-devam günlerinde ÜST ekstremi fade'leme (kırılan tepeyi shortlayıp ezilme).

Bu modül ŞAMPİYONUN KENDİ sinyal üreticisini (aday_sinyaller_uret — proxy DEĞİL, gerçek
fade net çıktıları) kullanır; ANAYASA #8: şampiyon KODUNA DOKUNMAZ, sadece okur.

3 senaryo net PF/beklenti/maxDD/OOS ile karşılaştırılır:
  A) ŞAMPİYON fade (htf_vpfr_ok)                         — mevcut durum
  B) A + CONFIRM FİLTRE (kırılım-devam günü üst fade'i AT) — hipotezle korunmuş şampiyon
  C) B + kırılım-devam kolu (o günlerde trend-LONG işlem)  — şampiyon + hipotez birlikte

confirm = genlik%1-2 + Asia-HIGH hacim-destekli kırılım (kanıtlı hipotez, klines'tan).
Yalnız B ≥ A (PF↑, maxDD↓, OOS sağlam) ise confirm şampiyona eklemeye DEĞER.

Çalıştırma:  python oar_sampiyon_confirm.py --symbol BTCUSDT,ETHUSDT --from 2019-01 --to 2025-06 --telegram
NOT: aggTrades gerektirir (şampiyon fade çıktısı için); ay ay ilerleme yazar.
"""
import argparse
from collections import deque
from datetime import datetime

from oar_local_backtest import (_klines_oku, _aggt_ay_yollari, _metrics_oku,
                                _ms_olcekle, _gun_hazirla, aday_sinyaller_uret,
                                GUN_MS, SAAT_MS)

MIN_RANGE_ALT = 1.0
MIN_RANGE_UST = 2.0
HACIM_PENCERE = 20
ASIA_BIT      = 4.0
OOS_ORAN      = 0.20


def _confirm_gunler(sembol, bas, bit):
    """
    Klines'tan (hızlı) her gün için kırılım-devam CONFIRM bayrağı:
    genlik %1-2 + Asia-HIGH hacim-destekli kırılım. Döner: set(gun_idx).
    NO-LOOKAHEAD hacim tabanı (önceki 20 gün).
    """
    import numpy as np
    k = _klines_oku(sembol, bas, bit)
    if k is None or not len(k):
        return set()
    k = k.copy()
    k["open_time"] = _ms_olcekle(k["open_time"])
    OT_MIN, OT_MAX = 1_400_000_000_000, 2_000_000_000_000
    k = k[(k["open_time"] >= OT_MIN) & (k["open_time"] < OT_MAX)]
    k["gun"]  = (k["open_time"] // GUN_MS).astype("int64")
    k["saat"] = (k["open_time"] % GUN_MS) / SAAT_MS
    confirm = set()
    hacim_taban = deque(maxlen=HACIM_PENCERE)
    for g in sorted(k["gun"].unique()):
        gk = k[k["gun"] == g]
        asia = gk[gk["saat"] < ASIA_BIT]
        post = gk[gk["saat"] >= ASIA_BIT].sort_values("open_time")
        if asia.empty or post.empty:
            continue
        a_high = float(asia["high"].max()); a_low = float(asia["low"].min())
        gun_dk_hacim = float(post["volume"].mean())
        if a_low > 0 and (MIN_RANGE_ALT <= (a_high - a_low) / a_low * 100.0 < MIN_RANGE_UST):
            hi = post["high"].to_numpy()
            taban = float(np.median(hacim_taban)) if len(hacim_taban) >= 5 else None
            if (hi >= a_high).any() and taban is not None:
                ki = int(np.argmax(hi >= a_high))
                kir_hacim = float(post["volume"].to_numpy()[ki:ki + 60].mean())
                if kir_hacim > taban:
                    confirm.add(int(g))
        hacim_taban.append(gun_dk_hacim)
    return confirm


def _metrikler(netler):
    n = len(netler)
    if not n:
        return None
    kaz = [x for x in netler if x > 0]; kyp = [x for x in netler if x <= 0]
    gk = sum(kaz); gz = abs(sum(kyp))
    pf = round(gk / gz, 2) if gz > 0 else float("inf")
    eq = pk = dd = 0.0
    for x in netler:
        eq += x; pk = max(pk, eq); dd = max(dd, pk - eq)
    return {"n": n, "wr": round(100.0 * len(kaz) / n, 1), "pf": pf,
            "beklenti": round(sum(netler) / n, 3), "maxdd": round(dd, 2),
            "toplam": round(sum(netler), 1)}


def _oos(netler_ile_gun):
    """netler_ile_gun = [(gun, net)] → (in-sample netler, oos netler)."""
    s = sorted(netler_ile_gun, key=lambda x: x[0])
    kes = int(len(s) * (1 - OOS_ORAN))
    return [x[1] for x in s[:kes]], [x[1] for x in s[kes:]]


def _senaryo_metrik(kayitlar):
    """kayitlar = [(gun, net)] → metrik + OOS beklenti."""
    netler = [x[1] for x in kayitlar]
    m = _metrikler(netler)
    if not m:
        return None
    _, oos = _oos(kayitlar)
    om = _metrikler(oos)
    m["oos_beklenti"] = om["beklenti"] if om else None
    return m


def _analiz(sembol, bas, bit):
    """Şampiyon fade adaylarını üret + confirm bayrağı → 3 senaryonun (gun,net) listeleri."""
    print(f"   [{sembol}] confirm günleri (klines) hesaplanıyor…", flush=True)
    confirm = _confirm_gunler(sembol, bas, bit)
    print(f"   [{sembol}] {len(confirm)} kırılım-devam confirm günü", flush=True)

    print(f"   [{sembol}] şampiyon adayları (klines+aggTrades) — ay ay…", flush=True)
    klines = _klines_oku(sembol, bas, bit)
    yollar = _aggt_ay_yollari(sembol, bas, bit)
    if klines is None or not yollar:
        print(f"   [{sembol}] ⚠ parquet yok — atlandı", flush=True)
        return [], [], []
    gunler = _gun_hazirla(klines, yollar, _metrics_oku(sembol, bas, bit))
    adaylar = aday_sinyaller_uret(gunler)

    A, B, C = [], [], []   # (gun, net)
    for c in adaylar:
        gun = int(c["ts"] // GUN_MS)
        # ŞAMPİYON fade: mod=fade + htf_vpfr_ok (şampiyonun tanımlayıcı filtresi)
        if c.get("mod") == "fade" and c.get("htf_vpfr_ok"):
            ust_fade = (c.get("yon") == "SHORT")   # üst ekstremi fade (short)
            A.append((gun, c["pct"]))
            # B: confirm günü + üst fade ise ATLA (kırılan tepeyi fade'leme)
            if not (gun in confirm and ust_fade):
                B.append((gun, c["pct"]))
                C.append((gun, c["pct"]))
        # C ek kolu: confirm günü trend-LONG devam işlemi (hipotez kolu)
        if (c.get("mod") == "trend" and c.get("yon") == "LONG"
                and gun in confirm):
            C.append((gun, c["pct"]))
    print(f"   [{sembol}] ✓ aday {len(adaylar)} · A={len(A)} B={len(B)} C={len(C)}", flush=True)
    return A, B, C


def _rapor(A, B, C):
    mA = _senaryo_metrik(A); mB = _senaryo_metrik(B); mC = _senaryo_metrik(C)
    L = ["═══ ŞAMPİYON + KIRILIM-DEVAM CONFIRM BİRLEŞİK BACKTEST ═══"]
    def satir(ad, m):
        if not m:
            return f"  {ad}: yetersiz veri"
        return (f"  {ad}: n{m['n']} · WR%{m['wr']} · PF {m['pf']} · beklenti {m['beklenti']:+.3f}% "
                f"· maxDD {m['maxdd']}% · toplam {m['toplam']:+.0f}% · OOS beklenti {m['oos_beklenti']}")
    L.append(satir("A) ŞAMPİYON fade (htf_vpfr)          ", mA))
    L.append(satir("B) A + CONFIRM filtre (üst fade AT)   ", mB))
    L.append(satir("C) B + kırılım-devam kolu (trend-LONG)", mC))
    # Karar
    if mA and mB:
        d_pf = (mB["pf"] - mA["pf"]) if (mA["pf"] != float("inf") and mB["pf"] != float("inf")) else None
        d_dd = mA["maxdd"] - mB["maxdd"]
        d_bek = mB["beklenti"] - mA["beklenti"]
        iyi = (d_bek >= 0 and d_dd >= 0 and (mB["oos_beklenti"] or 0) >= 0)
        L.append("")
        L.append(f"→ CONFIRM ETKİSİ (B−A): beklenti {d_bek:+.3f} · maxDD {d_dd:+.2f} "
                 f"· PF Δ {d_pf if d_pf is not None else '—'}")
        L.append("✅ CONFIRM ŞAMPİYONU İYİLEŞTİRİYOR (ekle)" if iyi
                 else "❌ CONFIRM net iyileştirmiyor — şampiyona DOKUNMA")
    L.append("\nNOT: yalnız B, A'dan İYİ (beklenti↑ VE maxDD↓ VE OOS≥0) ise confirm eklenir.")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT,ETHUSDT")
    ap.add_argument("--from", dest="bas", default="2019-01")
    ap.add_argument("--to",   dest="bit", default="2025-06")
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args()

    A, B, C = [], [], []
    for sym in [s.strip().upper() for s in args.symbol.split(",") if s.strip()]:
        print(f"[ŞampiyonConfirm] {sym} {args.bas}..{args.bit}…", flush=True)
        a, b, c = _analiz(sym, args.bas, args.bit)
        A += a; B += b; C += c

    rapor = _rapor(A, B, C)
    print("\n" + rapor)

    if args.telegram:
        try:
            import asyncio
            from ajan_merkez import bildir
            asyncio.run(bildir("Şampiyon+Confirm Backtest", "backtest",
                               "Şampiyon fade + kırılım-devam confirm birleşik testi",
                               detay=rapor))
            print("\n[Telegram] thread 4129'a gönderildi ✓", flush=True)
        except Exception as e:
            print(f"\n[Telegram] gönderilemedi: {str(e)[:80]}", flush=True)


if __name__ == "__main__":
    main()
