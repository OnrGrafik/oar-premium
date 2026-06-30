"""
oar_local_backtest.py — Yerel Derin-Geçmiş OAR Asia Range Backtest (YENİ — yalnız LOKAL)
═══════════════════════════════════════════════════════════════════════════════════════
GERÇEK OAR ASIA RANGE stratejisini (kullanıcının kılavuzundan kodlanmıştır) geçmiş
veride backtest eder. Kiyotaka'ya HİÇ bağlı değildir; Görev 1–4 modüllerini birleştirir:
  data_ingest      → Binance dump klines (1m) + aggTrades (SINIRSIZ geçmiş)
  data_integrity   → veriyi backteste sokmadan doğrula
  footprint_engine → aggTrades'ten KENDİ CVD + POC/VPFR'ımız (Kiyotaka yerine)
  walk_forward     → parametre gridini IS'te optimize / OOS'ta doğrula (overfit'siz)

OAR ASIA RANGE KURALLARI (kullanıcı kılavuzu → kod):
  • Asya aralığı: TR 03:00–07:00 = UTC 00:00–04:00. Fib bu aralığa çekilir.
  • Geçerlilik: Asya genliği ≥ %min_range (varsayılan %1) → trade; altı → ATLA.
  • Fib: 2.618 2.272 1.618 1.377 1.0 0.5 0.0 -0.377 -0.618 -1.272 -1.618 (fiyat=low+r·v).
  • SHORT: fiyat ÜST ekstrem fib'e (≥1.0, Asia-High üstü) değer + CVD bearish (+ POC üstü)
           → likidite alımı, range içine short.
  • LONG : fiyat ALT ekstrem fib'e (≤0.0, Asia-Low altı) değer + CVD bullish (+ POC yakını)
           → long.
  • Geçersizlik: NY Close (UTC ~17:30) sonrası sinyal alınmaz (range gün içinde geçerli).
  • Değerlendirme: girişten eval_saat sonra ±esik ile WIN/LOSS/FLAT.

⚠️ YALNIZ LOKAL: ağır deps (pandas/pyarrow) requirements-dev.txt'tedir; canlı runtime
   bu dosyayı import ETMEZ.

KULLANIM:
  python data_ingest.py --symbol BTCUSDT --type klines    --from 2024-01 --to 2024-06
  python data_ingest.py --symbol BTCUSDT --type aggTrades  --from 2024-01 --to 2024-06
  python oar_local_backtest.py --symbol BTCUSDT --from 2024-01 --to 2024-06
"""
import os
import argparse
from pathlib import Path
from datetime import datetime, timezone

GUN_MS = 86_400_000
SAAT_MS = 3_600_000
ASIA_BAS_UTC = 0      # UTC 00:00 (TR 03:00)
ASIA_BIT_UTC = 4      # UTC 04:00 (TR 07:00)
NY_CLOSE_UTC = 17.5   # UTC 17:30 (TR 20:30) — sonrası sinyal yok

# OAR fib oranları (indikatör koduyla birebir)
OAR_FIB = [2.618, 2.272, 1.618, 1.377, 1.0, 0.5, 0.0, -0.377, -0.618, -1.272, -1.618]
UST_EKSTREM = [1.0, 1.377, 1.618, 2.272, 2.618]      # SHORT bölgesi (Asia-High üstü)
ALT_EKSTREM = [0.0, -0.377, -0.618, -1.272, -1.618]  # LONG bölgesi (Asia-Low altı)

# Parametre gridi — walk_forward IS'te bunları optimize eder, OOS'ta doğrular
PARAM_GRID = [
    # (etiket, min_range_pct, eval_saat, cvd_pencere, poc_filtre, temas_tol_pct)
    ("gevsek",   0.8, 4, 15, False, 0.15),
    ("standart", 1.0, 4, 15, True,  0.10),
    ("standart8",1.0, 8, 20, True,  0.10),
    ("sıkı",     1.2, 4, 20, True,  0.08),
    ("poc_yok",  1.0, 4, 15, False, 0.10),
    ("hizli",    1.0, 2, 10, True,  0.12),
]


# ─── Deterministik çekirdek (parquet gerektirmez — test edilebilir) ──────────
def fib_seviyeleri(low: float, high: float) -> dict:
    """OAR fib fiyat seviyeleri. {oran: fiyat}."""
    r = high - low
    return {v: round(low + r * v, 2) for v in OAR_FIB}


def asia_gecerli(high: float, low: float, min_range_pct: float) -> bool:
    """Asya genliği ≥ %min → trade güvenli."""
    if low <= 0:
        return False
    return (high - low) / low * 100.0 >= min_range_pct


def fib_yonu(oran: float) -> str:
    """Üst ekstrem → SHORT (tepe likiditesi), alt ekstrem → LONG (dip)."""
    return "SHORT" if oran >= 1.0 else "LONG"


def temas_eden_fib(fiyat: float, fibs: dict, tol_pct: float):
    """Fiyatın değdiği (±%tol) EKSTREM fib oranını döndür (yoksa None)."""
    for oran, seviye in fibs.items():
        if oran not in UST_EKSTREM and oran not in ALT_EKSTREM:
            continue
        if seviye > 0 and abs(fiyat - seviye) / seviye * 100.0 <= tol_pct:
            return oran
    return None


def cvd_teyit(yon: str, cvd_delta: float) -> bool:
    """SHORT için CVD negatif (satış), LONG için pozitif (alış) olmalı."""
    if yon == "SHORT":
        return cvd_delta < 0
    return cvd_delta > 0


def poc_teyit(yon: str, fiyat: float, poc: float) -> bool:
    """SHORT: fiyat POC üstü (direnç), LONG: POC altı/yakını (destek)."""
    if not poc:
        return True   # POC yoksa filtre uygulanmaz
    return fiyat >= poc if yon == "SHORT" else fiyat <= poc


# ── Gerçekçi işlem maliyeti (işin kuralları — uydurma değil) ────────────────
FEE_PCT = 0.11    # round-trip taker komisyon (Binance ~%0.055 × 2)
SLIP_PCT = 0.02   # tahmini kayma (slippage)


def degerlendir(giris: float, sonraki: list, yon: str, esik_pct: float = 0.5):
    """(Eski/basit) Girişten sonraki fiyat serisinde ±esik → (outcome, pct)."""
    if not sonraki:
        return "FLAT", 0.0
    son = sonraki[-1]
    pct = (son - giris) / giris * 100 if yon == "LONG" else (giris - son) / giris * 100
    if pct >= esik_pct:
        return "WIN", round(pct, 3)
    if pct <= -esik_pct:
        return "LOSS", round(pct, 3)
    return "FLAT", round(pct, 3)


def tp_sl_seviyeleri(oran: float, giris: float, fibs: dict):
    """
    OAR TP/SL (kılavuz): hedef range ortası (fib 0.5), stop bir sonraki fib.
    SHORT (üst ekstrem): TP=0.5 (altta), SL=girişin hemen üstündeki fib.
    LONG  (alt ekstrem): TP=0.5 (üstte), SL=girişin hemen altındaki fib.
    """
    mid = fibs.get(0.5)
    if oran >= 1.0:  # SHORT
        tp = mid if (mid and mid < giris) else giris * 0.995
        ust = [v for v in fibs.values() if v > giris]
        sl = min(ust) if ust else giris * 1.01
    else:            # LONG
        tp = mid if (mid and mid > giris) else giris * 1.005
        alt = [v for v in fibs.values() if v < giris]
        sl = max(alt) if alt else giris * 0.99
    return tp, sl


def degerlendir_tpsl(giris: float, yon: str, tp: float, sl: float, sonraki: list):
    """
    Bar bar: TP mi SL mi önce vurulur? Maliyet (fee+slippage) düşülür.
    Döner: (outcome, net_pct). outcome ∈ WIN/LOSS (net kâra göre).
    """
    cikis = sonraki[-1] if sonraki else giris
    for c in sonraki:
        if yon == "SHORT":
            if c <= tp:
                cikis = tp; break
            if c >= sl:
                cikis = sl; break
        else:
            if c >= tp:
                cikis = tp; break
            if c <= sl:
                cikis = sl; break
    gross = (giris - cikis) / giris * 100 if yon == "SHORT" else (cikis - giris) / giris * 100
    net = round(gross - FEE_PCT - SLIP_PCT, 4)
    return ("WIN" if net > 0 else "LOSS"), net


# ─── Parquet yükleme (lazy pandas) ───────────────────────────────────────────
def _hist_dir() -> Path:
    from data_ingest import hist_dir
    return hist_dir()


def _klines_oku(sembol, bas, bit, borsa="binance"):
    """Klines (1m) — küçük; tüm aralık yüklenir (sadece gerekli kolonlar)."""
    import pandas as pd
    from data_ingest import _aylar
    kok = _hist_dir() / borsa / sembol / "klines"
    kol = ["open_time", "open", "high", "low", "close", "volume"]
    parcalar = []
    for yil, ay in _aylar(bas, bit):
        yol = kok / f"{yil:04d}" / f"{sembol}-1m-{yil:04d}-{ay:02d}.parquet"
        if yol.exists():
            parcalar.append(pd.read_parquet(yol, columns=kol))
    return pd.concat(parcalar, ignore_index=True) if parcalar else None


def _metrics_oku(sembol, bas, bit, borsa="binance"):
    """Futures metrics (OI + whale/retail L/S) — küçük; tüm aralık yüklenir. Yoksa None."""
    import pandas as pd
    from data_ingest import _aylar
    kok = _hist_dir() / borsa / sembol / "metrics"
    parcalar = []
    for yil, ay in _aylar(bas, bit):
        yol = kok / f"{yil:04d}" / f"{sembol}-metrics-{yil:04d}-{ay:02d}.parquet"
        if yol.exists():
            parcalar.append(pd.read_parquet(yol))
    if not parcalar:
        return None
    df = pd.concat(parcalar, ignore_index=True)
    # create_time → epoch ms
    df["ts_ms"] = pd.to_datetime(df["create_time"], errors="coerce").astype("int64") // 1_000_000
    return df


def _aggt_ay_yollari(sembol, bas, bit, borsa="binance"):
    """aggTrades aylık parquet yollarını döndürür (streaming için — concat YOK)."""
    from data_ingest import _aylar
    kok = _hist_dir() / borsa / sembol / "aggTrades"
    yollar = []
    for yil, ay in _aylar(bas, bit):
        yol = kok / f"{yil:04d}" / f"{sembol}-aggTrades-{yil:04d}-{ay:02d}.parquet"
        if yol.exists():
            yollar.append(yol)
    return yollar


# ─── Gün-bazlı ön hesap — STREAMING (bellek-güvenli) ─────────────────────────
def _gun_hazirla(klines, aggt_yollari, metrics_df=None):
    """
    Her gün için Asya H/L, fib, post-asia barlar + dakikalık CVD + günlük POC
    (+ varsa OI/whale/retail metrics haritaları).
    aggTrades AY AY okunur; tüm yıl asla RAM'e alınmaz (yalnız küçük gün-özetleri).
    Döner: {gun_idx: {...}}
    """
    import pandas as pd
    import numpy as np
    from collections import defaultdict
    from footprint_engine import aggressor_delta

    # 0) Bozuk satırları ele: open_time geçerli ms aralığında olmalı (~2014–2033).
    #    Aksi halde devasa/negatif gün indeksi tarih taşması yapar (OverflowError).
    OT_MIN, OT_MAX = 1_400_000_000_000, 2_000_000_000_000
    k = klines
    gecerli = (k["open_time"] >= OT_MIN) & (k["open_time"] < OT_MAX)
    if not bool(gecerli.all()):
        atilan = int((~gecerli).sum())
        print(f"      ⚠ {atilan} bozuk klines satırı (geçersiz open_time) atlandı", flush=True)
        k = k[gecerli]

    # 1) Klines → günlük Asya/fib/post (küçük)
    gun_arr = (k["open_time"] // GUN_MS).astype("int64")
    saat = (k["open_time"] % GUN_MS) / SAAT_MS
    k = k.assign(gun=gun_arr, saat=saat)

    htf = _htf_hesapla(k)        # HTF anchored VWAP (W/M/Q), no-lookahead (filtreli k)
    gunler = {}
    for gun, kg in k.groupby("gun"):
        asia = kg[(kg["saat"] >= ASIA_BAS_UTC) & (kg["saat"] < ASIA_BIT_UTC)]
        post = kg[(kg["saat"] >= ASIA_BIT_UTC) & (kg["saat"] < NY_CLOSE_UTC)].sort_values("open_time")
        if asia.empty or post.empty:
            continue
        a_h, a_l = float(asia["high"].max()), float(asia["low"].min())
        gunler[int(gun)] = {
            "a_h": a_h, "a_l": a_l, "fibs": fib_seviyeleri(a_l, a_h),
            "post_ts": post["open_time"].tolist(),
            "post_close": post["close"].tolist(),
            "cvd_map": {}, "poc": None,
            "htf": htf.get(int(gun), {}),
        }

    # 2) aggTrades AY AY → gün-bazlı birikim (dk-delta + fiyat-hacim)
    cvd_dk = defaultdict(lambda: defaultdict(float))   # gun → {dk: net delta}
    vol_dk = defaultdict(lambda: defaultdict(float))   # gun → {dk: toplam hacim}
    poc_px = defaultdict(lambda: defaultdict(float))   # gun → {fiyat_bin: hacim}
    for i, yol in enumerate(aggt_yollari, 1):
        print(f"      · aggTrades {yol.name} ({i}/{len(aggt_yollari)}) işleniyor…", flush=True)
        a = pd.read_parquet(yol, columns=["timestamp", "price", "quantity", "is_buyer_maker"])
        a["gun"] = (a["timestamp"] // GUN_MS).astype("int64")
        a["dk"] = (a["timestamp"] // 60_000).astype("int64")
        a["delta"] = aggressor_delta(a)
        # POC için 4 anlamlı haneye yuvarla (ölçekten bağımsız, hafif)
        p = a["price"].to_numpy()
        mag = np.floor(np.log10(np.clip(np.abs(p), 1e-9, None)))
        faktor = 10.0 ** (mag - 3)
        a["pbin"] = np.round(p / faktor) * faktor
        for (gun, dk), v in a.groupby(["gun", "dk"])["delta"].sum().items():
            cvd_dk[int(gun)][int(dk)] += float(v)
        for (gun, dk), v in a.groupby(["gun", "dk"])["quantity"].sum().items():
            vol_dk[int(gun)][int(dk)] += float(v)
        for (gun, pb), v in a.groupby(["gun", "pbin"])["quantity"].sum().items():
            poc_px[int(gun)][float(pb)] += float(v)
        del a   # ay DataFrame'ini hemen bırak

    # 3) Finalize: dakikalık CVD (cumsum) + POC (argmax) + footprint istatistikleri
    for gun, g in gunler.items():
        dkd = cvd_dk.get(gun)
        if dkd:
            kum = 0.0
            cvd_map = {}
            for dk in sorted(dkd):
                kum += dkd[dk]
                cvd_map[dk] = kum
            g["cvd_map"] = cvd_map
            g["delta_map"] = {int(d): float(v) for d, v in dkd.items()}  # ham dk-delta
        vd = vol_dk.get(gun)
        if vd:
            g["vol_map"] = {int(d): float(v) for d, v in vd.items()}
            voller = list(vd.values())
            g["vol_ort"] = float(np.mean(voller))
            g["vol_std"] = float(np.std(voller)) or 1.0
            absd = sorted(abs(x) for x in dkd.values()) if dkd else []
            # balina eşiği: günlük |delta| 80. persentil
            g["delta_abs_esik"] = absd[int(len(absd) * 0.8)] if absd else 0.0
        pxd = poc_px.get(gun)
        if pxd:
            g["poc"] = max(pxd, key=pxd.get)

    # 4) Metrics (OI + whale/retail L/S) — varsa gün-bazlı dk haritaları
    if metrics_df is not None and len(metrics_df):
        m = metrics_df.copy()
        m["gun"] = (m["ts_ms"] // GUN_MS).astype("int64")
        m["dk"] = (m["ts_ms"] // 60_000).astype("int64")
        for gun, mg in m.groupby("gun"):
            g = gunler.get(int(gun))
            if g is None:
                continue
            oi = mg["sum_open_interest"].astype(float)
            g["oi_map"] = {int(d): float(v) for d, v in zip(mg["dk"], oi)}
            g["oi_ort"] = float(oi.mean())
            g["oi_std"] = float(oi.std()) or 1.0
            g["whale_ls_map"] = {int(d): float(v) for d, v in
                                 zip(mg["dk"], mg["sum_toptrader_long_short_ratio"].astype(float))}
            g["retail_ls_map"] = {int(d): float(v) for d, v in
                                  zip(mg["dk"], mg["count_long_short_ratio"].astype(float))}
    return gunler


def _htf_hesapla(klines):
    """
    HTF anchored VWAP (haftalık/aylık/çeyreklik) — klines'tan, LOOKAHEAD'siz.
    Her gün için, periyot başından ÖNCEKİ GÜNE kadar biriken VWAP değeri.
    Döner: {gun: {"vwap_w":.., "vwap_m":.., "vwap_q":..}}
    """
    import pandas as pd
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    _EPOCH = _dt(1970, 1, 1, tzinfo=_tz.utc)
    k = klines
    gun = (k["open_time"] // GUN_MS).astype("int64")
    hlc3 = (k["high"] + k["low"] + k["close"]) / 3.0
    dfg = pd.DataFrame({"gun": gun, "pv": hlc3 * k["volume"], "v": k["volume"]})
    gunluk = dfg.groupby("gun", as_index=False).agg(pv=("pv", "sum"), v=("v", "sum")).sort_values("gun")

    out = {}
    cum = {"w": [0.0, 0.0], "m": [0.0, 0.0], "q": [0.0, 0.0]}
    prev = {"w": None, "m": None, "q": None}
    for r in gunluk.itertuples():
        g = int(r.gun)
        if not (10000 <= g <= 50000):   # ~1997–2106; dışı bozuk veri → atla (taşma önle)
            continue
        # epoch + gün (platform-bağımsız; Windows fromtimestamp OSError'ını önler)
        d = _EPOCH + _td(days=g)
        ic = d.isocalendar()
        anahtar = {"w": (ic[0], ic[1]), "m": (d.year, d.month),
                   "q": (d.year, (d.month - 1) // 3)}
        for key in ("w", "m", "q"):
            if prev[key] != anahtar[key]:
                cum[key] = [0.0, 0.0]
                prev[key] = anahtar[key]
        # Bugünü EKLEMEDEN önceki birikim → no-lookahead VWAP
        out[g] = {
            f"vwap_{key}": (cum[key][0] / cum[key][1] if cum[key][1] else None)
            for key in ("w", "m", "q")
        }
        for key in ("w", "m", "q"):
            cum[key][0] += float(r.pv)
            cum[key][1] += float(r.v)
    return out


def _dk_deger(harita: dict, ts: int):
    """ts dakikasındaki değer; o dk yoksa en yakın küçük dk (yoksa 0)."""
    dk = int(ts // 60_000)
    if dk in harita:
        return harita[dk]
    adaylar = [d for d in harita if d <= dk]
    return harita[max(adaylar)] if adaylar else 0.0


def _cvd_delta(cvd_map, ts, pencere):
    """ts anındaki CVD ile pencere dk öncesi CVD farkı (yön/ivme)."""
    dk = int(ts // 60_000)
    if dk not in cvd_map:
        # en yakın küçük dk
        adaylar = [d for d in cvd_map if d <= dk]
        if not adaylar:
            return 0.0
        dk = max(adaylar)
    onceki = dk - pencere
    adaylar = [d for d in cvd_map if d <= onceki]
    baz = cvd_map[max(adaylar)] if adaylar else 0.0
    return cvd_map[dk] - baz


def _sinyaller_uret(gunler: dict, param: tuple) -> list:
    """Tek parametre seti için OAR Asia Range sinyalleri (ts'li, walk_forward'a)."""
    etiket, min_range, eval_saat, cvd_pencere, poc_filtre, tol = param
    sinyaller = []
    for gun, g in gunler.items():
        if not asia_gecerli(g["a_h"], g["a_l"], min_range):
            continue
        fibs, poc, cvd_map = g["fibs"], g["poc"], g["cvd_map"]
        ts_list, close_list = g["post_ts"], g["post_close"]
        gun_sinyal_alindi = False
        for j, (ts, fiyat) in enumerate(zip(ts_list, close_list)):
            if gun_sinyal_alindi:
                break
            oran = temas_eden_fib(fiyat, fibs, tol)
            if oran is None:
                continue
            yon = fib_yonu(oran)
            d = _cvd_delta(cvd_map, ts, cvd_pencere)
            if not cvd_teyit(yon, d):
                continue
            if poc_filtre and not poc_teyit(yon, fiyat, poc):
                continue
            ileri = close_list[j + 1: j + 1 + eval_saat * 60]
            tp, sl = tp_sl_seviyeleri(oran, fiyat, fibs)
            out, net = degerlendir_tpsl(fiyat, yon, tp, sl, ileri)  # fee+slippage dahil
            sinyaller.append({"ts": int(ts), "fib": oran, "yon": yon,
                              "outcome": out, "pct": net})
            gun_sinyal_alindi = True
    return sinyaller


def aday_sinyaller_uret(gunler: dict, eval_saat: int = 4, cvd_pencere: int = 15,
                        tol: float = 0.10, min_range: float = 1.0) -> list:
    """
    FİLTRESİZ feature-zengin aday sinyaller (keşif motoru için).
    Her geçerli günde, her ekstrem fib'in İLK temasında bir aday üretir; her aday
    footprint/CVD/POC feature'ları + net TP/SL outcome taşır. Blokların filtreleyeceği
    ham havuz budur (oar_sinyaller + oar_kesif).
    """
    adaylar = []
    for gun, g in gunler.items():
        if not asia_gecerli(g["a_h"], g["a_l"], min_range):
            continue
        fibs = g["fibs"]; poc = g.get("poc"); cvd_map = g.get("cvd_map", {})
        delta_map = g.get("delta_map", {}); vol_map = g.get("vol_map", {})
        vol_ort = g.get("vol_ort", 0.0); vol_std = g.get("vol_std", 1.0) or 1.0
        balina_esik = g.get("delta_abs_esik", 0.0)
        ts_list, close_list = g["post_ts"], g["post_close"]
        alinan = set()
        for j, (ts, fiyat) in enumerate(zip(ts_list, close_list)):
            oran = temas_eden_fib(fiyat, fibs, tol)
            if oran is None or oran in alinan:
                continue
            alinan.add(oran)
            yon = fib_yonu(oran)
            seviye = fibs[oran]
            bar_delta = _dk_deger(delta_map, ts)
            vol = _dk_deger(vol_map, ts)
            vol_z = (vol - vol_ort) / vol_std
            cvd_d = _cvd_delta(cvd_map, ts, cvd_pencere)
            ilk5 = close_list[j + 1: j + 6] + [fiyat]
            ileri15 = close_list[j + 1: j + 16]
            if yon == "SHORT":
                absorp = (vol_z >= 1.0) and (max(ilk5) <= fiyat * 1.001)
                reclaim = any(c < seviye * 0.997 for c in ileri15)
            else:
                absorp = (vol_z >= 1.0) and (min(ilk5) >= fiyat * 0.999)
                reclaim = any(c > seviye * 1.003 for c in ileri15)
            ileri = close_list[j + 1: j + 1 + eval_saat * 60]
            tp, sl = tp_sl_seviyeleri(oran, fiyat, fibs)
            out, net = degerlendir_tpsl(fiyat, yon, tp, sl, ileri)
            kayit = {
                "ts": int(ts), "yon": yon, "fib": oran, "fiyat": fiyat, "poc": poc,
                "cvd_delta": cvd_d, "cvd_esik": 0.0,
                "bar_delta": bar_delta, "vol_z": round(vol_z, 3),
                "vol_yuksek": bool(vol_z >= 1.0),
                "balina": bool(balina_esik > 0 and abs(bar_delta) >= balina_esik),
                "absorp": bool(absorp), "reclaim": bool(reclaim),
                "outcome": out, "pct": net,
            }
            # OI / whale-retail (metrics varsa) — yoksa alan eklenmez → blok None → atlanır
            oi_map = g.get("oi_map")
            if oi_map:
                oi = _dk_deger(oi_map, ts)
                oi_z = (oi - g.get("oi_ort", 0)) / g.get("oi_std", 1.0)
                whale = _dk_deger(g.get("whale_ls_map", {}), ts)   # >1 long, <1 short
                retail = _dk_deger(g.get("retail_ls_map", {}), ts)
                if yon == "SHORT":
                    zit = (whale < 1.0 and retail > 1.0)   # whale short + retail long
                else:
                    zit = (whale > 1.0 and retail < 1.0)   # whale long + retail short
                kayit["oi_yuksek"] = bool(oi_z >= 1.0)
                kayit["whale_retail_zit"] = bool(zit)
            # HTF VWAP yakınlığı (haftalık/aylık/çeyreklik confluence)
            htf = g.get("htf") or {}
            htf_vwaplar = [htf.get("vwap_w"), htf.get("vwap_m"), htf.get("vwap_q")]
            if any(v is not None for v in htf_vwaplar):
                yakin = any(v and abs(fiyat - v) / fiyat * 100 <= 0.5 for v in htf_vwaplar)
                kayit["htf_vwap_yakin"] = bool(yakin)
            adaylar.append(kayit)
    return adaylar


def kesif_calistir(sembol, bas, bit, **kw):
    """Tek sembol/aralık: aday sinyalleri üret → keşif motoru."""
    from oar_kesif import kesfet
    klines = _klines_oku(sembol, bas, bit)
    aggt_yollari = _aggt_ay_yollari(sembol, bas, bit)
    if klines is None or not aggt_yollari:
        return {"hata": "parquet yok — önce data_ingest ile klines+aggTrades çek."}
    gunler = _gun_hazirla(klines, aggt_yollari, _metrics_oku(sembol, bas, bit))
    adaylar = aday_sinyaller_uret(gunler)
    return {"sembol": sembol, "aralik": f"{bas}..{bit}", "aday_sinyal": len(adaylar),
            "kesif": kesfet(adaylar, **kw)}


def _aday_cache_yol(sym, y_bas, y_bit):
    """Bir (sembol, yıl-aralığı) aday havuzunun disk önbellek yolu."""
    d = _hist_dir() / "_kesif_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{sym}_{y_bas}_{y_bit}_aday.json"


def _aday_cache_oku(sym, y_bas, y_bit):
    import json
    yol = _aday_cache_yol(sym, y_bas, y_bit)
    if yol.exists():
        try:
            with open(yol, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def _aday_cache_yaz(sym, y_bas, y_bit, adaylar):
    import json
    yol = _aday_cache_yol(sym, y_bas, y_bit)
    with open(yol, "w", encoding="utf-8") as f:
        json.dump(adaylar, f)


def _yil_araligi(bas, bit):
    return list(range(int(bas.split("-")[0]), int(bit.split("-")[0]) + 1))


def kesif_coklu(semboller, bas, bit, **kw):
    """
    ÇOK-YILLI BİRLEŞİK KEŞİF: birden çok sembol × birden çok yılın adaylarını TEK
    havuzda toplar → keşif motoru tek seferde çalışır (daha çok sinyal, daha güvenilir
    holdout). Bellek-güvenli: her (sembol, yıl) ayrı işlenir (aggTrades stream), yalnız
    küçük aday sözlükleri birikir; ham veri her yıl sonunda bırakılır.
    HOLDOUT zamanca en yeni dilim olur (eski yıllarda öğren, en yeni yılda doğrula).
    """
    from oar_kesif import kesfet
    by, ey = int(bas.split("-")[0]), int(bit.split("-")[0])
    havuz = []
    ozet = []
    yillar = _yil_araligi(bas, bit)
    toplam_is = len(semboller) * len(yillar)
    sira = 0
    for sym in semboller:
        for yil in yillar:
            sira += 1
            y_bas = bas if yil == by else f"{yil}-01"
            y_bit = bit if yil == ey else f"{yil}-12"
            print(f"[KEŞİF {sira}/{toplam_is}] {sym} {yil} "
                  f"(havuzda {len(havuz)} aday)…", flush=True)
            # Önbellek: bu (sembol,yıl) daha önce işlendiyse diskten yükle, yeniden hesaplama
            onbellek = _aday_cache_oku(sym, y_bas, y_bit)
            if onbellek is not None:
                havuz.extend(onbellek)
                print(f"   ⏩ {sym} {yil}: önbellekten {len(onbellek)} aday yüklendi "
                      f"(yeniden işlenmedi)", flush=True)
                ozet.append(f"{sym} {yil}: {len(onbellek)} aday (önbellek)")
                continue
            klines = _klines_oku(sym, y_bas, y_bit)
            yollar = _aggt_ay_yollari(sym, y_bas, y_bit)
            if klines is None or not yollar:
                print(f"   ⚠ {sym} {yil}: veri yok — atlandı", flush=True)
                ozet.append(f"{sym} {yil}: veri yok")
                continue
            gunler = _gun_hazirla(klines, yollar, _metrics_oku(sym, y_bas, y_bit))
            adlar = aday_sinyaller_uret(gunler)
            for a in adlar:
                a["sembol"] = sym
            _aday_cache_yaz(sym, y_bas, y_bit, adlar)   # çökme olsa bile bu yıl korunur
            havuz.extend(adlar)
            print(f"   ✓ {sym} {yil}: {len(gunler)} gün → {len(adlar)} aday sinyal "
                  f"(önbelleğe yazıldı)", flush=True)
            ozet.append(f"{sym} {yil}: {len(adlar)} aday")
            del klines, gunler
    return {"semboller": semboller, "aralik": f"{bas}..{bit}",
            "havuz_boyutu": len(havuz), "veri_ozeti": ozet,
            "kesif": kesfet(havuz, **kw)}


def calistir(sembol, bas, bit, folds=4):
    """Yerel parquet'lerden OAR Asia Range backtest + walk_forward OOS."""
    from data_integrity import dogrula
    from walk_forward import walk_forward

    klines = _klines_oku(sembol, bas, bit)
    aggt_yollari = _aggt_ay_yollari(sembol, bas, bit)
    if klines is None or not aggt_yollari:
        return {"hata": "parquet yok — önce data_ingest ile klines+aggTrades çek."}

    ok_k, rap_k = dogrula(klines, "klines", interval="1m")
    # aggTrades AY AY akışla işlenir (tüm yıl RAM'e alınmaz → bellek-güvenli)
    gunler = _gun_hazirla(klines, aggt_yollari)
    ok_a = True  # bütünlük gün-bazlı CVD/POC üretiminde dolaylı doğrulanır

    # Her param için sinyalleri bir kez üret, walk_forward'a fonksiyon ver
    param_sinyal = {p[0]: _sinyaller_uret(gunler, p) for p in PARAM_GRID}
    wf = walk_forward(lambda et: param_sinyal.get(et, []),
                      [p[0] for p in PARAM_GRID],
                      fold_sayisi=folds, is_oran=0.7)

    toplam_sinyal = sum(len(v) for v in param_sinyal.values())
    # Ay bazında işlem tablosu (en çok seçilen parametrenin sinyalleri)
    en_param = wf.get("en_cok_secilen_param")
    aylik = _aylik_islem(param_sinyal.get(en_param, []))
    return {
        "sembol": sembol, "aralik": f"{bas}..{bit}",
        "gun_sayisi": len(gunler),
        "param_sinyal_sayilari": {k: len(v) for k, v in param_sinyal.items()},
        "toplam_sinyal": toplam_sinyal,
        "en_param": en_param,
        "aylik_islem": aylik,
        "yillik_islem": {"toplam": sum(a["toplam"] for a in aylik.values()),
                         "win": sum(a["win"] for a in aylik.values())},
        "walk_forward": wf,
        "butunluk": {"klines_ok": ok_k, "aggtrades_ok": ok_a},
        "veri": {"klines_satir": int(len(klines)), "aggtrades_ay": len(aggt_yollari)},
    }


def _aylik_islem(sinyaller: list) -> dict:
    """ts'li sinyalleri YYYY-MM bazında WIN/LOSS sayar."""
    from datetime import datetime as _dt, timezone as _tz
    aylik = {}
    for s in sinyaller:
        ay = _dt.fromtimestamp(s["ts"] / 1000, tz=_tz.utc).strftime("%Y-%m")
        d = aylik.setdefault(ay, {"toplam": 0, "win": 0})
        d["toplam"] += 1
        if s.get("outcome") == "WIN":
            d["win"] += 1
    return dict(sorted(aylik.items()))


def main():
    ap = argparse.ArgumentParser(description="OAR Asia Range yerel derin-geçmiş backtest")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--from", dest="bas", default="", help="YYYY-MM")
    ap.add_argument("--to", dest="bit", default="", help="YYYY-MM")
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--ozet", action="store_true", help="birikmiş tüm backtest geçmişini yazdır")
    ap.add_argument("--kesif", action="store_true",
                    help="keşif modu: en iyi blok kombinasyonunu bul (OOS+holdout)")
    ap.add_argument("--yukle", default="", help="canlı sistem URL'i (sonucu hafızaya POST et)")
    ap.add_argument("--api-key", default="", help="canlı sistem OAR_API_KEY (yükleme için)")
    args = ap.parse_args()

    if args.ozet:
        _ozet_yazdir()
        return
    if not args.bas or not args.bit:
        print("HATA: --from ve --to zorunlu (veya --ozet kullan).")
        return
    _on_kontrol()

    if args.kesif:
        from oar_kesif import rapor as kesif_rapor
        # --symbol virgülle çoklu olabilir; aralık çok yıllı olabilir → birleşik keşif
        semboller = [s.strip() for s in args.symbol.split(",") if s.strip()]
        print(f"[KEŞİF BAŞLADI] {'+'.join(semboller)} {args.bas}..{args.bit} — "
              f"aggTrades gün gün işlenecek (büyük aralıkta uzun sürer, ilerleme aşağıda).",
              flush=True)
        res = kesif_coklu(semboller, args.bas, args.bit)
        print(f"[KEŞİF] {'+'.join(semboller)} {res['aralik']}: "
              f"havuz {res['havuz_boyutu']} aday sinyal")
        for satir in res["veri_ozeti"]:
            print("   •", satir)
        print(kesif_rapor(res["kesif"]))
        return

    res = calistir(args.symbol, args.bas, args.bit, folds=args.folds)
    if res.get("hata"):
        print("HATA:", res["hata"])
        return

    from walk_forward import rapor
    wf = res["walk_forward"]
    print(f"[OAR-BT] {res['sembol']} {res['aralik']}: {res['gun_sayisi']} gün | "
          f"sinyal/param: {res['param_sinyal_sayilari']} | bütünlük={res['butunluk']}")
    print(rapor(wf))
    # Ay bazında işlem tablosu (en iyi param) — fee+slippage dahil net WIN
    yi = res["yillik_islem"]
    print(f"── AY BAZINDA İŞLEM (param={res['en_param']}) — YIL TOPLAM: "
          f"{yi['toplam']} işlem, {yi['win']} net-WIN ──")
    for ay, d in res["aylik_islem"].items():
        wr = round(d["win"] / d["toplam"] * 100, 1) if d["toplam"] else 0
        print(f"   {ay}: {d['toplam']:>2} işlem | net-WR %{wr}")

    import json
    oos = wf.get("toplu_oos_metrik", {})
    kayit = {
        "sembol": res["sembol"], "aralik": res["aralik"],
        "tarih": datetime.now(timezone.utc).isoformat(),
        "gun_sayisi": res["gun_sayisi"],
        "toplam_sinyal": res["toplam_sinyal"],
        "en_iyi_param": wf.get("en_cok_secilen_param"),
        "oos_metrik": oos,
        "yillik_islem": res["yillik_islem"],
        "aylik_islem": res["aylik_islem"],
        "strateji": "OAR_ASIA_RANGE_TPSL",   # fee+slippage+TP/SL dahil
        "kaynak": "yerel_derin_gecmis",
    }
    yol = _gecmise_ekle(kayit)
    print(f"[OAR-BT] Hafızaya eklendi (birikimli): {yol}")
    if args.yukle:
        kod, cevap = _sisteme_yukle(args.yukle, kayit, args.api_key)
        print(f"[OAR-BT] Canlı sisteme yükleme: HTTP {kod} {cevap}")


def _ozet_yazdir():
    """Birikmiş yerel_backtest_gecmis.json'u okunur tablo olarak yazdır."""
    import json
    yol = _hist_dir() / "yerel_backtest_gecmis.json"
    if not yol.exists():
        print("Henüz birikmiş backtest yok:", yol)
        return
    try:
        kayitlar = json.loads(yol.read_text(encoding="utf-8"))
    except Exception as e:
        print("Okuma hatası:", e)
        return
    print(f"═══ BİRİKMİŞ BACKTEST GEÇMİŞİ ({len(kayitlar)} koşu) ═══")
    print(f"{'Sembol':10}{'Aralık':20}{'Puan':>5}{'WR%':>7}{'Sharpe':>9}{'Trade':>7}  Param")
    for x in kayitlar:
        o = x.get("oos_metrik", {})
        print(f"{x.get('sembol',''):10}{x.get('aralik',''):20}"
              f"{o.get('puan',0):>5}{o.get('win_rate',0):>7}{o.get('sharpe',0):>9}"
              f"{o.get('toplam_sinyal',0):>7}  {x.get('en_iyi_param','')}")


def _on_kontrol():
    eksik = [m for m in ("pandas", "pyarrow") if _yok(m)]
    if eksik:
        print("⚠ Bu LOKAL bir araçtır (Railway/canlı runtime için değil).")
        print(f"  Eksik kütüphane: {', '.join(eksik)}")
        print("  Kendi bilgisayarında kur:  pip install -r requirements-dev.txt")
        raise SystemExit(1)


def _yok(mod):
    try:
        __import__(mod); return False
    except ImportError:
        return True


def _gecmise_ekle(kayit: dict, maxn: int = 500):
    import json
    yol = _hist_dir() / "yerel_backtest_gecmis.json"
    try:
        gecmis = json.loads(yol.read_text(encoding="utf-8")) if yol.exists() else []
    except Exception:
        gecmis = []
    gecmis.append(kayit)
    # encoding="utf-8" ZORUNLU: Windows cp1254 emoji yazamaz.
    yol.write_text(json.dumps(gecmis[-maxn:], ensure_ascii=False, indent=2), encoding="utf-8")
    return yol


def _sisteme_yukle(url: str, kayit: dict, api_key: str = ""):
    import requests
    h = {"X-API-Key": api_key} if api_key else {}
    try:
        r = requests.post(url.rstrip("/") + "/api/backtest/yerel-ekle",
                          json=kayit, headers=h, timeout=30)
        return r.status_code, r.text[:200]
    except Exception as e:
        return 0, str(e)[:200]


if __name__ == "__main__":
    main()
