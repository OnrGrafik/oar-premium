"""
ajan_merkez.py — Merkezi Ajan Aktivite Kanalı
═══════════════════════════════════════════════════════════════════════════════
Sistemdeki TÜM agentlerin (research, backtest, öneri, hipotez, lider, OAR...)
anlık görüş / istek / backtest / çıktı / öneri / eksik durumlarını TEK Telegram
kanalına gönderir → kullanıcı her şeyi tek yerden takip eder; research boşa
düşmesin, geliştirme önerisi kaybolmasın, görülen eksik giderilmeden unutulmasın.

Kanal: https://t.me/c/2142274543/4129  → chat -1002142274543, thread 4129

Kullanım (herhangi bir agent):
    from ajan_merkez import bildir
    await bildir("Research Agent", "research", "SOL fib 0.5 hipotezi WR %58", detay="...")

Türler (emoji): görüş 🧠 · research 🔬 · backtest 📊 · çıktı 📈 · öneri 💡
                eksik ⚠️ · istek 📨 · durum ℹ️
Her mesaj ayrıca diske loglanır (ajan_aktivite.json) → lider periyodik özet çeker.
"""
import os, json
from pathlib import Path
from datetime import datetime, timezone

AJAN_CHAT   = "-1002142274543"
AJAN_THREAD = "4129"

DATA_DIR = Path(os.environ.get("DATA_DIR") or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
                or ("/var/data" if Path("/var/data").exists() else "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = DATA_DIR / "ajan_aktivite.json"

TUR_EMOJI = {
    "görüş": "🧠", "gorus": "🧠", "research": "🔬", "backtest": "📊",
    "çıktı": "📈", "cikti": "📈", "öneri": "💡", "oneri": "💡",
    "eksik": "⚠️", "istek": "📨", "durum": "ℹ️",
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _log_yukle() -> list:
    try:
        if LOG_FILE.exists():
            return json.loads(LOG_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def _log_kaydet(kayit: dict):
    log = _log_yukle()
    log.append(kayit)
    log = log[-500:]     # son 500 aktivite
    try:
        LOG_FILE.write_text(json.dumps(log, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


DEDUP_FILE = DATA_DIR / "ajan_dedup.json"
DEDUP_SAAT = 24          # aynı (ajan+özet) mesajı 24 saat içinde TEKRAR gitmez
DEDUP_SAAT_EKSIK = 6     # arıza bildirimi 6 saatte bir tekrar edebilir


def _dedup_gecti(ajan: str, tur: str, ozet: str) -> bool:
    """True = bu mesaj yakın zamanda zaten gönderildi → ATLAMA (restart/redeploy spamı önlenir)."""
    import hashlib, time
    h = hashlib.sha1(f"{ajan}|{ozet}".encode("utf-8")).hexdigest()[:16]
    try:
        d = json.loads(DEDUP_FILE.read_text(encoding="utf-8")) if DEDUP_FILE.exists() else {}
    except Exception:
        d = {}
    simdi = time.time()
    ttl = (DEDUP_SAAT_EKSIK if tur.lower() == "eksik" else DEDUP_SAAT) * 3600
    if simdi - d.get(h, 0) < ttl:
        return True
    d[h] = simdi
    d = {k: v for k, v in d.items() if simdi - v < 72 * 3600}   # 3 günden eskiyi temizle
    try:
        DEDUP_FILE.write_text(json.dumps(d), encoding="utf-8")
    except Exception:
        pass
    return False


async def bildir(ajan: str, tur: str, ozet: str, detay: str = "") -> bool:
    """
    Bir agent aktivitesini merkezi kanala gönder + diske logla.
    ajan: agent adı · tur: görüş/research/backtest/çıktı/öneri/eksik/istek/durum
    ozet: tek satır · detay: opsiyonel açıklama.
    Aynı (ajan+özet) 24 saat içinde İKİNCİ kez Telegram'a GİTMEZ (kalıcı disk dedup —
    redeploy/restart döngüsünde aynı mesajın tekrar tekrar atılmasını keser).
    """
    emoji = TUR_EMOJI.get(tur.lower(), "ℹ️")
    kayit = {"tarih": _now(), "ajan": ajan, "tur": tur, "ozet": ozet, "detay": detay[:500]}
    _log_kaydet(kayit)
    if _dedup_gecti(ajan, tur, ozet):
        return False
    satirlar = [f"{emoji} *{ajan}* — {tur.upper()}", ozet]
    if detay:
        satirlar.append(f"_{detay[:600]}_")
    metin = "\n".join(satirlar)
    try:
        from main import _telegram_gonder
        return await _telegram_gonder(metin, thread_id=AJAN_THREAD, chat_id=AJAN_CHAT)
    except Exception:
        return False


def son_aktiviteler(n: int = 20, tur: str = None) -> list:
    """Lider periyodik özet için son N aktiviteyi (ops. türe göre) döndür."""
    log = _log_yukle()
    if tur:
        log = [x for x in log if x.get("tur") == tur]
    return log[-n:]


def bekleyen_ozet() -> str:
    """
    Son aktivitelerden lider-özeti metni: öneri/eksik/research vurgulanır
    (boşa düşmesin diye). Kullanıcının canlı takip ettiği digest.
    """
    log = _log_yukle()
    if not log:
        return ""
    son = log[-30:]
    onemli = [x for x in son if x.get("tur") in ("öneri", "oneri", "eksik", "research", "backtest")]
    if not onemli:
        return ""
    satirlar = ["🧭 *LİDER — Ajan Aktivite Özeti*"]
    for x in onemli[-10:]:
        e = TUR_EMOJI.get(x.get("tur", "").lower(), "ℹ️")
        satirlar.append(f"{e} {x.get('ajan','?')}: {x.get('ozet','')[:120]}")
    return "\n".join(satirlar)
