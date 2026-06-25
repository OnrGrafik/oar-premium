"""
Öneri Motoru — OAR Premium  (Research/Lider önerisi → Telegram onayı → config)
══════════════════════════════════════════════════════════════════════════════
Akış:
  Research/Lider AI yorumu  →  yapılandırılmış öneri (parametre yaması)
  →  Telegram'a "✅ Uygula / ❌ Reddet" butonlu mesaj
  →  kullanıcı onayı  →  config_overrides.json'a yazılır (KOD DEĞİŞMEZ)
  →  risk_skoru vb. modüller bu override'ları okuyup davranışını günceller.

Güvenlik:
  • Otomatik KOD YAZMA yok; yalnızca BİLİNEN parametreler ayarlanır.
  • Her değişiklik kullanıcı onayına tabidir (insan-onaylı).
  • Tanınmayan öneriler "BİLGİ" olarak kaydedilir, otomatik uygulanmaz.
  • Tüm değerler güvenli aralıklara KISILIR (clamp).
"""

from __future__ import annotations
import os
import json
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR        = Path(os.environ.get("DATA_DIR", "data"))
ONERI_FILE      = DATA_DIR / "oneriler.json"
CONFIG_FILE     = DATA_DIR / "config_overrides.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _save(path: Path, data):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# ── Config overrides (diğer modüller bunu okur) ──────────────────────────────
def config_oku() -> dict:
    """Aktif config override'larını döndürür. Modüller varsayılanlarını bununla ezer."""
    return _load(CONFIG_FILE, {})


def _config_uygula(degisiklikler: dict):
    """degisiklikler: {"nokta.ayrac.anahtar": deger} → iç içe dict'e yazar."""
    cfg = config_oku()
    for yol, deger in degisiklikler.items():
        parcalar = yol.split(".")
        d = cfg
        for p in parcalar[:-1]:
            d = d.setdefault(p, {})
        d[parcalar[-1]] = deger
    cfg["_guncellendi"] = _now()
    _save(CONFIG_FILE, cfg)


# ── Öneri → parametre çıkarımı (kural tabanlı, güvenli) ───────────────────────
def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


# (regex, açıklama, üretici(fonk) -> {param: deger})  — eşleşen kurallar birleşir
_KURALLAR = [
    (r"makro.*(gün|saat|açıklama|veri).*(risk|iştah).*(düş|azalt|kıs)",
     "Makro açıklama pencerelerinde risk iştahını düşür",
     lambda m: {"risk_skoru.makro_olay_risk_carpani": 0.4}),

    (r"(ters|mean.?reversion|range).*(askıya|durdur|kapat|suspend)",
     "Makro penceresinde ters/range hipotezlerini askıya al",
     lambda m: {"risk_skoru.pencere_ters_askiya": True}),

    (r"(ters|short).*(ağırlı|weight).*(art)",
     "Risk-off'ta ters/hipotez ağırlığını artır",
     lambda m: {"risk_skoru.agirlik.hipotez": 0.30, "risk_skoru.agirlik.rejim": 0.25}),

    (r"(trend|long).*(ağırlı|weight).*(art)",
     "Risk-on'da trend/rejim ağırlığını artır",
     lambda m: {"risk_skoru.agirlik.rejim": 0.35, "risk_skoru.agirlik.hipotez": 0.15}),

    (r"gamma.*(ağırlı|weight).*(art)",
     "Opsiyon/gamma ağırlığını artır",
     lambda m: {"risk_skoru.agirlik.gamma": 0.30}),

    (r"skor\s*<\s*-?\s*(\d{2,3})",
     "Güçlü ters eşiğini ayarla (skor<-N)",
     lambda m: {"risk_skoru.guclu_ters_esik": -_clamp(int(m.group(1)), 20, 80)}),

    (r"skor\s*>\s*\+?\s*(\d{2,3})",
     "Güçlü trend eşiğini ayarla (skor>N)",
     lambda m: {"risk_skoru.guclu_trend_esik": _clamp(int(m.group(1)), 20, 80)}),
]


def oneri_coz(metin: str) -> tuple[dict, list[str]]:
    """
    Serbest metinden güvenli parametre değişikliklerini çıkarır.
    Döner: (degisiklikler, eslesen_aciklamalar)
    Hiç eşleşme yoksa boş dict → öneri "BİLGİ" olarak kaydedilir.
    """
    t = (metin or "").lower()
    degis: dict = {}
    aciklama: list[str] = []
    for desen, acik, uretici in _KURALLAR:
        m = re.search(desen, t)
        if m:
            try:
                degis.update(uretici(m))
                aciklama.append(acik)
            except Exception:
                pass
    return degis, aciklama


# ── Öneri kayıtları ──────────────────────────────────────────────────────────
def _id(kaynak: str, metin: str) -> str:
    return hashlib.sha1(f"{kaynak}|{metin}".encode("utf-8")).hexdigest()[:10]


def oneri_olustur(kaynak: str, metin: str) -> dict | None:
    """
    Yeni öneri kaydı oluşturur (yeni/benzersizse). Döner: öneri | None (mükerrer).
    kaynak: "research" | "lider" | "manuel"
    """
    metin = (metin or "").strip()
    if len(metin) < 12:
        return None
    oid = _id(kaynak, metin[:200])
    kayit = _load(ONERI_FILE, {"oneriler": []})
    if any(o["id"] == oid for o in kayit["oneriler"]):
        return None  # mükerrer

    degis, acik = oneri_coz(metin)
    oneri = {
        "id": oid,
        "kaynak": kaynak,
        "metin": metin[:600],
        "degisiklikler": degis,
        "ozet": acik,
        "tip": "PARAMETRE" if degis else "BILGI",
        "durum": "BEKLIYOR",
        "olusturuldu": _now(),
    }
    kayit["oneriler"].insert(0, oneri)
    kayit["oneriler"] = kayit["oneriler"][:100]
    _save(ONERI_FILE, kayit)
    return oneri


def bekleyenler() -> list[dict]:
    kayit = _load(ONERI_FILE, {"oneriler": []})
    return [o for o in kayit["oneriler"] if o["durum"] == "BEKLIYOR"]


def oneri_getir(oid: str) -> dict | None:
    kayit = _load(ONERI_FILE, {"oneriler": []})
    return next((o for o in kayit["oneriler"] if o["id"] == oid), None)


def _durum_yaz(oid: str, durum: str) -> dict | None:
    kayit = _load(ONERI_FILE, {"oneriler": []})
    for o in kayit["oneriler"]:
        if o["id"] == oid:
            o["durum"] = durum
            o["karar_zamani"] = _now()
            _save(ONERI_FILE, kayit)
            return o
    return None


def onayla(oid: str) -> dict | None:
    """Öneriyi onaylar; PARAMETRE tipindeyse config_overrides'a uygular."""
    o = oneri_getir(oid)
    if not o or o["durum"] != "BEKLIYOR":
        return o
    if o["tip"] == "PARAMETRE" and o["degisiklikler"]:
        _config_uygula(o["degisiklikler"])
    return _durum_yaz(oid, "ONAYLANDI")


def reddet(oid: str) -> dict | None:
    return _durum_yaz(oid, "REDDEDILDI")


# ── Telegram ─────────────────────────────────────────────────────────────────
def _liste_metni(o: dict) -> str:
    satir = [f"💡 <b>Sistem Geliştirme Önerisi</b> ({o['kaynak']})",
             "", o["metin"][:500]]
    if o["tip"] == "PARAMETRE":
        satir.append("")
        satir.append("🔧 <b>Uygulanacak ayarlar:</b>")
        for k, v in o["degisiklikler"].items():
            satir.append(f"  • <code>{k}</code> → <b>{v}</b>")
        satir.append("\nOnaylarsan ayarlar config_overrides'a yazılır (kod değişmez).")
    else:
        satir.append("\nℹ️ Otomatik parametre çıkarılamadı — bilgi amaçlı. "
                     "Onay yalnızca 'görüldü' olarak işaretler.")
    return "\n".join(satir)


def _klavye(oid: str) -> dict:
    return {"inline_keyboard": [[
        {"text": "✅ Uygula",  "callback_data": f"oneri:onay:{oid}"},
        {"text": "❌ Reddet",  "callback_data": f"oneri:red:{oid}"},
    ]]}


async def oneri_gonder_telegram(o: dict) -> bool:
    """Öneriyi inline butonlarla Telegram'a gönderir."""
    import httpx
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat  = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return False
    payload = {"chat_id": chat, "text": _liste_metni(o), "parse_mode": "HTML",
               "disable_web_page_preview": True,
               "reply_markup": json.dumps(_klavye(o["id"]))}
    tid = os.environ.get("TELEGRAM_THREAD_ID", "")
    if tid:
        try: payload["message_thread_id"] = int(tid)
        except Exception: pass
    try:
        async with httpx.AsyncClient(timeout=15) as cl:
            r = await cl.post(f"https://api.telegram.org/bot{token}/sendMessage",
                              json=payload)
            return r.status_code == 200
    except Exception:
        return False


async def oneri_tara_ve_gonder(metin: str, kaynak: str) -> dict | None:
    """
    Research/Lider yorumundan ÖNERİ cümlelerini ayıklayıp öneri üretir ve
    (yeni ise) Telegram'a butonlu olarak gönderir.
    """
    if not metin:
        return None
    # "öneri/önerilir" geçen cümleleri tercih et; yoksa tüm metni kullan
    cumleler = re.split(r"(?<=[.!?])\s+", metin)
    oneri_cumle = " ".join(c for c in cumleler if re.search(r"öner|tavsiye|eklen|ayarla", c.lower()))
    hedef = oneri_cumle.strip() or metin.strip()
    o = oneri_olustur(kaynak, hedef)
    if o:
        await oneri_gonder_telegram(o)
    return o


async def callback_isle(callback_data: str) -> str:
    """
    Telegram callback_query handler. callback_data: "oneri:onay:<id>" | "oneri:red:<id>"
    Döner: kullanıcıya gösterilecek kısa yanıt.
    """
    try:
        _, aksiyon, oid = callback_data.split(":", 2)
    except ValueError:
        return "Geçersiz işlem."
    if aksiyon == "onay":
        o = onayla(oid)
        if not o:
            return "Öneri bulunamadı."
        if o["durum"] != "ONAYLANDI":
            return "Öneri zaten işlenmiş."
        if o["tip"] == "PARAMETRE":
            return f"✅ Uygulandı: {', '.join(o['degisiklikler'].keys())}"
        return "✅ Görüldü olarak işaretlendi."
    if aksiyon == "red":
        o = reddet(oid)
        return "❌ Reddedildi." if o else "Öneri bulunamadı."
    return "Bilinmeyen işlem."


if __name__ == "__main__":
    import asyncio
    o = oneri_olustur("manuel",
        "OAR'a makro verilerin açıklanacağı gün ve saatlerde risk iştahını "
        "otomatik düşüren bir modül eklenmesi ve skor<-40 koşulunda ters "
        "stratejilerin ağırlığının artırılması önerilir.")
    print(json.dumps(o, ensure_ascii=False, indent=2))
    print("Onay:", asyncio.run(callback_isle(f"oneri:onay:{o['id']}")))
    print("Config:", json.dumps(config_oku(), ensure_ascii=False))
