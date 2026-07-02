"""
data_ingest.py — Yerel Tarihsel Veri Çekme (YENİ — yalnız LOKAL/backtest)
═══════════════════════════════════════════════════════════════════════════
AMAÇ: Backtest için BTCUSDT/ETHUSDT verisini canlı REST yerine resmi public
bulk dump'tan (data.binance.vision) yerel diske çeker.

⚠️ CANLI HATTA DOKUNMAZ:
  - main.py / canlı agentlar bu modülü import ETMEZ.
  - Canlı veri için mevcut WebSocket/REST hattı (exchange_client.py) kullanılır.
  - Ağır bağımlılıklar (pandas, pyarrow, requests) → requirements-dev.txt.
    Render runtime'da kurulu değildir; bu modül yalnız lokal runner'da çalışır.

KAYNAK: data.binance.vision aylık zip'li CSV dump'ları.
  Veri tipleri:
    - klines    : OHLCV (1m bar) — fiyat/hacim backtest'i.
    - aggTrades : trade-level (price/qty/timestamp + isBuyerMaker bayrağı).
                  Görev 3'ün (footprint_engine) CVD/footprint kaynağıdır.

  NOT (dump real-time DEĞİL): Bir günün verisi ertesi gün yüklenir. Bu yüzden
  canlı/anlık karar için kullanılmaz — yalnız tarihsel backtest içindir.

  NOT (L2 yok): Public dump'ta tam tarihsel L2 order book YOKTUR. Footprint/CVD
  aggTrades'in buyer-maker bayrağından (aggressor tarafı) kurulur; bu yeterlidir.

HEDEF KLASÖR: env OAR_HIST_DIR (tanımsızsa C:\\Users\\ONURKLNC\\Desktop\\Data).
  Yapı (tarihe/yıla göre partition):
    {OAR_HIST_DIR}/{borsa}/{sembol}/{veri_tipi}/{yil}/{sembol}-{veri_tipi}-{YYYY-MM}.parquet

AKIŞ: indir → checksum (SHA256) doğrula → CSV→Parquet dönüştür.
  - Inkremental & resumable: var olan ay parquet'i atlanır (yarıda kalırsa devam).
  - Tekilleştirme: parquet yazılırken timestamp'e göre duplicate satır atılır.

KULLANIM:
  python data_ingest.py --symbol BTCUSDT --type aggTrades --from 2024-01 --to 2024-02
  python data_ingest.py --symbol ETHUSDT --type klines --interval 1m --from 2024-01 --to 2024-03
"""
import os
import sys
import csv
import zipfile
import hashlib
import argparse
from pathlib import Path
from datetime import date

# ── Sabitler ────────────────────────────────────────────────────────────────
BASE_URL = "https://data.binance.vision/data"
DEFAULT_HIST_DIR = r"C:\Users\ONURKLNC\Desktop\Data"

# Binance dump CSV kolon şemaları (resmi sıra).
# https://github.com/binance/binance-public-data
KLINES_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "count",
    "taker_buy_base", "taker_buy_quote", "ignore",
]
AGGTRADES_COLS = [
    "agg_trade_id", "price", "quantity",
    "first_trade_id", "last_trade_id",
    "timestamp", "is_buyer_maker", "is_best_match",
]

# Futures metrics (GÜNLÜK dump): OI + whale (top-trader L/S) + retail (global L/S) + taker
METRICS_COLS = [
    "create_time", "symbol", "sum_open_interest", "sum_open_interest_value",
    "count_toptrader_long_short_ratio", "sum_toptrader_long_short_ratio",
    "count_long_short_ratio", "sum_taker_long_short_vol_ratio",
]

VERI_TIPLERI = {"klines", "aggTrades", "metrics"}


def hist_dir() -> Path:
    """
    Kalıcı veri/durum klasörü. Öncelik:
      1) OAR_HIST_DIR         — elle (yerel PC: parquet backtest klasörü)
      2) DATA_DIR             — main.py Railway Volume'a eşitler (KALICI disk)
      3) RAILWAY_VOLUME_MOUNT_PATH — Railway kalıcı volume mount yolu
    Hiçbiri yoksa: yerel Windows Data klasörü VARSA onu, yoksa ./data (Linux/Railway
    güvenli — Windows yoluna düşüp geçici diske yazmayı önler → deployda sıfırlanmaz).
    """
    env = (os.environ.get("OAR_HIST_DIR")
           or os.environ.get("DATA_DIR")
           or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH"))
    if env:
        return Path(env)
    varsayilan = Path(DEFAULT_HIST_DIR)
    return varsayilan if varsayilan.exists() else Path("data")


def _aylar(bas: str, bit: str):
    """'2024-01'..'2024-03' → [(2024,1),(2024,2),(2024,3)] (dahil)."""
    by, bm = (int(x) for x in bas.split("-"))
    ey, em = (int(x) for x in bit.split("-"))
    y, m = by, bm
    while (y, m) <= (ey, em):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


def _dump_yolu(borsa: str, market: str, sembol: str, veri_tipi: str,
               interval: str, yil: int, ay: int) -> tuple[str, str]:
    """(zip_url, dosya_adi_govdesi) döner. borsa şimdilik 'binance' sabit."""
    ym = f"{yil:04d}-{ay:02d}"
    if veri_tipi == "klines":
        # .../spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2024-01.zip
        govde = f"{sembol}-{interval}-{ym}"
        rel = f"{market}/monthly/klines/{sembol}/{interval}/{govde}.zip"
    else:  # aggTrades
        govde = f"{sembol}-aggTrades-{ym}"
        rel = f"{market}/monthly/aggTrades/{sembol}/{govde}.zip"
    return f"{BASE_URL}/{rel}", govde


def _parquet_yolu(borsa: str, sembol: str, veri_tipi: str,
                  yil: int, govde: str) -> Path:
    return hist_dir() / borsa / sembol / veri_tipi / f"{yil:04d}" / f"{govde}.parquet"


def _indir(url: str, hedef: Path) -> bool:
    """url'i hedef'e indirir. 404 (henüz yüklenmemiş ay) → False."""
    import requests  # ağır dep — lazy import (requirements-dev.txt)
    hedef.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=120) as r:
        if r.status_code == 404:
            return False
        r.raise_for_status()
        with open(hedef, "wb") as f:
            for parca in r.iter_content(chunk_size=1 << 20):
                f.write(parca)
    return True


def _checksum_dogrula(zip_yol: Path, url: str) -> bool:
    """
    Binance her zip için '<url>.CHECKSUM' (SHA256) yayınlar.
    İndirilen zip'in SHA256'sını bu değerle karşılaştırır.
    CHECKSUM erişilemezse (None) doğrulama atlanır ama uyarı verilir.
    """
    import requests  # lazy
    r = requests.get(url + ".CHECKSUM", timeout=60)
    if r.status_code != 200:
        print(f"  ⚠ CHECKSUM bulunamadı, doğrulama atlandı: {zip_yol.name}")
        return True
    beklenen = r.text.strip().split()[0].lower()
    h = hashlib.sha256()
    with open(zip_yol, "rb") as f:
        for blok in iter(lambda: f.read(1 << 20), b""):
            h.update(blok)
    gercek = h.hexdigest().lower()
    if gercek != beklenen:
        print(f"  ✗ CHECKSUM UYUŞMADI: {zip_yol.name}")
        return False
    return True


def _csv_oku(zip_yol: Path, veri_tipi: str):
    """Zip içindeki tek CSV'yi satır listesi (dict) olarak döndürür."""
    import pandas as pd  # lazy
    kolonlar = (KLINES_COLS if veri_tipi == "klines"
                else METRICS_COLS if veri_tipi == "metrics"
                else AGGTRADES_COLS)
    with zipfile.ZipFile(zip_yol) as z:
        ad = z.namelist()[0]
        with z.open(ad) as fh:
            # Binance bazı dump'larda başlık satırı koyar; ilk hücre sayısal
            # değilse başlık var demektir → header=0, değilse header=None.
            ornek = fh.read(64).decode("utf-8", "ignore")
        ilk_hucre = ornek.split(",")[0].strip()
        basliksiz = ilk_hucre.replace(".", "").replace("-", "").isdigit()
        with z.open(ad) as fh:
            df = pd.read_csv(
                fh,
                header=None if basliksiz else 0,
                names=kolonlar,
            )
    return df


def _parquete_yaz(df, parquet_yol: Path, veri_tipi: str) -> int:
    """Tekilleştir + parquet yaz. Yazılan satır sayısını döndürür."""
    anahtar = ("open_time" if veri_tipi == "klines"
               else "create_time" if veri_tipi == "metrics"
               else "timestamp")
    if anahtar in df.columns:
        df = df.drop_duplicates(subset=[anahtar]).sort_values(anahtar)
    parquet_yol.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(parquet_yol, index=False)
    return len(df)


def _metrics_ay_cek(sembol: str, yil: int, ay: int, tmp_dir: Path) -> dict:
    """
    Futures metrics GÜNLÜK dump'tır → bir ayın tüm günlerini indir, tek aylık
    parquet'e birleştir. (OI + whale/retail L/S + taker.)
    """
    import calendar
    import pandas as pd
    govde = f"{sembol}-metrics-{yil:04d}-{ay:02d}"
    pq = _parquet_yolu("binance", sembol, "metrics", yil, govde)
    if pq.exists():
        return {"ay": f"{yil}-{ay:02d}", "durum": "ATLANDI (mevcut)", "yol": str(pq)}
    parcalar = []
    for gun in range(1, calendar.monthrange(yil, ay)[1] + 1):
        ym = f"{yil:04d}-{ay:02d}-{gun:02d}"
        url = f"{BASE_URL}/futures/um/daily/metrics/{sembol}/{sembol}-metrics-{ym}.zip"
        zip_yol = tmp_dir / f"{sembol}-metrics-{ym}.zip"
        try:
            if not _indir(url, zip_yol):
                continue  # o gün yok (404) → atla
            if not _checksum_dogrula(zip_yol, url):
                continue
            parcalar.append(_csv_oku(zip_yol, "metrics"))
        finally:
            if zip_yol.exists():
                zip_yol.unlink()
    if not parcalar:
        return {"ay": f"{yil}-{ay:02d}", "durum": "YOK (404 — gün dosyası yok)"}
    df = pd.concat(parcalar, ignore_index=True)
    n = _parquete_yaz(df, pq, "metrics")
    return {"ay": f"{yil}-{ay:02d}", "durum": "İNDİRİLDİ", "satir": n, "yol": str(pq)}


def ay_cek(borsa: str, market: str, sembol: str, veri_tipi: str,
           interval: str, yil: int, ay: int, tmp_dir: Path) -> dict:
    """Tek ay için: indir→doğrula→parquet. Resumable (varsa atlar)."""
    if veri_tipi == "metrics":
        return _metrics_ay_cek(sembol, yil, ay, tmp_dir)
    url, govde = _dump_yolu(borsa, market, sembol, veri_tipi, interval, yil, ay)
    pq = _parquet_yolu(borsa, sembol, veri_tipi, yil, govde)
    if pq.exists():
        return {"ay": f"{yil}-{ay:02d}", "durum": "ATLANDI (mevcut)", "yol": str(pq)}

    zip_yol = tmp_dir / f"{govde}.zip"
    try:
        if not _indir(url, zip_yol):
            return {"ay": f"{yil}-{ay:02d}", "durum": "YOK (404 — henüz yüklenmemiş)"}
        if not _checksum_dogrula(zip_yol, url):
            return {"ay": f"{yil}-{ay:02d}", "durum": "HATA (checksum)"}
        df = _csv_oku(zip_yol, veri_tipi)
        n = _parquete_yaz(df, pq, veri_tipi)
        return {"ay": f"{yil}-{ay:02d}", "durum": "İNDİRİLDİ", "satir": n, "yol": str(pq)}
    finally:
        if zip_yol.exists():
            zip_yol.unlink()  # ham zip'i tut etme — disk koruması


def cek(sembol: str, veri_tipi: str, bas: str, bit: str,
        interval: str = "1m", borsa: str = "binance", market: str = "spot") -> dict:
    """
    bas/bit: 'YYYY-MM' (dahil). Eksik ayları çeker, mevcutları atlar.
    """
    if veri_tipi not in VERI_TIPLERI:
        raise ValueError(f"veri_tipi {sorted(VERI_TIPLERI)} içinden olmalı, geldi: {veri_tipi}")
    tmp_dir = hist_dir() / "_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    sonuclar = []
    for yil, ay in _aylar(bas, bit):
        s = ay_cek(borsa, market, sembol, veri_tipi, interval, yil, ay, tmp_dir)
        print(f"[{sembol} {veri_tipi}] {s['ay']}: {s['durum']}"
              + (f" ({s.get('satir')} satır)" if s.get("satir") else ""))
        sonuclar.append(s)
    return {
        "sembol": sembol, "veri_tipi": veri_tipi,
        "aralik": f"{bas}..{bit}", "hedef": str(hist_dir()),
        "sonuclar": sonuclar,
    }


def _on_kontrol():
    """Ağır deps eksikse çıplak ModuleNotFoundError yerine net yönerge ver."""
    eksik = []
    for mod in ("requests", "pandas", "pyarrow"):
        try:
            __import__(mod)
        except ImportError:
            eksik.append(mod)
    if eksik:
        print("⚠ Bu LOKAL bir araçtır (Railway/canlı runtime için değil).")
        print(f"  Eksik kütüphane: {', '.join(eksik)}")
        print("  Kendi bilgisayarında kur:  pip install -r requirements-dev.txt")
        print("  (Railway shell'de: /opt/venv/bin/pip ... — ama bu iş büyük disk ister, "
              "Railway uygun değildir.)")
        raise SystemExit(1)


def main():
    _on_kontrol()
    ap = argparse.ArgumentParser(description="OAR yerel tarihsel veri çekme (Binance public dump)")
    ap.add_argument("--symbol", required=True, help="örn. BTCUSDT")
    ap.add_argument("--type", required=True, choices=sorted(VERI_TIPLERI), help="klines | aggTrades")
    ap.add_argument("--from", dest="bas", required=True, help="başlangıç ayı YYYY-MM")
    ap.add_argument("--to", dest="bit", required=True, help="bitiş ayı YYYY-MM (dahil)")
    ap.add_argument("--interval", default="1m", help="klines için bar aralığı (varsayılan 1m)")
    ap.add_argument("--market", default="spot", choices=["spot", "futures/um"],
                    help="spot | futures/um (USDⓈ-M)")
    args = ap.parse_args()
    cek(args.symbol, args.type, args.bas, args.bit,
        interval=args.interval, market=args.market)


if __name__ == "__main__":
    main()
