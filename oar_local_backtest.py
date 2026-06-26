"""
oar_local_backtest.py — Yerel Derin-Geçmiş Backtest Runner (YENİ — yalnız LOKAL)
═══════════════════════════════════════════════════════════════════════════════
SORUN: Kiyotaka API yalnız ~1 günlük geçmiş veriyor (7+ gün = HTTP 403). Bu yüzden
canlı otonom backtest (oar_autonomous_backtest) eski günlerde VPFR alamıyor.

ÇÖZÜM: Bu runner, Kiyotaka'ya HİÇ bağlı kalmadan, Görev 1–4 modüllerini birleştirir:
  data_ingest    → Binance public dump'tan klines + aggTrades (SINIRSIZ geçmiş)
  data_integrity → veriyi backteste sokmadan doğrula
  footprint_engine → aggTrades'ten KENDİ VPFR/POC'umuzu üret (Kiyotaka yerine)
  walk_forward   → IS optimize / OOS doğrula (overfit'siz karar)

⚠️ YALNIZ LOKAL: ağır deps (pandas/pyarrow) requirements-dev.txt'tedir; Render/Railway
   runtime'ı bunu import ETMEZ. Canlı main.py hattına dokunmaz.

KULLANIM (yerel, önce data_ingest ile veri çekilmiş olmalı):
  python data_ingest.py --symbol BTCUSDT --type klines    --from 2024-01 --to 2024-06
  python data_ingest.py --symbol BTCUSDT --type aggTrades  --from 2024-01 --to 2024-06
  python oar_local_backtest.py --symbol BTCUSDT --from 2024-01 --to 2024-06

OAR sinyal mantığı (sadeleştirilmiş, deterministik):
  - Her gün önceki günün yüksek/düşük aralığından fib ekstrem seviyeleri çıkarılır.
  - Fiyat bir ekstrem fib'e değer + o günün footprint POC'u o seviyeye yakınsa → sinyal.
  - Sinyal eval_saat sonra ±esik ile WIN/LOSS/FLAT.
"""
import os
import argparse
from pathlib import Path
from datetime import datetime, timezone

# OAR ekstrem fib oranları (seed_oar_rules ile aynı mantık)
FIB_EKSTREM = {
    "alt": [-0.272, -0.618, -1.272, -1.618],   # LONG sweep bölgesi
    "ust": [1.272, 1.618, 2.272, 2.618],        # SHORT sweep bölgesi
}


# ─── Deterministik çekirdek (parquet gerektirmez — test edilebilir) ──────────
def fib_seviyeleri(low: float, high: float) -> dict:
    """Aralıktan ekstrem fib fiyat seviyeleri. {oran: fiyat}."""
    r = high - low
    out = {}
    for oran in FIB_EKSTREM["alt"] + FIB_EKSTREM["ust"]:
        out[oran] = round(low + r * oran, 2)
    return out


def vpfr_teyit(fib_fiyat: float, poc: float, tol_pct: float = 0.5) -> bool:
    """Footprint POC, fib seviyesine %tol kadar yakın mı (confluence)."""
    if not poc or not fib_fiyat:
        return False
    return abs(fib_fiyat - poc) / fib_fiyat * 100 <= tol_pct


def degerlendir(giris: float, sonraki_fiyatlar: list, yon: str,
                esik_pct: float = 0.5) -> tuple[str, float]:
    """
    Girişten sonraki fiyat serisinde ±esik ile sonucu belirle.
    Döner: (outcome, pct). outcome ∈ WIN/LOSS/FLAT.
    """
    if not sonraki_fiyatlar:
        return "FLAT", 0.0
    son = sonraki_fiyatlar[-1]
    pct = (son - giris) / giris * 100 if yon == "LONG" else (giris - son) / giris * 100
    if pct >= esik_pct:
        return "WIN", round(pct, 3)
    if pct <= -esik_pct:
        return "LOSS", round(pct, 3)
    return "FLAT", round(pct, 3)


def fib_yonu(oran: float) -> str:
    """Alt ekstrem → LONG (dip avı), üst ekstrem → SHORT (tepe avı)."""
    return "LONG" if oran < 0 else "SHORT"


# ─── Parquet yükleme (lazy pandas) ───────────────────────────────────────────
def _hist_dir() -> Path:
    from data_ingest import hist_dir
    return hist_dir()


def _parquet_oku(sembol: str, veri_tipi: str, bas: str, bit: str, borsa="binance"):
    """data_ingest layout'undan ilgili ayların parquet'lerini birleştirir."""
    import pandas as pd  # lazy
    from data_ingest import _aylar
    kok = _hist_dir() / borsa / sembol / veri_tipi
    parcalar = []
    for yil, ay in _aylar(bas, bit):
        if veri_tipi == "klines":
            govde = f"{sembol}-1m-{yil:04d}-{ay:02d}"
        else:
            govde = f"{sembol}-aggTrades-{yil:04d}-{ay:02d}"
        yol = kok / f"{yil:04d}" / f"{govde}.parquet"
        if yol.exists():
            parcalar.append(pd.read_parquet(yol))
    if not parcalar:
        return None
    return pd.concat(parcalar, ignore_index=True)


# ─── Backtest çekirdeği ──────────────────────────────────────────────────────
def calistir(sembol: str, bas: str, bit: str, eval_saat: int = 4,
             esik_pct: float = 0.5, tol_pct: float = 0.5) -> dict:
    """
    Yerel parquet'lerden OAR backtest sinyalleri üretir (Kiyotaka YOK; footprint VPFR).
    Döner: walk_forward'a verilebilecek sinyaller + özet.
    """
    import pandas as pd  # lazy
    from data_integrity import dogrula
    from footprint_engine import bar_delta_ozet

    klines = _parquet_oku(sembol, "klines", bas, bit)
    aggt = _parquet_oku(sembol, "aggTrades", bas, bit)
    if klines is None or aggt is None:
        return {"hata": "parquet yok — önce data_ingest ile klines+aggTrades çek."}

    # Görev 2 kapısı
    ok_k, rap_k = dogrula(klines, "klines", interval="1m")
    ok_a, rap_a = dogrula(aggt, "aggTrades")
    if not ok_k or not ok_a:
        return {"hata": "veri bütünlük kapısı FAIL",
                "klines_rapor": rap_k, "aggtrades_rapor": rap_a}

    # Günlük footprint POC (Görev 3 — Kiyotaka yerine kendi VPFR'imiz)
    fp = bar_delta_ozet(aggt, bar_ms=86_400_000)  # günlük bar
    poc_gun = {int(r["bar_ts"] // 86_400_000): r["poc_fiyat"] for _, r in fp.iterrows()}

    # Günlük OHLC (klines open_time ms)
    k = klines.copy()
    k["gun"] = (k["open_time"] // 86_400_000).astype(int)
    gunler = sorted(k["gun"].unique())

    sinyaller = []
    for i in range(1, len(gunler)):
        onceki, bugun = gunler[i - 1], gunler[i]
        onceki_k = k[k["gun"] == onceki]
        bugun_k = k[k["gun"] == bugun].sort_values("open_time")
        if onceki_k.empty or bugun_k.empty:
            continue
        low, high = float(onceki_k["low"].min()), float(onceki_k["high"].max())
        fibs = fib_seviyeleri(low, high)
        poc = poc_gun.get(bugun)

        closes = bugun_k["close"].tolist()
        times = bugun_k["open_time"].tolist()
        for j, (ts, fiyat) in enumerate(zip(times, closes)):
            for oran, seviye in fibs.items():
                # fib temas (±%0.1) + footprint POC confluence
                if abs(fiyat - seviye) / seviye * 100 <= 0.1 and vpfr_teyit(seviye, poc, tol_pct):
                    yon = fib_yonu(oran)
                    ileri = closes[j + 1: j + 1 + eval_saat * 60]  # 1m bar → saat*60
                    out, pct = degerlendir(fiyat, ileri, yon, esik_pct)
                    sinyaller.append({"ts": int(ts), "fib": oran, "outcome": out,
                                      "pct": pct, "yon": yon})
                    break  # gün-içi aynı bar'da tek sinyal

    return {
        "sembol": sembol, "aralik": f"{bas}..{bit}",
        "sinyal_sayisi": len(sinyaller), "sinyaller": sinyaller,
        "gun_sayisi": len(gunler),
        "veri": {"klines_satir": len(klines), "aggtrades_satir": len(aggt)},
    }


def _on_kontrol():
    """Ağır deps eksikse net yönerge ver (çıplak ModuleNotFoundError yerine)."""
    eksik = []
    for mod in ("pandas", "pyarrow"):
        try:
            __import__(mod)
        except ImportError:
            eksik.append(mod)
    if eksik:
        print("⚠ Bu LOKAL bir araçtır (Railway/canlı runtime için değil).")
        print(f"  Eksik kütüphane: {', '.join(eksik)}")
        print("  Kendi bilgisayarında kur:  pip install -r requirements-dev.txt")
        print("  Önce data_ingest.py ile klines+aggTrades verisini çekmiş olmalısın.")
        raise SystemExit(1)


def main():
    _on_kontrol()
    ap = argparse.ArgumentParser(description="OAR yerel derin-geçmiş backtest (Kiyotaka'sız)")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--from", dest="bas", required=True, help="YYYY-MM")
    ap.add_argument("--to", dest="bit", required=True, help="YYYY-MM")
    ap.add_argument("--eval", type=int, default=4, help="değerlendirme saati")
    ap.add_argument("--esik", type=float, default=0.5, help="WIN/LOSS eşiği %")
    ap.add_argument("--folds", type=int, default=4)
    args = ap.parse_args()

    res = calistir(args.symbol, args.bas, args.bit, eval_saat=args.eval, esik_pct=args.esik)
    if res.get("hata"):
        print("HATA:", res["hata"])
        return
    print(f"[LocalBT] {res['sembol']} {res['aralik']}: {res['sinyal_sayisi']} sinyal "
          f"/ {res['gun_sayisi']} gün ({res['veri']})")

    # Görev 4 — walk-forward OOS (overfit'siz karar)
    from walk_forward import walk_forward, rapor
    sg = res["sinyaller"]
    wf = walk_forward(lambda _p: sg, ["yerel"], fold_sayisi=args.folds, is_oran=0.7)
    print(rapor(wf))

    # Kalıcı kayıt (canlı sayfa istersse okuyabilir)
    import json
    out = _hist_dir() / f"local_backtest_{args.symbol}.json"
    out.write_text(json.dumps({"ozet": {k: v for k, v in res.items() if k != "sinyaller"},
                               "walk_forward": wf,
                               "tarih": datetime.now(timezone.utc).isoformat()},
                              ensure_ascii=False, indent=2))
    print(f"[LocalBT] Kaydedildi: {out}")


if __name__ == "__main__":
    main()
