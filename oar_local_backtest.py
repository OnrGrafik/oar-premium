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


def degerlendir(giris: float, sonraki: list, yon: str, esik_pct: float = 0.5):
    """Girişten sonraki fiyat serisinde ±esik → (outcome, pct)."""
    if not sonraki:
        return "FLAT", 0.0
    son = sonraki[-1]
    pct = (son - giris) / giris * 100 if yon == "LONG" else (giris - son) / giris * 100
    if pct >= esik_pct:
        return "WIN", round(pct, 3)
    if pct <= -esik_pct:
        return "LOSS", round(pct, 3)
    return "FLAT", round(pct, 3)


# ─── Parquet yükleme (lazy pandas) ───────────────────────────────────────────
def _hist_dir() -> Path:
    from data_ingest import hist_dir
    return hist_dir()


def _parquet_oku(sembol, veri_tipi, bas, bit, borsa="binance"):
    import pandas as pd
    from data_ingest import _aylar
    kok = _hist_dir() / borsa / sembol / veri_tipi
    parcalar = []
    for yil, ay in _aylar(bas, bit):
        govde = (f"{sembol}-1m-{yil:04d}-{ay:02d}" if veri_tipi == "klines"
                 else f"{sembol}-aggTrades-{yil:04d}-{ay:02d}")
        yol = kok / f"{yil:04d}" / f"{govde}.parquet"
        if yol.exists():
            parcalar.append(pd.read_parquet(yol))
    return pd.concat(parcalar, ignore_index=True) if parcalar else None


# ─── Gün-bazlı ön hesap (footprint CVD + POC bir kez) ────────────────────────
def _gun_hazirla(klines, aggt):
    """
    Her gün için: Asya H/L, fib, post-asia bar listesi (ts,close), dakikalık CVD,
    günlük POC. Parametreden BAĞIMSIZ ağır hesap burada bir kez yapılır.
    Döner: {gun_idx: {...}}
    """
    import pandas as pd
    from footprint_engine import aggressor_delta

    k = klines.copy()
    k["gun"] = (k["open_time"] // GUN_MS).astype(int)
    k["saat"] = (k["open_time"] % GUN_MS) / SAAT_MS  # 0..24 (UTC)

    # aggTrades → dakikalık imzalı delta + POC için hazırlık
    a = aggt.copy()
    a["gun"] = (a["timestamp"] // GUN_MS).astype(int)
    a["dk"] = (a["timestamp"] // 60_000).astype(int)
    a["delta"] = aggressor_delta(a)

    gunler = {}
    for gun, kg in k.groupby("gun"):
        asia = kg[(kg["saat"] >= ASIA_BAS_UTC) & (kg["saat"] < ASIA_BIT_UTC)]
        post = kg[(kg["saat"] >= ASIA_BIT_UTC) & (kg["saat"] < NY_CLOSE_UTC)].sort_values("open_time")
        if asia.empty or post.empty:
            continue
        a_h, a_l = float(asia["high"].max()), float(asia["low"].min())

        ag = a[a["gun"] == gun]
        # dakikalık CVD (delta cumsum), ts→cvd haritası
        if not ag.empty:
            dk_delta = ag.groupby("dk")["delta"].sum().sort_index()
            cvd_seri = dk_delta.cumsum()
            cvd_map = {int(dk): float(c) for dk, c in cvd_seri.items()}
            # günlük POC: en yüksek hacimli fiyat (0.1$ yuvarlama)
            ag2 = ag.assign(fyuv=(ag["price"]).round(1))
            poc = float(ag2.groupby("fyuv")["quantity"].sum().idxmax())
        else:
            cvd_map, poc = {}, None

        gunler[int(gun)] = {
            "a_h": a_h, "a_l": a_l,
            "fibs": fib_seviyeleri(a_l, a_h),
            "post_ts": post["open_time"].tolist(),
            "post_close": post["close"].tolist(),
            "cvd_map": cvd_map,
            "poc": poc,
        }
    return gunler


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
            out, pct = degerlendir(fiyat, ileri, yon, esik_pct=0.5)
            sinyaller.append({"ts": int(ts), "fib": oran, "yon": yon,
                              "outcome": out, "pct": pct})
            gun_sinyal_alindi = True
    return sinyaller


def calistir(sembol, bas, bit, folds=4):
    """Yerel parquet'lerden OAR Asia Range backtest + walk_forward OOS."""
    from data_integrity import dogrula
    from walk_forward import walk_forward

    klines = _parquet_oku(sembol, "klines", bas, bit)
    aggt = _parquet_oku(sembol, "aggTrades", bas, bit)
    if klines is None or aggt is None:
        return {"hata": "parquet yok — önce data_ingest ile klines+aggTrades çek."}

    ok_k, rap_k = dogrula(klines, "klines", interval="1m")
    ok_a, rap_a = dogrula(aggt, "aggTrades")
    # Bütünlük uyarıları bilgi amaçlı; eksik bar backtesti durdurmaz ama raporlanır
    gunler = _gun_hazirla(klines, aggt)

    # Her param için sinyalleri bir kez üret, walk_forward'a fonksiyon ver
    param_sinyal = {p[0]: _sinyaller_uret(gunler, p) for p in PARAM_GRID}
    wf = walk_forward(lambda et: param_sinyal.get(et, []),
                      [p[0] for p in PARAM_GRID],
                      fold_sayisi=folds, is_oran=0.7)

    toplam_sinyal = sum(len(v) for v in param_sinyal.values())
    return {
        "sembol": sembol, "aralik": f"{bas}..{bit}",
        "gun_sayisi": len(gunler),
        "param_sinyal_sayilari": {k: len(v) for k, v in param_sinyal.items()},
        "toplam_sinyal": toplam_sinyal,
        "walk_forward": wf,
        "butunluk": {"klines_ok": ok_k, "aggtrades_ok": ok_a},
        "veri": {"klines_satir": len(klines), "aggtrades_satir": len(aggt)},
    }


def main():
    ap = argparse.ArgumentParser(description="OAR Asia Range yerel derin-geçmiş backtest")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--from", dest="bas", required=True, help="YYYY-MM")
    ap.add_argument("--to", dest="bit", required=True, help="YYYY-MM")
    ap.add_argument("--folds", type=int, default=4)
    ap.add_argument("--yukle", default="", help="canlı sistem URL'i (sonucu hafızaya POST et)")
    ap.add_argument("--api-key", default="", help="canlı sistem OAR_API_KEY (yükleme için)")
    args = ap.parse_args()
    _on_kontrol()

    res = calistir(args.symbol, args.bas, args.bit, folds=args.folds)
    if res.get("hata"):
        print("HATA:", res["hata"])
        return

    from walk_forward import rapor
    wf = res["walk_forward"]
    print(f"[OAR-BT] {res['sembol']} {res['aralik']}: {res['gun_sayisi']} gün | "
          f"sinyal/param: {res['param_sinyal_sayilari']} | bütünlük={res['butunluk']}")
    print(rapor(wf))

    import json
    oos = wf.get("toplu_oos_metrik", {})
    kayit = {
        "sembol": res["sembol"], "aralik": res["aralik"],
        "tarih": datetime.now(timezone.utc).isoformat(),
        "gun_sayisi": res["gun_sayisi"],
        "toplam_sinyal": res["toplam_sinyal"],
        "en_iyi_param": wf.get("en_cok_secilen_param"),
        "oos_metrik": oos,
        "strateji": "OAR_ASIA_RANGE",
        "kaynak": "yerel_derin_gecmis",
    }
    yol = _gecmise_ekle(kayit)
    print(f"[OAR-BT] Hafızaya eklendi (birikimli): {yol}")
    if args.yukle:
        kod, cevap = _sisteme_yukle(args.yukle, kayit, args.api_key)
        print(f"[OAR-BT] Canlı sisteme yükleme: HTTP {kod} {cevap}")


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
