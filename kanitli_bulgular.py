"""
kanitli_bulgular.py — OAR Kanıtlanmış Bulgu Deposu (lider ↔ hipotez motoru köprüsü)
═══════════════════════════════════════════════════════════════════════════════════════
AMAÇ: Hipotez motorunun (yerel derin-veri) ürettiği KANITLI kazananları (LIFT+OOS+Wilson)
kalıcı, PAYLAŞILAN bir depoda tutmak → Lider Agent (Railway) bunları OKUR:
  (a) OAR bağlamına katar (bana bildirirken kanıtlı bulguları göz önüne alır),
  (b) YENİ bir kanıtlı bulgu geldiğinde Telegram'a haber verir.

Depo REPO KÖKÜNDE bir JSON (DATA_DIR değil) → git ile senkronlanır: motor yerelde yazar,
kullanıcı commit/push eder, Railway lider deploy'da görür. Kanıt-temelli sistemin
"öğrenilenler" hafızası budur — LLM lafı değil, SAYI (LIFT/OOS/Wilson/PF).

Bir bulgu = {ad, wr, taban, lift, wilson_alt, oos_wr, n, kaynak, tarih, bildirildi}.
"bildirildi" bayrağı → lider aynı bulguyu iki kez Telegram'a atmaz.
"""
import json
from pathlib import Path
from datetime import datetime, timezone

KANIT_FILE = Path(__file__).resolve().parent / "kanitli_bulgular.json"
# Bildirilen adlar KALICI diskte de tutulur: repo dosyası her deploy'da git'ten
# (bildirildi bayrağı olmadan) gelir → yalnız repo bayrağına güvenmek her restart'ta
# aynı bulguların Telegram'a TEKRAR atılmasına yol açıyordu.
import os as _os
_DATA_DIR = Path(_os.environ.get("DATA_DIR") or _os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
                 or ("/var/data" if Path("/var/data").exists() else "data"))
_BILDIRILEN_FILE = _DATA_DIR / "kanitli_bildirilen.json"


def _bildirilen_yukle() -> set:
    try:
        return set(json.loads(_BILDIRILEN_FILE.read_text(encoding="utf-8")))
    except Exception:
        return set()


def _bildirilen_kaydet(adlar: set):
    try:
        _BILDIRILEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        _BILDIRILEN_FILE.write_text(json.dumps(sorted(adlar), ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def _yukle() -> list:
    try:
        return json.loads(KANIT_FILE.read_text(encoding="utf-8")) if KANIT_FILE.exists() else []
    except Exception:
        return []


def _kaydet(bulgular: list):
    try:
        KANIT_FILE.write_text(json.dumps(bulgular, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def bulgu_ekle(ad: str, wr: float, taban: float, lift: float,
               wilson_alt: float, oos_wr, n: int, kaynak: str = "hipotez_motoru") -> bool:
    """
    Kanıtlı bulgu ekle/güncelle. Aynı ad varsa yalnız DAHA İYİ LIFT ile günceller.
    Döner: True = YENİ bulgu (ilk kez), False = mevcut/güncelleme.
    """
    bulgular = _yukle()
    mevcut = next((b for b in bulgular if b["ad"] == ad), None)
    if mevcut:
        if lift > mevcut.get("lift", -99):
            mevcut.update({"wr": wr, "taban": taban, "lift": lift,
                           "wilson_alt": wilson_alt, "oos_wr": oos_wr, "n": n,
                           "guncelleme": _now()})
            _kaydet(bulgular)
        return False
    bulgular.append({
        "ad": ad, "wr": wr, "taban": taban, "lift": lift, "wilson_alt": wilson_alt,
        "oos_wr": oos_wr, "n": n, "kaynak": kaynak, "tarih": _now(), "bildirildi": False,
    })
    # LIFT'e göre sırala, en iyi 100'ü tut
    bulgular.sort(key=lambda b: b.get("lift", 0), reverse=True)
    _kaydet(bulgular[:100])
    return True


def bulgular_al(n: int = 100) -> list:
    return _yukle()[:n]


def bildirilmemis() -> list:
    """Lider'in henüz Telegram'a atmadığı kanıtlı bulgular (kalıcı disk + repo bayrağı birlikte)."""
    eski = _bildirilen_yukle()
    return [b for b in _yukle() if not b.get("bildirildi") and b["ad"] not in eski]


def bildirildi_isaretle(adlar: list):
    bulgular = _yukle()
    ad_set = set(adlar)
    for b in bulgular:
        if b["ad"] in ad_set:
            b["bildirildi"] = True
    _kaydet(bulgular)
    _bildirilen_kaydet(_bildirilen_yukle() | ad_set)


def ozet_metni(n: int = 8) -> str:
    """Lider OAR bağlamı için kanıtlı bulgu özeti (LLM promptuna eklenir)."""
    bulgular = _yukle()
    if not bulgular:
        return "Kanıtlı bulgu deposu boş (hipotez motoru henüz kazanan üretmedi)."
    satir = ["OAR KANITLI BULGULAR (LIFT+OOS+Wilson ile doğrulanmış — karar/bildirimde göz önüne al):"]
    for b in bulgular[:n]:
        satir.append(f"  • {b['ad']}: WR%{b['wr']} vs taban%{b['taban']} "
                     f"LIFT {b['lift']:+} (n{b['n']}, OOS%{b.get('oos_wr')}, Wilson≥%{b.get('wilson_alt')})")
    return "\n".join(satir)
