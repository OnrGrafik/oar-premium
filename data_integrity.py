"""
data_integrity.py — Veri Bütünlük Kapısı (YENİ — yalnız LOKAL/backtest)
═══════════════════════════════════════════════════════════════════════════
AMAÇ: Hiçbir veri indikatöre/backteste girmeden önce doğrulansın.

⚠️ CANLI HATTA DOKUNMAZ: yalnız data_ingest çıktısını ve backtest yolunu
   kapılar. main.py / canlı agentlar bu modülü import ETMEZ.
   Ağır dep (pandas) → requirements-dev.txt (lazy import).

ANA FONKSİYON:
    dogrula(df, veri_tipi) -> (ok: bool, rapor: dict)

KONTROLLER:
  - eksiksizlik : beklenen vs gelen bar/tick, zaman boşlukları (gap)
  - tekillik    : duplicate timestamp
  - sıra        : monotonik artan timestamp, gelecek-tarihli kayıt yok
  - OHLC tutarlılık : low ≤ open/close ≤ high (yalnız klines)
  - hacim       : negatif / sıfır hacim
  - outlier     : fat-finger fiyat sıçraması işaretleme
  - aggTrades   : buyer-maker etiketi mevcut ve tutarlı (bool/0-1)

Kolon adları data_ingest.py şemasıyla aynıdır:
  klines    → open_time, open, high, low, close, volume
  aggTrades → timestamp, price, quantity, is_buyer_maker
"""
from datetime import datetime, timezone

# Beklenen bar aralıkları (ms) — eksiksizlik kontrolü için.
INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000,
    "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
}

# Ardışık tick/bar arası fiyatın bu oranı aşan sıçraması = olası fat-finger.
OUTLIER_ESIK = 0.10  # %10


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _bos_rapor() -> dict:
    return {"hatalar": [], "uyarilar": [], "metrikler": {}}


def dogrula(df, veri_tipi: str, interval: str = "1m") -> tuple[bool, dict]:
    """
    df: pandas.DataFrame (data_ingest çıktısı şeması).
    veri_tipi: 'klines' | 'aggTrades'.
    Döner: (ok, rapor). ok=False ise rapor['hatalar'] dolu.
    """
    rapor = _bos_rapor()
    if veri_tipi not in ("klines", "aggTrades"):
        rapor["hatalar"].append(f"bilinmeyen veri_tipi: {veri_tipi}")
        return False, rapor

    n = len(df)
    rapor["metrikler"]["satir"] = n
    if n == 0:
        rapor["hatalar"].append("boş veri (0 satır)")
        return False, rapor

    ts_kol = "open_time" if veri_tipi == "klines" else "timestamp"
    if ts_kol not in df.columns:
        rapor["hatalar"].append(f"zaman kolonu yok: {ts_kol}")
        return False, rapor

    ts = df[ts_kol]

    # ── Sıra: monotonik artan ──────────────────────────────────────────────
    if not ts.is_monotonic_increasing:
        rapor["hatalar"].append("timestamp monotonik artan değil (sıra bozuk)")

    # ── Tekillik ───────────────────────────────────────────────────────────
    dup = int(ts.duplicated().sum())
    if dup:
        rapor["hatalar"].append(f"{dup} duplicate timestamp")
    rapor["metrikler"]["duplicate"] = dup

    # ── Gelecek-tarihli kayıt ──────────────────────────────────────────────
    # Binance ms epoch; aggTrades 2025+ bazı dumplar µs olabilir → tolerans.
    tampon = 2 * 86_400_000  # 2 gün ileri tolerans
    gelecek = int((ts > _now_ms() + tampon).sum())
    if gelecek:
        rapor["hatalar"].append(f"{gelecek} gelecek-tarihli kayıt")
    rapor["metrikler"]["gelecek_tarihli"] = gelecek

    if veri_tipi == "klines":
        _dogrula_klines(df, rapor, interval)
    else:
        _dogrula_aggtrades(df, rapor)

    ok = len(rapor["hatalar"]) == 0
    rapor["ok"] = ok
    return ok, rapor


def _dogrula_klines(df, rapor: dict, interval: str):
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    v = df["volume"]

    # OHLC tutarlılık: low ≤ min(open,close) ve high ≥ max(open,close), low ≤ high
    bozuk = int(
        ((l > o) | (l > c) | (h < o) | (h < c) | (l > h)).sum()
    )
    if bozuk:
        rapor["hatalar"].append(f"{bozuk} bar OHLC tutarsız (low/high ihlali)")

    # Negatif / sıfır hacim
    neg = int((v < 0).sum())
    sifir = int((v == 0).sum())
    if neg:
        rapor["hatalar"].append(f"{neg} negatif hacim")
    if sifir:
        rapor["uyarilar"].append(f"{sifir} sıfır hacimli bar")

    # Eksiksizlik: beklenen bar sayısı (ilk→son / interval) vs gelen
    step = INTERVAL_MS.get(interval)
    if step:
        ot = df["open_time"]
        beklenen = int((ot.iloc[-1] - ot.iloc[0]) // step) + 1
        eksik = beklenen - len(df)
        rapor["metrikler"]["beklenen_bar"] = beklenen
        rapor["metrikler"]["eksik_bar"] = eksik
        if eksik > 0:
            rapor["hatalar"].append(f"{eksik} bar eksik (gap), beklenen {beklenen}")
        # Gap konumu (ilk birkaç)
        farklar = ot.diff().dropna()
        gap_sayisi = int((farklar > step).sum())
        if gap_sayisi:
            rapor["uyarilar"].append(f"{gap_sayisi} zaman boşluğu noktası")

    # Fat-finger: ardışık kapanışta %ESIK üzeri sıçrama
    _outlier_isaretle(c, rapor)


def _dogrula_aggtrades(df, rapor: dict):
    p = df["price"]
    q = df["quantity"]

    neg = int((q <= 0).sum())
    if neg:
        rapor["hatalar"].append(f"{neg} sıfır/negatif miktarlı trade")

    if (p <= 0).any():
        rapor["hatalar"].append("sıfır/negatif fiyat")

    # buyer-maker etiketi mevcut ve tutarlı mı (bool ya da 0/1)
    if "is_buyer_maker" not in df.columns:
        rapor["hatalar"].append("is_buyer_maker etiketi yok (aggressor tarafı kurulamaz)")
    else:
        bm = df["is_buyer_maker"]
        # Boş değer var mı?
        bos = int(bm.isna().sum())
        if bos:
            rapor["hatalar"].append(f"{bos} trade'de buyer_maker boş")
        # Değer kümesi {True,False} / {0,1} / {'true','false'} dışında mı?
        gecerli = {True, False, 0, 1, "true", "false", "True", "False"}
        gecersiz = set(bm.dropna().unique()) - gecerli
        if gecersiz:
            rapor["hatalar"].append(f"buyer_maker tutarsız değerler: {list(gecersiz)[:5]}")

    _outlier_isaretle(p, rapor)


def _outlier_isaretle(seri, rapor: dict):
    """Ardışık değerde %OUTLIER_ESIK üzeri sıçrama → fat-finger uyarısı."""
    if len(seri) < 2:
        return
    oran = seri.pct_change().abs()
    outlier = int((oran > OUTLIER_ESIK).sum())
    rapor["metrikler"]["outlier"] = outlier
    if outlier:
        rapor["uyarilar"].append(
            f"{outlier} olası fat-finger sıçraması (>%{OUTLIER_ESIK*100:.0f})"
        )


if __name__ == "__main__":
    import sys
    import pandas as pd
    if len(sys.argv) < 3:
        print("kullanım: python data_integrity.py <parquet> <klines|aggTrades>")
        sys.exit(1)
    df = pd.read_parquet(sys.argv[1])
    ok, rap = dogrula(df, sys.argv[2])
    import json
    print(json.dumps(rap, ensure_ascii=False, indent=2))
    print("SONUÇ:", "OK" if ok else "BOZUK")
