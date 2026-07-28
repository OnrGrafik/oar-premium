"""
auth.py — Üyelik + oturum sistemi (kullanıcı kararı: başvuru+admin onayı,
e-posta+şifre, bcrypt, admin-manuel + Telegram bildirim).

- Depo: SQLite `DATA_DIR/users.db` (Railway kalıcı volume — git'e GİRMEZ, şifre sızmaz).
- Şifre: bcrypt (yoksa stdlib pbkdf2 fallback — sandbox/bcrypt-siz ortam için).
- Oturum: server-side rastgele token → HttpOnly+Secure+SameSite=Strict çerez, DB'de saklanır (iptal edilebilir).
- İlk admin: ADMIN_EMAIL + ADMIN_PASS env'den ilk açılışta kurulur.

6c: bu modül ziyaretçiye altyapı adı sızdırmaz; yalnız API + Telegram akışında.
"""
import os, sqlite3, secrets, time, hmac, hashlib, re
from pathlib import Path
from datetime import datetime, timezone

try:
    import bcrypt
    _HAS_BCRYPT = True
except Exception:
    _HAS_BCRYPT = False

DATA_DIR = Path(os.environ.get("DATA_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
                or ("/var/data" if Path("/var/data").exists() else "data"))
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
DB_PATH = DATA_DIR / "users.db"

COOKIE_NAME = "oar_session"
SESSION_TTL = 30 * 86400          # 30 gün
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _conn():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    return c


# ── Şema + ilk admin ─────────────────────────────────────────────
def init_db():
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            kullanici_adi TEXT,
            ad_soyad TEXT,
            sifre_hash TEXT NOT NULL,
            rol TEXT NOT NULL DEFAULT 'uye',
            durum TEXT NOT NULL DEFAULT 'beklemede',
            olusturma_t TEXT, onay_t TEXT, son_giris_t TEXT, onaylayan TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS sessions(
            token TEXT PRIMARY KEY, user_id INTEGER, olusturma_t TEXT, bitis REAL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS audit_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT, t TEXT, aktor TEXT,
            eylem TEXT, hedef TEXT, detay TEXT)""")
    _bootstrap_admin()

def _bootstrap_admin():
    email = (os.environ.get("ADMIN_EMAIL", "") or "").lower().strip()
    sifre = os.environ.get("ADMIN_PASS", "") or ""
    if not email or not sifre:
        return
    u = kullanici_by_email(email)
    if not u:
        try:
            _insert(email, sifre, ad_soyad="Yönetici", rol="admin", durum="aktif", onaylayan="bootstrap")
        except Exception:
            pass
    elif u.get("rol") != "admin" or u.get("durum") != "aktif":
        with _conn() as c:
            c.execute("UPDATE users SET rol='admin', durum='aktif' WHERE email=?", (email,))


# ── Şifre hash ───────────────────────────────────────────────────
def hash_sifre(p: str) -> str:
    if _HAS_BCRYPT:
        return "bcrypt$" + bcrypt.hashpw(p.encode(), bcrypt.gensalt()).decode()
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", p.encode(), salt.encode(), 200_000)
    return "pbkdf2$" + salt + "$" + dk.hex()

def dogrula_sifre(p: str, h: str) -> bool:
    try:
        if h.startswith("bcrypt$"):
            if not _HAS_BCRYPT:
                return False
            return bcrypt.checkpw(p.encode(), h[7:].encode())
        if h.startswith("pbkdf2$"):
            _, salt, hexd = h.split("$", 2)
            dk = hashlib.pbkdf2_hmac("sha256", p.encode(), salt.encode(), 200_000)
            return hmac.compare_digest(dk.hex(), hexd)
    except Exception:
        return False
    return False


# ── Kullanıcı CRUD ───────────────────────────────────────────────
def _row(r):
    if not r:
        return None
    d = dict(r)
    d.pop("sifre_hash", None)          # hash ASLA dışarı çıkmaz
    return d

def kullanici_by_email(email: str):
    with _conn() as c:
        r = c.execute("SELECT * FROM users WHERE email=?", (email.lower().strip(),)).fetchone()
        return dict(r) if r else None   # iç kullanım (hash dahil)

def kullanici_by_id(uid: int):
    with _conn() as c:
        r = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        return _row(r)

def _insert(email, sifre, ad_soyad="", kullanici_adi="", rol="uye", durum="beklemede", onaylayan=None) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO users(email,kullanici_adi,ad_soyad,sifre_hash,rol,durum,olusturma_t,onay_t,onaylayan)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (email, kullanici_adi, ad_soyad, hash_sifre(sifre), rol, durum,
             _now(), _now() if durum == "aktif" else None, onaylayan))
        return cur.lastrowid

def kayit(email: str, sifre: str, ad_soyad: str = "", kullanici_adi: str = "",
          rol: str = "uye", durum: str = "beklemede", onaylayan=None) -> dict:
    email = (email or "").lower().strip()
    if not _EMAIL_RE.match(email):
        raise ValueError("Geçerli bir e-posta girin.")
    if not sifre or len(sifre) < 6:
        raise ValueError("Şifre en az 6 karakter olmalı.")
    if kullanici_by_email(email):
        raise ValueError("Bu e-posta zaten kayıtlı.")
    uid = _insert(email, sifre, ad_soyad.strip(), (kullanici_adi or "").strip(), rol, durum, onaylayan)
    return kullanici_by_id(uid)


# ── Oturum ───────────────────────────────────────────────────────
def olustur_oturum(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with _conn() as c:
        c.execute("INSERT INTO sessions(token,user_id,olusturma_t,bitis) VALUES(?,?,?,?)",
                  (token, user_id, _now(), time.time() + SESSION_TTL))
    return token

def oturum_kullanici(token: str):
    if not token:
        return None
    with _conn() as c:
        r = c.execute("SELECT * FROM sessions WHERE token=?", (token,)).fetchone()
        if not r:
            return None
        if r["bitis"] and r["bitis"] < time.time():
            c.execute("DELETE FROM sessions WHERE token=?", (token,))
            return None
        u = c.execute("SELECT * FROM users WHERE id=?", (r["user_id"],)).fetchone()
        return _row(u)

def oturum_sil(token: str):
    with _conn() as c:
        c.execute("DELETE FROM sessions WHERE token=?", (token,))

def oturum_sil_kullanici(uid: int):
    with _conn() as c:
        c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))


# ── Giriş (rate-limit + genel hata) ──────────────────────────────
_DENEME = {}   # {ip: [zaman...]} — basit login rate-limit
def _rate_ok(ip: str, limit: int = 6, pencere: int = 60) -> bool:
    now = time.time()
    q = [t for t in _DENEME.get(ip, []) if now - t < pencere]
    q.append(now)
    _DENEME[ip] = q
    return len(q) <= limit

def giris(email: str, sifre: str, ip: str = "") -> dict:
    if ip and not _rate_ok(ip):
        raise ValueError("Çok fazla deneme. 1 dakika sonra tekrar deneyin.")
    u = kullanici_by_email(email or "")
    if not u or not dogrula_sifre(sifre or "", u.get("sifre_hash", "")):
        raise ValueError("E-posta veya şifre hatalı.")
    if u["durum"] == "beklemede":
        raise ValueError("Hesabınız onay bekliyor. Yönetici onayından sonra giriş yapabilirsiniz.")
    if u["durum"] in ("askida", "reddedildi"):
        raise ValueError("Hesabınız aktif değil. Yönetici ile iletişime geçin.")
    with _conn() as c:
        c.execute("UPDATE users SET son_giris_t=? WHERE id=?", (_now(), u["id"]))
    token = olustur_oturum(u["id"])
    return {"token": token, "user": kullanici_by_id(u["id"])}


# ── Profil (üye self-edit) ───────────────────────────────────────
def profil_guncelle(uid: int, ad_soyad=None, kullanici_adi=None, email=None) -> dict:
    with _conn() as c:
        if email is not None:
            email = email.lower().strip()
            if not _EMAIL_RE.match(email):
                raise ValueError("Geçerli bir e-posta girin.")
            var = c.execute("SELECT id FROM users WHERE email=? AND id<>?", (email, uid)).fetchone()
            if var:
                raise ValueError("Bu e-posta başka bir hesapta kullanılıyor.")
            c.execute("UPDATE users SET email=? WHERE id=?", (email, uid))
        if ad_soyad is not None:
            c.execute("UPDATE users SET ad_soyad=? WHERE id=?", (ad_soyad.strip(), uid))
        if kullanici_adi is not None:
            c.execute("UPDATE users SET kullanici_adi=? WHERE id=?", (kullanici_adi.strip(), uid))
    return kullanici_by_id(uid)

def sifre_degistir(uid: int, eski: str, yeni: str):
    with _conn() as c:
        r = c.execute("SELECT sifre_hash FROM users WHERE id=?", (uid,)).fetchone()
    if not r or not dogrula_sifre(eski or "", r["sifre_hash"]):
        raise ValueError("Mevcut şifre hatalı.")
    if not yeni or len(yeni) < 6:
        raise ValueError("Yeni şifre en az 6 karakter olmalı.")
    with _conn() as c:
        c.execute("UPDATE users SET sifre_hash=? WHERE id=?", (hash_sifre(yeni), uid))
    oturum_sil_kullanici(uid)          # tüm oturumları düşür (yeniden giriş)


# ── Admin işlemleri ──────────────────────────────────────────────
def liste(durum: str = "", q: str = "") -> list:
    sql, args = "SELECT * FROM users", []
    kos = []
    if durum:
        kos.append("durum=?"); args.append(durum)
    if q:
        kos.append("(email LIKE ? OR ad_soyad LIKE ? OR kullanici_adi LIKE ?)")
        args += [f"%{q}%"] * 3
    if kos:
        sql += " WHERE " + " AND ".join(kos)
    sql += " ORDER BY CASE durum WHEN 'beklemede' THEN 0 ELSE 1 END, id DESC"
    with _conn() as c:
        return [_row(r) for r in c.execute(sql, args).fetchall()]

def bekleyenler() -> list:
    return liste(durum="beklemede")

def durum_ata(uid: int, durum: str, aktor: str = "") -> dict:
    if durum not in ("aktif", "askida", "reddedildi", "beklemede"):
        raise ValueError("Geçersiz durum.")
    with _conn() as c:
        c.execute("UPDATE users SET durum=?, onay_t=CASE WHEN ?='aktif' THEN ? ELSE onay_t END, onaylayan=? WHERE id=?",
                  (durum, durum, _now(), aktor, uid))
    if durum in ("askida", "reddedildi"):
        oturum_sil_kullanici(uid)
    denetim(aktor, f"durum:{durum}", str(uid))
    return kullanici_by_id(uid)

def rol_ata(uid: int, rol: str, aktor: str = "") -> dict:
    if rol not in ("admin", "uye"):
        raise ValueError("Geçersiz rol.")
    with _conn() as c:
        c.execute("UPDATE users SET rol=? WHERE id=?", (rol, uid))
    denetim(aktor, f"rol:{rol}", str(uid))
    return kullanici_by_id(uid)

def sil(uid: int, aktor: str = ""):
    oturum_sil_kullanici(uid)
    with _conn() as c:
        c.execute("DELETE FROM users WHERE id=?", (uid,))
    denetim(aktor, "sil", str(uid))

def sifre_sifirla(uid: int, aktor: str = "") -> str:
    """Admin tetikli: geçici şifre üretir, döndürür (admin iletir), oturumları düşürür."""
    gecici = secrets.token_urlsafe(6)
    with _conn() as c:
        c.execute("UPDATE users SET sifre_hash=? WHERE id=?", (hash_sifre(gecici), uid))
    oturum_sil_kullanici(uid)
    denetim(aktor, "sifre_sifirla", str(uid))
    return gecici

def admin_olustur_uye(email, sifre, ad_soyad="", rol="uye", aktor="") -> dict:
    u = kayit(email, sifre, ad_soyad=ad_soyad, rol=rol, durum="aktif", onaylayan=aktor or "admin")
    denetim(aktor, "olustur", str(u["id"]), email)
    return u

def denetim(aktor: str, eylem: str, hedef: str = "", detay: str = ""):
    try:
        with _conn() as c:
            c.execute("INSERT INTO audit_log(t,aktor,eylem,hedef,detay) VALUES(?,?,?,?,?)",
                      (_now(), aktor, eylem, hedef, detay))
    except Exception:
        pass

def audit_liste(limit: int = 100) -> list:
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

def istatistik() -> dict:
    with _conn() as c:
        rows = c.execute("SELECT durum, COUNT(*) n FROM users GROUP BY durum").fetchall()
    return {r["durum"]: r["n"] for r in rows}
