"""
Kitap Veritabanı — OAR Premium (SQLite)
═══════════════════════════════════════════════════════════════════
240+ kitabın 27.000+ chunk'ını SQLite'ta tutar. knowledge.json'a
DOKUNMAZ (o küçük kalır, hızlı çalışır). Kitaplar ayrı DB'de:
  • RAM'e tüm dosyayı yüklemez → 502 yok
  • FTS5 tam metin arama → milisaniyede arar
  • Import idempotent (tekrar yükleme güvenli)

SQLite + FTS5 Python'da gömülü — ek kütüphane YOK, ücretsiz.
"""
import os, sqlite3, json
from pathlib import Path
from datetime import datetime, timezone

DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "kitaplar.db"

def _conn():
    c = sqlite3.connect(str(DB_PATH), timeout=30)
    c.execute("PRAGMA journal_mode=WAL")      # eşzamanlı yazma/okuma
    c.execute("PRAGMA synchronous=NORMAL")
    return c

def init_db():
    c = _conn()
    c.execute("""CREATE TABLE IF NOT EXISTS chunks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT, category TEXT, chunk_idx INTEGER,
        content TEXT, added_at TEXT,
        UNIQUE(title, chunk_idx))""")
    # FTS5 tam metin arama indeksi
    c.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
        USING fts5(content, title, category, content=chunks, content_rowid=id)""")
    # Trigger: chunks'a eklenince FTS güncelle
    c.execute("""CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
        INSERT INTO chunks_fts(rowid,content,title,category)
        VALUES(new.id,new.content,new.title,new.category); END""")
    c.commit(); c.close()

def import_chunks(documents: list) -> dict:
    """Bir batch chunk ekle. Idempotent (UNIQUE ile tekrarı atlar)."""
    init_db()
    c = _conn()
    eklenen = 0
    for d in documents:
        try:
            c.execute("""INSERT OR IGNORE INTO chunks(title,category,chunk_idx,content,added_at)
                VALUES(?,?,?,?,?)""",
                (d.get("title","?"), d.get("category","genel"),
                 d.get("chunk_idx",0), d.get("content",""),
                 d.get("added_at", datetime.now(timezone.utc).isoformat())))
            if c.total_changes:
                eklenen += c.rowcount
        except Exception:
            pass
    c.commit()
    # özet
    kitap = c.execute("SELECT COUNT(DISTINCT title) FROM chunks").fetchone()[0]
    toplam = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    c.close()
    return {"eklenen": eklenen, "toplam_kitap": kitap, "toplam_chunk": toplam}

def ara(sorgu: str, limit: int = 5) -> list:
    """FTS5 ile kitaplarda tam metin arama — RAM'e yüklemeden."""
    if not DB_PATH.exists(): return []
    init_db()
    c = _conn()
    # FTS5 sorgu temizleme (özel karakterler hata verir)
    temiz = " ".join(w for w in sorgu.replace('"',' ').split() if len(w) > 2)
    if not temiz: 
        c.close(); return []
    try:
        # OR ile birleştir (herhangi kelime eşleşsin), bm25 ile sırala
        fts_q = " OR ".join(temiz.split())
        rows = c.execute("""
            SELECT c.title, c.category, c.content, bm25(chunks_fts) AS skor
            FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid
            WHERE chunks_fts MATCH ?
            ORDER BY skor LIMIT ?""", (fts_q, limit)).fetchall()
        c.close()
        return [{"title":r[0],"category":r[1],"content":r[2],"skor":round(r[3],2)} for r in rows]
    except Exception as e:
        c.close()
        return []

def istatistik() -> dict:
    if not DB_PATH.exists():
        return {"kitap_sayisi":0,"toplam_chunk":0,"kategoriler":{},"kitaplar":[]}
    init_db()
    c = _conn()
    kitap = c.execute("SELECT COUNT(DISTINCT title) FROM chunks").fetchone()[0]
    toplam = c.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    kats = dict(c.execute("SELECT category,COUNT(DISTINCT title) FROM chunks GROUP BY category").fetchall())
    kitaplar = [{"title":r[0],"category":r[1],"chunks":r[2]} for r in
                c.execute("SELECT title,category,COUNT(*) FROM chunks GROUP BY title ORDER BY title").fetchall()]
    c.close()
    return {"kitap_sayisi":kitap,"toplam_chunk":toplam,"kategoriler":kats,"kitaplar":kitaplar}

def temizle_hepsi() -> dict:
    """Tüm kitapları sil (yeniden yükleme için)."""
    if DB_PATH.exists():
        DB_PATH.unlink()
        for ext in ["-wal","-shm"]:
            p = Path(str(DB_PATH)+ext)
            if p.exists(): p.unlink()
    init_db()
    return {"status":"temizlendi"}
