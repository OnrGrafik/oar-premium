"""
Persistence Layer — OAR Premium
═══════════════════════════════════════════════════════
SQLite tabanlı kalıcı depolama. JSON dosyalarının aksine:
  • Eşzamanlı yazımda bozulmaz (WAL modu)
  • Sorgulanabilir (tarih aralığı, win rate, agent bazlı)
  • Paper trade ve CIO karar geçmişi için kalıcı kayıt

Render restart'larında /var/data kalıcı disk üzerinde durur.
"""

import sqlite3
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from contextlib import contextmanager
import threading

DATA_DIR = Path(os.environ.get("DATA_DIR") or ("/var/data" if Path("/var/data").exists() else "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "oar.db"

_lock = threading.Lock()
_initialized = False


@contextmanager
def _conn():
    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Tabloları oluştur (idempotent)."""
    global _initialized
    if _initialized:
        return
    with _lock, _conn() as c:
        # CIO karar geçmişi
        c.execute("""
            CREATE TABLE IF NOT EXISTS kararlar (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                sembol      TEXT NOT NULL,
                karar       TEXT NOT NULL,
                konfidans   REAL,
                conviction  TEXT,
                ham_skor    REAL,
                rejim       TEXT,
                long_say    INTEGER,
                short_say   INTEGER,
                catisma_say INTEGER,
                detay_json  TEXT,
                tarih       TEXT NOT NULL
            )
        """)
        # Paper trade işlemleri
        c.execute("""
            CREATE TABLE IF NOT EXISTS paper_trades (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                sembol       TEXT NOT NULL,
                yon          TEXT NOT NULL,
                giris        REAL NOT NULL,
                sl           REAL,
                tp           REAL,
                miktar       REAL,
                konfidans    REAL,
                rejim        TEXT,
                durum        TEXT NOT NULL DEFAULT 'OPEN',
                cikis        REAL,
                pnl_pct      REAL,
                pnl_usd      REAL,
                sonuc        TEXT,
                karar_id     INTEGER,
                acilis_tarih TEXT NOT NULL,
                kapanis_tarih TEXT,
                not_metni    TEXT
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_trades_durum ON paper_trades(durum)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_kararlar_tarih ON kararlar(tarih)")
    _initialized = True


# ─── CIO Karar kaydı ──────────────────────────────────────────────
def karar_kaydet(k: dict) -> int:
    """Confidence Engine çıktısını arşivle. Karar id döner."""
    init_db()
    oy = k.get("oy_dagilimi", {})
    rejim = k.get("rejim", {})
    with _lock, _conn() as c:
        cur = c.execute("""
            INSERT INTO kararlar
            (sembol, karar, konfidans, conviction, ham_skor, rejim,
             long_say, short_say, catisma_say, detay_json, tarih)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            k.get("sembol", "BTCUSDT"),
            k.get("karar", "NO_TRADE"),
            k.get("konfidans", 0),
            k.get("conviction", "LOW"),
            k.get("ham_skor", 0),
            rejim.get("rejim", "UNKNOWN") if isinstance(rejim, dict) else "",
            oy.get("LONG", 0),
            oy.get("SHORT", 0),
            len(k.get("catismalar", [])),
            json.dumps(k, ensure_ascii=False),
            k.get("tarih") or datetime.now(timezone.utc).isoformat(),
        ))
        return cur.lastrowid


def karar_gecmisi(limit: int = 50, sembol: str = None) -> list:
    init_db()
    with _conn() as c:
        if sembol:
            rows = c.execute(
                "SELECT id,sembol,karar,konfidans,conviction,ham_skor,rejim,long_say,short_say,catisma_say,tarih "
                "FROM kararlar WHERE sembol=? ORDER BY id DESC LIMIT ?", (sembol, limit)).fetchall()
        else:
            rows = c.execute(
                "SELECT id,sembol,karar,konfidans,conviction,ham_skor,rejim,long_say,short_say,catisma_say,tarih "
                "FROM kararlar ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


# ─── Paper Trade kayıtları ────────────────────────────────────────
def trade_ac(t: dict) -> int:
    init_db()
    with _lock, _conn() as c:
        cur = c.execute("""
            INSERT INTO paper_trades
            (sembol, yon, giris, sl, tp, miktar, konfidans, rejim,
             durum, karar_id, acilis_tarih, not_metni)
            VALUES (?,?,?,?,?,?,?,?,'OPEN',?,?,?)
        """, (
            t.get("sembol", "BTCUSDT"),
            t.get("yon"),
            t.get("giris"),
            t.get("sl"),
            t.get("tp"),
            t.get("miktar", 1000.0),
            t.get("konfidans", 0),
            t.get("rejim", ""),
            t.get("karar_id"),
            datetime.now(timezone.utc).isoformat(),
            t.get("not_metni", ""),
        ))
        return cur.lastrowid


def acik_tradeler(sembol: str = None) -> list:
    init_db()
    with _conn() as c:
        if sembol:
            rows = c.execute("SELECT * FROM paper_trades WHERE durum='OPEN' AND sembol=? ORDER BY id DESC", (sembol,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM paper_trades WHERE durum='OPEN' ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


def trade_kapat(trade_id: int, cikis: float, sonuc: str) -> dict:
    """Bir trade'i kapat — pnl içeride hesaplanır (fee hariç, geriye uyum için)."""
    init_db()
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM paper_trades WHERE id=?", (trade_id,)).fetchone()
        if not row:
            return {}
        t = dict(row)
        giris = t["giris"]
        yon = t["yon"]
        if yon == "LONG":
            pnl_pct = (cikis - giris) / giris * 100
        else:
            pnl_pct = (giris - cikis) / giris * 100
        pnl_usd = (t["miktar"] or 1000.0) * pnl_pct / 100
        c.execute("""
            UPDATE paper_trades
            SET durum='CLOSED', cikis=?, pnl_pct=?, pnl_usd=?, sonuc=?, kapanis_tarih=?
            WHERE id=?
        """, (cikis, round(pnl_pct, 3), round(pnl_usd, 2), sonuc,
              datetime.now(timezone.utc).isoformat(), trade_id))
        t.update({"durum": "CLOSED", "cikis": cikis, "pnl_pct": round(pnl_pct, 3),
                  "pnl_usd": round(pnl_usd, 2), "sonuc": sonuc})
        return t


def trade_kapat_net(trade_id: int, cikis: float, sonuc: str,
                    pnl_pct: float, pnl_usd: float) -> dict:
    """Fee dahil net PnL dışarıdan verilmiş kapatma (paper_trade_agent kullanır)."""
    init_db()
    with _lock, _conn() as c:
        row = c.execute("SELECT * FROM paper_trades WHERE id=?", (trade_id,)).fetchone()
        if not row:
            return {}
        t = dict(row)
        c.execute("""
            UPDATE paper_trades
            SET durum='CLOSED', cikis=?, pnl_pct=?, pnl_usd=?, sonuc=?, kapanis_tarih=?
            WHERE id=?
        """, (cikis, pnl_pct, pnl_usd, sonuc,
              datetime.now(timezone.utc).isoformat(), trade_id))
        t.update({"durum": "CLOSED", "cikis": cikis,
                  "pnl_pct": pnl_pct, "pnl_usd": pnl_usd, "sonuc": sonuc})
        return t


def trade_gecmisi(limit: int = 100, sembol: str = None) -> list:
    init_db()
    with _conn() as c:
        if sembol:
            rows = c.execute("SELECT * FROM paper_trades WHERE sembol=? ORDER BY id DESC LIMIT ?", (sembol, limit)).fetchall()
        else:
            rows = c.execute("SELECT * FROM paper_trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def karar_detay_json(karar_id: int) -> dict:
    """Bir kararın detay JSON'unu getir."""
    init_db()
    import json as _json
    with _conn() as c:
        row = c.execute("SELECT detay_json FROM kararlar WHERE id=?", (karar_id,)).fetchone()
    if row and row[0]:
        try:
            return _json.loads(row[0])
        except Exception:
            pass
    return {}


def trade_istatistik(sembol: str = None) -> dict:
    """Kapanmış trade'lerin performans özeti."""
    init_db()
    with _conn() as c:
        q = "SELECT yon, sonuc, pnl_pct, pnl_usd, rejim FROM paper_trades WHERE durum='CLOSED'"
        params = []
        if sembol:
            q += " AND sembol=?"
            params.append(sembol)
        rows = [dict(r) for r in c.execute(q, params).fetchall()]

    toplam = len(rows)
    if toplam == 0:
        return {"toplam": 0, "win": 0, "loss": 0, "win_rate": 0,
                "toplam_pnl_pct": 0, "toplam_pnl_usd": 0, "ort_pnl_pct": 0,
                "rejim_bazli": {}, "acik": len(acik_tradeler(sembol))}

    win = sum(1 for r in rows if (r["pnl_pct"] or 0) > 0)
    loss = toplam - win
    toplam_pnl_pct = sum(r["pnl_pct"] or 0 for r in rows)
    toplam_pnl_usd = sum(r["pnl_usd"] or 0 for r in rows)

    # Rejim bazlı win rate
    rejim_bazli = {}
    for r in rows:
        rj = r.get("rejim") or "UNKNOWN"
        d = rejim_bazli.setdefault(rj, {"toplam": 0, "win": 0, "pnl_pct": 0})
        d["toplam"] += 1
        if (r["pnl_pct"] or 0) > 0:
            d["win"] += 1
        d["pnl_pct"] += r["pnl_pct"] or 0
    for rj, d in rejim_bazli.items():
        d["win_rate"] = round(d["win"] / d["toplam"] * 100, 1) if d["toplam"] else 0
        d["pnl_pct"] = round(d["pnl_pct"], 2)

    return {
        "toplam": toplam,
        "win": win,
        "loss": loss,
        "win_rate": round(win / toplam * 100, 1),
        "toplam_pnl_pct": round(toplam_pnl_pct, 2),
        "toplam_pnl_usd": round(toplam_pnl_usd, 2),
        "ort_pnl_pct": round(toplam_pnl_pct / toplam, 3),
        "rejim_bazli": rejim_bazli,
        "acik": len(acik_tradeler(sembol)),
    }


if __name__ == "__main__":
    init_db()
    print(f"DB hazır: {DB_PATH}")
    print("İstatistik:", json.dumps(trade_istatistik(), ensure_ascii=False, indent=2))
