"""
footprint_engine.py — Gerçek Aggressor-Tabanlı CVD + Footprint (YENİ — yalnız backtest)
═══════════════════════════════════════════════════════════════════════════════════
AMAÇ: aggTrades'in buyer-maker bayrağından GERÇEK (tick-tabanlı) CVD ve footprint
delta üretir. Mevcut canlı proxy CVD'yi backtest doğruluğu için tamamlar.

⚠️ PROXY vs GERÇEK — NET AYRIM:
  - PROXY (canlı, REST-only, DOKUNULMAZ): order_flow_agent._cvd_hesapla()
      delta = (close-low)/(high-low) * volume * 2 - volume        ← "Kaygın formülü"
    OHLC'den tahmin; gerçek aggressor verisi yok. Render'da hafif kalsın diye böyle.
  - GERÇEK (bu modül, yalnız backtest): aggTrades is_buyer_maker bayrağından
    aggressor tarafı kesin bilinir. Canlı hatta GİRMEZ.

  indicator_engine.py footprint'i bilerek "veri_yok" işaretler — o davranış
  DEĞİŞTİRİLMEZ; bu modül backtest yolunda ayrı durur.

AGGRESSOR MANTIĞI (Binance aggTrades):
  is_buyer_maker == True  → alıcı maker, SATICI agresör (taker sell)  → delta -= qty
  is_buyer_maker == False → alıcı taker, ALICI agresör (taker buy)    → delta += qty

  CVD            = Σ(agresif_alış − agresif_satış) (kümülatif)
  Footprint delta= bar içinde, her fiyat seviyesinde (alış − satış)

Ağır dep (pandas) → requirements-dev.txt (lazy import). Kolon adları data_ingest
aggTrades şemasıyla aynı: timestamp, price, quantity, is_buyer_maker.
"""


def _bool_buyer_maker(seri):
    """is_buyer_maker kolonunu bool'a normalize et (True/False/0/1/'true'...)."""
    def _b(x):
        if isinstance(x, str):
            return x.strip().lower() in ("true", "1")
        return bool(x)
    return seri.map(_b)


def aggressor_delta(df):
    """
    Her trade için imzalı delta serisi döndürür:
      + : agresif alış (taker buy), − : agresif satış (taker sell).
    df: aggTrades DataFrame (timestamp, price, quantity, is_buyer_maker).
    """
    bm = _bool_buyer_maker(df["is_buyer_maker"])
    isaret = bm.map(lambda maker: -1 if maker else 1)  # maker=alıcı → satış agresör
    return df["quantity"] * isaret


def cvd_serisi(df):
    """
    Kümülatif Volume Delta (CVD) serisi.
    Döner: (delta, cvd) pandas Series ikilisi (df ile aynı index).
    """
    delta = aggressor_delta(df)
    return delta, delta.cumsum()


def cvd_toplam(df) -> float:
    """Tüm pencere için net CVD (Σ agresif alış − agresif satış)."""
    return float(aggressor_delta(df).sum())


def footprint(df, bar_ms: int = 300_000, fiyat_adim: float = None):
    """
    Bar × fiyat-seviyesi footprint delta tablosu.
    bar_ms     : bar süresi (ms). Varsayılan 5dk.
    fiyat_adim : fiyat seviyesi yuvarlama adımı (tick birleştirme). None → ham fiyat.

    Döner: DataFrame [bar_ts, fiyat, alis, satis, delta] (delta = alis − satis).
    """
    import pandas as pd  # lazy (requirements-dev.txt)
    delta = aggressor_delta(df)
    bar_ts = (df["timestamp"] // bar_ms) * bar_ms
    fiyat = df["price"]
    if fiyat_adim:
        fiyat = (fiyat / fiyat_adim).round() * fiyat_adim

    calisma = pd.DataFrame({
        "bar_ts": bar_ts.values,
        "fiyat": fiyat.values,
        "alis": delta.clip(lower=0).values,    # yalnız agresif alış hacmi
        "satis": (-delta).clip(lower=0).values,  # yalnız agresif satış hacmi
    })
    grup = calisma.groupby(["bar_ts", "fiyat"], as_index=False).agg(
        alis=("alis", "sum"), satis=("satis", "sum")
    )
    grup["delta"] = grup["alis"] - grup["satis"]
    return grup.sort_values(["bar_ts", "fiyat"]).reset_index(drop=True)


def bar_delta_ozet(df, bar_ms: int = 300_000):
    """
    Bar başına net footprint delta + POC (en yüksek toplam hacimli seviye).
    Döner: DataFrame [bar_ts, delta, hacim, poc_fiyat].
    """
    import pandas as pd  # lazy
    fp = footprint(df, bar_ms=bar_ms)
    fp["hacim"] = fp["alis"] + fp["satis"]
    satirlar = []
    for bar_ts, g in fp.groupby("bar_ts"):
        poc = g.loc[g["hacim"].idxmax(), "fiyat"]
        satirlar.append({
            "bar_ts": int(bar_ts),
            "delta": float(g["delta"].sum()),
            "hacim": float(g["hacim"].sum()),
            "poc_fiyat": float(poc),
        })
    return pd.DataFrame(satirlar)


if __name__ == "__main__":
    import sys
    import pandas as pd
    if len(sys.argv) < 2:
        print("kullanım: python footprint_engine.py <aggTrades.parquet>")
        sys.exit(1)
    df = pd.read_parquet(sys.argv[1])
    print("Net CVD:", cvd_toplam(df))
    print(bar_delta_ozet(df).head())
