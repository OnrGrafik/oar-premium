"""
oar_impulse_breakout.py — Asia Impulse Kırılım → Fib Uzatma Hedefleri (LOCAL research)
═══════════════════════════════════════════════════════════════════════════════════════
KULLANICI HİPOTEZİ:
  "Asia impulse geçilirse (Asia HIGH kırılırsa) ve HACİMLER destekliyorsa, fiyat
   Asia high üstü fib 1.618'e (TP1) — hatta ekstrem 2.618'e (full TP) — geçen günlerde
   FAZLA ulaşıyor."

Bu modül bunu YEREL parquet klines üzerinde SAYISALLAŞTIRIR ve Telegram'a rapor atar:
  P(TP1=1.618 vurulur | Asia-high kırılım + hacim desteği)  ve  P(full=2.618 | ...)
  → hacim DESTEKSİZ kırılımlara kıyasla LIFT (asıl kanıt bu; ham oran yanıltır).

TANIMLAR (varsayım değil — açıkça sabit; yanlışsa buradan değiştir):
  • Asia range: UTC 00:00–04:00 high/low (oar_local_backtest ile birebir). Geçerli: genlik ≥ MIN_RANGE%.
  • Fib: fiyat = low + (high−low)·oran. Üst uzatmalar: 1.377 / 1.618 / 2.272 / 2.618 (Asia-high ÜSTÜ).
  • "Impulse geçildi" = post-Asia (UTC 04:00→gün sonu) fiyat Asia HIGH'ı (fib 1.0) ilk kez aşar.
  • "Hacim destekliyor" = kırılım saatinin dakika-başı ort. hacmi, ÖNCEKİ 20 günün post-Asia
    dakika-başı hacim medyanının üstünde (NO-LOOKAHEAD — sadece geçmiş günler).
  • TP1 = fib 1.618, FULL = fib 2.618. Kırılımdan sonra gün sonuna kadar high bu seviyeyi görürse "vuruldu".
  • Geçersizlik (opsiyonel istatistik): TP1 görülmeden fiyat Asia-high altına (fib 1.0) geri kapanırsa "fail".

OI YOK. Veri: yerel BTC/ETH parquet klines(1m). aggTrades GEREKMEZ → hızlı.
Çalıştırma:  python oar_impulse_breakout.py --symbol BTCUSDT,ETHUSDT --from 2019-01 --to 2025-06
"""
import argparse
from collections import deque, defaultdict

from oar_local_backtest import (_klines_oku, _ms_olcekle, fib_seviyeleri,
                                GUN_MS, SAAT_MS)

MIN_RANGE   = 1.0    # Asia genliği ≥ %1 → geçerli gün (şampiyon market-kapısı ile aynı eşik)
TP1_FIB     = 1.618  # TP1 uzatma
FULL_FIB    = 2.618  # ekstrem / full TP
HACIM_PENCERE = 20   # hacim tabanı: önceki N gün (no-lookahead)
ASIA_BIT    = 4.0    # UTC — post-Asia buradan başlar
MIN_N       = 20     # bir grubu raporlamak için asgari örneklem


def _wr(k, n):
    return round(100.0 * k / n, 1) if n else 0.0


def _analiz(sembol, bas, bit):
    """Her geçerli gün için kırılım + hacim + hedef sonucu üretir."""
    import pandas as pd
    import numpy as np

    print(f"   [{sembol}] klines yükleniyor…", flush=True)
    k = _klines_oku(sembol, bas, bit)
    if k is None or not len(k):
        print(f"   [{sembol}] ⚠ klines yok — atlandı", flush=True)
        return []
    k = k.copy()
    k["open_time"] = _ms_olcekle(k["open_time"])
    OT_MIN, OT_MAX = 1_400_000_000_000, 2_000_000_000_000
    k = k[(k["open_time"] >= OT_MIN) & (k["open_time"] < OT_MAX)]
    k["gun"]  = (k["open_time"] // GUN_MS).astype("int64")
    k["saat"] = (k["open_time"] % GUN_MS) / SAAT_MS
    print(f"   [{sembol}] klines yüklendi: {len(k):,} mum · günler işleniyor…", flush=True)

    kayitlar = []
    hacim_taban = deque(maxlen=HACIM_PENCERE)   # önceki günlerin post-Asia dk-başı hacmi
    gunler = sorted(k["gun"].unique())
    toplam = len(gunler)
    for idx, g in enumerate(gunler, 1):
        gk = k[k["gun"] == g]
        asia = gk[gk["saat"] < ASIA_BIT]
        post = gk[gk["saat"] >= ASIA_BIT].sort_values("open_time")
        if asia.empty or post.empty:
            continue
        a_high = float(asia["high"].max())
        a_low  = float(asia["low"].min())
        if a_low <= 0 or (a_high - a_low) / a_low * 100.0 < MIN_RANGE:
            # geçersiz gün — yine de hacim tabanını güncelle (post-Asia dk-başı hacim)
            if len(post):
                hacim_taban.append(float(post["volume"].mean()))
            continue

        fibs = fib_seviyeleri(a_low, a_high)
        tp1  = fibs[TP1_FIB]
        full = fibs[FULL_FIB]

        # Kırılım: post-Asia'da high ilk kez Asia-high'ı (fib 1.0) aşan dakika
        h = post["high"].to_numpy()
        kir_idx = np.argmax(h >= a_high) if (h >= a_high).any() else -1

        # önceki günlerin tabanı (bu günü İÇERMEZ — no-lookahead)
        taban = float(np.median(hacim_taban)) if len(hacim_taban) >= 5 else None
        # bu günün post-Asia dk-başı hacmini SONRA tabana ekle
        gun_dk_hacim = float(post["volume"].mean())

        if kir_idx >= 0:
            sonrasi = post.iloc[kir_idx:]
            # kırılım saatinin (ilk 60 dk) dk-başı hacmi
            kir_hacim = float(sonrasi["volume"].iloc[:60].mean())
            destek = (taban is not None) and (kir_hacim > taban)
            max_high = float(sonrasi["high"].max())
            # kırılımdan sonra gün sonuna kadar hedefe ulaştı mı
            tp1_vur  = bool(max_high >= tp1)
            full_vur = bool(max_high >= full)
            # maksimum ulaşılan fib katı (uzatma gücü)
            max_ext  = (max_high - a_low) / (a_high - a_low) if a_high > a_low else 0.0
            # geçersizlik: TP1'den ÖNCE Asia-high altına geri kapanış oldu mu
            c = sonrasi["close"].to_numpy(); hh = sonrasi["high"].to_numpy()
            fail_before = False
            for j in range(len(sonrasi)):
                if hh[j] >= tp1:
                    break
                if c[j] < a_high:
                    fail_before = True
                    break
            kayitlar.append({
                "gun": int(g), "destek": destek,
                "tp1": tp1_vur, "full": full_vur,
                "max_ext": round(max_ext, 3),
                "temiz_tp1": bool(tp1_vur and not fail_before),
            })

        hacim_taban.append(gun_dk_hacim)
        if idx % 365 == 0:
            print(f"      · [{sembol}] {idx}/{toplam} gün işlendi…", flush=True)

    print(f"   [{sembol}] ✓ tamamlandı: {len(kayitlar)} kırılım günü", flush=True)
    return kayitlar


def _grup_ozet(kayitlar, kosul):
    alt = [r for r in kayitlar if kosul(r)]
    n = len(alt)
    if not n:
        return None
    return {
        "n": n,
        "tp1": _wr(sum(r["tp1"] for r in alt), n),
        "full": _wr(sum(r["full"] for r in alt), n),
        "temiz_tp1": _wr(sum(r["temiz_tp1"] for r in alt), n),
        "ort_ext": round(sum(r["max_ext"] for r in alt) / n, 3),
    }


def _rapor(kayitlar):
    toplam = len(kayitlar)
    destekli   = _grup_ozet(kayitlar, lambda r: r["destek"])
    desteksiz  = _grup_ozet(kayitlar, lambda r: not r["destek"])
    tumu       = _grup_ozet(kayitlar, lambda r: True)

    satirlar = ["═══ ASIA IMPULSE KIRILIM → FİB HEDEF RAPORU ═══",
                f"Toplam Asia-high kırılım günü (Asia≥%{MIN_RANGE:g}): {toplam}"]
    if tumu:
        satirlar.append(
            f"TÜM kırılımlar (n{tumu['n']}): TP1(1.618) %{tumu['tp1']} · "
            f"FULL(2.618) %{tumu['full']} · ort.uzatma {tumu['ort_ext']}x")
    if destekli and destekli["n"] >= MIN_N:
        satirlar.append(
            f"HACİM DESTEKLİ (n{destekli['n']}): TP1 %{destekli['tp1']} · "
            f"FULL %{destekli['full']} · temiz-TP1 %{destekli['temiz_tp1']} · ort {destekli['ort_ext']}x")
    if desteksiz and desteksiz["n"] >= MIN_N:
        satirlar.append(
            f"HACİM DESTEKSİZ (n{desteksiz['n']}): TP1 %{desteksiz['tp1']} · "
            f"FULL %{desteksiz['full']} · ort {desteksiz['ort_ext']}x")
    if destekli and desteksiz and destekli["n"] >= MIN_N and desteksiz["n"] >= MIN_N:
        lift_tp1  = round(destekli["tp1"]  - desteksiz["tp1"], 1)
        lift_full = round(destekli["full"] - desteksiz["full"], 1)
        satirlar.append(
            f"→ LIFT (destekli−desteksiz): TP1 {lift_tp1:+} · FULL {lift_full:+}  "
            f"{'✅ HACİM EDGE VAR' if lift_tp1 > 3 else '❌ hacim edge yok'}")
    return "\n".join(satirlar), (destekli, desteksiz, tumu)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT,ETHUSDT")
    ap.add_argument("--from", dest="bas", required=True)
    ap.add_argument("--to",   dest="bit", required=True)
    ap.add_argument("--telegram", action="store_true", help="sonucu Telegram'a (thread 4129) at")
    args = ap.parse_args()

    tum = []
    for sym in [s.strip().upper() for s in args.symbol.split(",") if s.strip()]:
        print(f"[ImpulseKırılım] {sym} {args.bas}..{args.bit} işleniyor…", flush=True)
        tum += _analiz(sym, args.bas, args.bit)

    rapor, _ = _rapor(tum)
    print("\n" + rapor)

    # Telegram merkezi ajan kanalına raporla (araştırmacı → OAR geliştirme girdisi)
    if args.telegram:
        try:
            import asyncio
            from ajan_merkez import bildir
            asyncio.run(bildir(
                "Impulse Kırılım Araştırıcı", "research",
                rapor.split("\n", 1)[0] + " — detay altta",
                detay=rapor))
            print("\n[Telegram] rapor thread 4129'a gönderildi ✓", flush=True)
        except Exception as e:
            print(f"\n[Telegram] gönderilemedi: {str(e)[:80]}", flush=True)


if __name__ == "__main__":
    main()
